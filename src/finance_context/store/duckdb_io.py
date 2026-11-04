"""DuckDB reads and writes of one local file or one MinIO object."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa

from finance_context.observability import log_event
from finance_context.settings import Settings, _duckdb_size_bytes
from finance_context.store.paths import shared_book_dir

_LOGGER = logging.getLogger("finance_context.store")
_BATCH_ROWS = 2048
# Not configurable: parquet row-group bytes must stay byte-identical, and memory
# grows per thread while memory_limit does not cover every allocation.
_DUCKDB_THREADS = 1
_DUCK_TYPES = {"VARCHAR": "VARCHAR", "INTEGER": "BIGINT", "BOOLEAN": "BOOLEAN"}

_SHARED = (
    "source/source.xlsx",
    "raw/cells.parquet",
    "raw/cell_presence.parquet",
    "raw/workbook.json",
    "ir/cells.parquet",
    "ir/edges.parquet",
    "ir/cell_edges.parquet",
    "ir/compile.json",
    "layout/layout.json",
)
_SESSION = (
    "graph/index.parquet",
    "graph/edges.parquet",
    "context/context.json",
    "context/context.md",
    "graph/graph.json",
    "graph/graph.md",
    "meta/meta.json",
)
_EXPORT_WARNING = "minio export failed"
_OBJECT_SIZE_SQL = "SELECT size FROM read_blob(?)"
_HTTP_STATUS = re.compile(r"\(HTTP (\d{3})\b")
_S3_LINE = re.compile(r"(?m)^([A-Z][A-Za-z0-9]+): ([^\r\n]{1,160})")
_publish_held = threading.local()
# One connection may spill up to 2 GiB. The free-space raise is split across
# max_concurrent_jobs first, so two builds cannot each take a 4 GiB volume.
# A configured floor is never lowered. A 16 GiB temp would fill a memory-backed pod.
_SPILL_CEILING_BYTES = 2 << 30
# Kept free before the cap is raised, so the output file still has room.
_SPILL_RESERVE_BYTES = 1 << 30


class ObjectStore(Protocol):
    def put(self, local: Path, url: str) -> None: ...

    def size(self, url: str) -> int: ...


def object_key(prefix: str, job_id: str, relative: str) -> str:
    """Bucket key. ``job_id`` is one path segment. The session id is not in it."""
    tail = relative.strip().lstrip("/")
    head = f"books/{job_id}/{tail}"
    cleaned = prefix.strip().strip("/")
    if cleaned:
        return f"{cleaned}/{head}"
    return head


def object_url(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


def copy_arrow_parquet(table: pa.Table, dest: Path) -> None:
    """Write one Snappy parquet file. DuckDB flushes vectors of 2048 rows."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    groups = max(table.num_rows, 1)
    with limited_connection() as con:
        con.register("batch", table)
        con.execute(
            "COPY (SELECT * FROM batch) TO ? "
            f"(FORMAT parquet, COMPRESSION snappy, ROW_GROUP_SIZE {groups}, "
            "USE_TMP_FILE false)",
            [str(dest)],
        )


class DuckDBBatch:
    """One DuckDB file, one table, flushed in chunks of 2048 rows.

    Rows are appended, then ``finish`` copies them to parquet. ``abort`` deletes
    the build files and leaves a parquet that was not replaced.
    """

    def __init__(self, db_path: Path, columns: Sequence[tuple[str, str]]) -> None:
        self._db = Path(db_path)
        self._partial: Path | None = None
        self._con: Any = None
        self._closed = False
        self._delete_build_files()
        self._db.parent.mkdir(parents=True, exist_ok=True)
        import duckdb

        self._con = duckdb.connect(str(self._db))
        temp = Path(str(self._db) + ".tmp")
        _set_duckdb_limits(self._con, str(temp), anchor=temp)
        specs = ", ".join(f"{_quote_ident(name)} {_DUCK_TYPES[dtype]}" for name, dtype in columns)
        self._con.execute(f"CREATE TABLE t ({specs})")

    def add_rows(self, rows: Sequence[Sequence[Any]]) -> None:
        """Append ``rows`` in chunks. An empty sequence does nothing."""
        if not rows:
            return
        con = self._require_open()
        for start in range(0, len(rows), _BATCH_ROWS):
            chunk = list(rows[start : start + _BATCH_ROWS])
            with con.appender("t", action="insert") as appender:
                appender.append_rows(chunk)

    def set_formulas(
        self, sheet: str, addrs: Sequence[str], formulas: Sequence[str | None]
    ) -> None:
        """Write one formula per address. Lengths must match. Empty is a no-op."""
        if len(addrs) != len(formulas):
            raise ValueError("addrs and formulas differ in length")
        if not addrs:
            return
        self._require_open().execute(
            """
            UPDATE t SET formula_raw = u.formula
            FROM (
                SELECT unnest(?::VARCHAR[]) AS addr, unnest(?::VARCHAR[]) AS formula
            ) AS u
            WHERE t.sheet = ? AND t.addr = u.addr
            """,
            [list(addrs), list(formulas), sheet],
        )

    @property
    def n_rows(self) -> int:
        row = self._require_open().execute("SELECT count(*) FROM t").fetchone()
        return 0 if row is None else int(row[0])

    def finish(self, parquet_path: Path, row_group_rows: int) -> None:
        """Copy the table to ``parquet_path`` and delete the build database."""
        if self._closed or self._con is None:
            raise RuntimeError("batch is closed")
        parquet_path = Path(parquet_path)
        count = self.n_rows
        if row_group_rows <= 0 or count == 0:
            groups = count
        else:
            groups = max(row_group_rows, _BATCH_ROWS)
        if groups <= 0:
            groups = _BATCH_ROWS
        partial = parquet_path.with_name(parquet_path.name + ".partial")
        self._partial = partial
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            # The row chunks that filled this table are gone. Return that heap
            # before DuckDB allocates its own 256MB, or the two stack.
            from finance_context.memory import trim_process_heap

            trim_process_heap()
            self._con.execute(
                "COPY (SELECT * FROM t) TO ? ("
                "FORMAT parquet, COMPRESSION snappy, "
                f"ROW_GROUP_SIZE {int(groups)}, "
                "PRESERVE_ORDER true, USE_TMP_FILE false)",
                [str(partial)],
            )
            self._close_connection()
            from finance_context.store.fs import atomic_replace

            atomic_replace(partial, parquet_path)
            self._partial = None
            self._closed = True
            self._delete_build_files()
        except Exception:
            self.abort()
            raise

    def abort(self) -> None:
        """Drop the database, its sidecars, and a partial parquet."""
        self._close_connection()
        self._delete_build_files()
        partial = self._partial
        self._partial = None
        if partial is not None and partial.is_file():
            partial.unlink()
        self._closed = True

    def __enter__(self) -> DuckDBBatch:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if not self._closed:
            self.abort()

    def _require_open(self) -> Any:
        if self._closed or self._con is None:
            raise RuntimeError("batch is closed")
        return self._con

    def _close_connection(self) -> None:
        con = self._con
        self._con = None
        if con is not None:
            con.close()

    def _delete_build_files(self) -> None:
        self._db.unlink(missing_ok=True)
        Path(str(self._db) + ".wal").unlink(missing_ok=True)
        tmp = Path(str(self._db) + ".tmp")
        if tmp.is_dir():
            shutil.rmtree(tmp, ignore_errors=True)
        else:
            tmp.unlink(missing_ok=True)


def read_parquet_table(path: Path) -> pa.Table:
    with limited_connection(path) as con:
        # Relation.to_arrow_table avoids the DB-API copy warning in DuckDB 2.0.
        return con.sql("SELECT * FROM read_parquet(?)", params=[str(path)]).to_arrow_table()


def read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    return read_parquet_table(path).to_pylist()


def read_parquet_column_tuples(path: Path, columns: list[str]) -> list[tuple[Any, ...]]:
    selected = ", ".join(_quote_ident(name) for name in columns)
    with limited_connection(path) as con:
        rows = con.execute(
            f"SELECT {selected} FROM read_parquet(?)",
            [str(path)],
        ).fetchall()
    return [tuple(row) for row in rows]


# One fetch chunk. Not a row cap: the scan continues until the file ends.
_PARQUET_FETCH_ROWS = 65536
# Last value of these columns is reused per col. The maps stay sheet-width.
_COLUMN_SHARE = ("formula_template", "number_format")


def _iter_parquet_rows(
    path: Path,
    columns: list[str],
    *,
    where: str | None = None,
    params: list[Any] | None = None,
) -> Iterator[tuple[list[str], list[tuple[Any, ...]]]]:
    """Stream named columns. Equal sheet/row and per-column templates share objects.

    Cells are stored row by row, so the previous sheet and row are the values
    to reuse. ``formula_template`` and ``number_format`` repeat down a column.
    """
    selected = ", ".join(_quote_ident(name) for name in columns)
    sql = f"SELECT {selected} FROM read_parquet(?)"
    bind: list[Any] = [str(path)]
    if where:
        sql = f"{sql} WHERE {where}"
        bind.extend(params or [])
    with limited_connection(path) as con:
        cursor = con.execute(sql, bind)
        names = [item[0] for item in cursor.description]
        pos = {name: index for index, name in enumerate(names)}
        sheet_i = pos.get("sheet")
        row_i = pos.get("row")
        col_i = pos.get("col")
        cached = [pos[name] for name in _COLUMN_SHARE if name in pos]
        last_sheet = None
        last_row = None
        last_by: dict[int, dict[Any, Any]] = {index: {} for index in cached}
        while True:
            fetched = cursor.fetchmany(_PARQUET_FETCH_ROWS)
            if not fetched:
                break
            batch: list[tuple[Any, ...]] = []
            for record in fetched:
                vals = list(record)
                if sheet_i is not None:
                    value = vals[sheet_i]
                    if value == last_sheet:
                        vals[sheet_i] = last_sheet
                    else:
                        last_sheet = value
                if row_i is not None:
                    value = vals[row_i]
                    if value == last_row:
                        vals[row_i] = last_row
                    else:
                        last_row = value
                if col_i is not None:
                    col = vals[col_i]
                    for index in cached:
                        value = vals[index]
                        if value is None:
                            continue
                        column_cache = last_by[index]
                        previous = column_cache.get(col)
                        if previous == value:
                            vals[index] = previous
                        else:
                            column_cache[col] = value
                batch.append(tuple(vals))
            del fetched
            yield names, batch


def read_parquet_dicts(
    path: Path,
    columns: list[str],
    *,
    where: str | None = None,
    params: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Read named columns. ``where`` is SQL with ``?`` placeholders, not cell text."""
    found: list[dict[str, Any]] = []
    for names, batch in _iter_parquet_rows(path, columns, where=where, params=params):
        found.extend(dict(zip(names, row, strict=True)) for row in batch)
    return found


def read_blob_bytes(uri: str, settings: Settings | None = None) -> bytes:
    if not uri.startswith("s3://"):
        return Path(uri).read_bytes()
    with limited_connection() as con:
        _configure_s3(con, settings)
        row = con.execute("SELECT content FROM read_blob(?)", [uri]).fetchone()
    if row is None or row[0] is None:
        raise OSError(f"missing object {uri}")
    payload = row[0]
    if isinstance(payload, bytes):
        return payload
    return bytes(payload)


def read_published_file(settings: Settings, local: Path, job_id: str, relative: str) -> bytes:
    """Return the bucket object when the completion marker lists it.

    A missing marker, an empty key list, a recorded export warning, or a
    failed bucket read uses the local file. An object left by a copy that
    did not finish is not served. A settled local book with no marker is
    copied once, then read from the bucket.
    """
    if not settings.minio_configured() or _export_warning_recorded(local, relative):
        return read_blob_bytes(str(local))
    if _marker_lists(settings, job_id, relative):
        return _read_bucket_or_local(settings, local, job_id, relative)
    _ensure_published_book(settings, local, job_id, relative)
    if _marker_lists(settings, job_id, relative):
        return _read_bucket_or_local(settings, local, job_id, relative)
    return read_blob_bytes(str(local))


def _read_bucket_or_local(settings: Settings, local: Path, job_id: str, relative: str) -> bytes:
    url = object_url(
        settings.minio_bucket,
        object_key(settings.minio_prefix, job_id, relative),
    )
    try:
        return read_blob_bytes(url, settings)
    except Exception:
        _LOGGER.warning("minio read failed for %s", relative)
    return read_blob_bytes(str(local))


def _ensure_published_book(settings: Settings, local: Path, job_id: str, relative: str) -> None:
    """Copy a finished local book when the marker does not list it."""
    dest = _session_dest(local, relative)
    if dest is None or not _job_is_settled(dest):
        return
    if not _local_book_complete(settings, job_id, dest):
        return
    book = shared_book_dir(settings.data_dir, job_id)
    with _publish_lock(book / ".minio-publish.lock"):
        if _export_warning_recorded(local, relative) or not _job_is_settled(dest):
            return
        state, keys = _load_marker(settings, job_id)
        if state == "unread":
            return
        if state == "missing":
            warning = export_succeeded_book(settings, job_id=job_id, book=book, dest=dest)
            if warning:
                _set_export_warning(dest, warning)
            return
        wanted = object_key(settings.minio_prefix, job_id, relative)
        if wanted in keys:
            return
        try:
            republish_file(settings, local, job_id, relative)
        except Exception:
            _LOGGER.warning("minio export failed for %s", relative)


def _session_dest(local: Path, relative: str) -> Path | None:
    parts = Path(relative).parts
    if not parts:
        return None
    try:
        return local.parents[len(parts) - 1]
    except IndexError:
        return None


def _job_is_settled(dest: Path) -> bool:
    meta_path = dest / "meta" / "meta.json"
    try:
        stored = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(stored, dict):
        return False
    return stored.get("status") not in {None, "queued", "running"}


def _local_book_complete(settings: Settings, job_id: str, dest: Path) -> bool:
    book = shared_book_dir(settings.data_dir, job_id)
    shared_ready = all((book / name).is_file() for name in _SHARED)
    session_ready = all((dest / name).is_file() for name in _SESSION)
    return shared_ready and session_ready


def _set_export_warning(dest: Path, warning: str) -> None:
    from finance_context.store.fs import update_json

    def mutate(stored: dict) -> dict:
        warnings = stored.get("warnings")
        items = list(warnings) if isinstance(warnings, list) else []
        if warning in items:
            return stored
        return {**stored, "warnings": [*items, warning]}

    path = dest / "meta" / "meta.json"
    if path.is_file():
        update_json(path, mutate)


@contextmanager
def _publish_lock(path: Path) -> Iterator[None]:
    """One writer for this marker file. The same thread may enter the same path again."""
    held = getattr(_publish_held, "paths", None)
    if held is None:
        held = set()
        _publish_held.paths = held
    key = str(path)
    if key in held:
        yield
        return
    from finance_context.store.fs import file_lock

    with file_lock(path):
        held.add(key)
        try:
            yield
        finally:
            held.discard(key)


def republish_file(
    settings: Settings,
    local: Path,
    job_id: str,
    relative: str,
    store: ObjectStore | None = None,
) -> None:
    """Copy one local file onto its bucket key. No MinIO settings is a no-op.

    The key is taken off the marker before the copy and put back only after
    the size check. A failed copy leaves the key off, so the next read uses
    the local file.
    """
    if not settings.minio_configured():
        return
    with _publish_lock(_publish_lock_path(settings, job_id, local, relative)):
        _republish_body(settings, local, job_id, relative, store)


def _republish_body(
    settings: Settings,
    local: Path,
    job_id: str,
    relative: str,
    store: ObjectStore | None,
) -> None:
    target = store if store is not None else DuckDBObjectStore(settings)
    url = object_url(settings.minio_bucket, object_key(settings.minio_prefix, job_id, relative))
    if not _mark_published_key(target, settings, job_id, relative, present=False):
        raise OSError("minio export failed for _complete.json")
    _put_and_check(target, local, url)
    if not _mark_published_key(target, settings, job_id, relative, present=True):
        raise OSError("minio export failed for _complete.json")


def export_succeeded_book(
    settings: Settings,
    *,
    job_id: str,
    book: Path,
    dest: Path,
    store: ObjectStore | None = None,
) -> str | None:
    """Copy a finished book into the bucket. ``None`` means nothing to report.

    Empty MinIO settings return immediately. The copy clears the completion
    marker before it replaces objects, then writes the marker again only after
    every key. A failed copy tries to leave the marker empty. If that write
    also fails, the previous marker stays. The returned warning keeps reads
    on the local files.
    """
    if not settings.minio_configured():
        return None
    with _publish_lock(book / ".minio-publish.lock"):
        return _export_body(settings, job_id=job_id, book=book, dest=dest, store=store)


def _export_body(
    settings: Settings,
    *,
    job_id: str,
    book: Path,
    dest: Path,
    store: ObjectStore | None,
) -> str | None:
    target = store if store is not None else DuckDBObjectStore(settings)
    pairs = [(book / name, name) for name in _SHARED]
    pairs.extend((dest / name, name) for name in _SESSION)
    marker_key = object_key(settings.minio_prefix, job_id, "_complete.json")
    uploaded: list[str] = []
    log_event(
        _LOGGER,
        logging.INFO,
        "export_start",
        "minio export started",
        objects=len(pairs),
    )
    started = time.monotonic()
    warning: str | None = None
    try:
        _put_marker(target, settings.minio_bucket, marker_key, [])
        for local, relative in pairs:
            if not local.is_file():
                raise FileNotFoundError(relative)
            key = object_key(settings.minio_prefix, job_id, relative)
            _put_and_check(target, local, object_url(settings.minio_bucket, key))
            uploaded.append(key)
        _put_marker(target, settings.minio_bucket, marker_key, uploaded)
    except Exception as exc:
        status, detail = _s3_failure(exc)
        _log_export_failure("minio export failed for job %s", job_id, detail=detail)
        # A 403 will not clear the marker either. Skip the second PUT.
        if status != 403:
            try:
                _put_marker(target, settings.minio_bucket, marker_key, [])
            except Exception as clear_exc:
                _log_export_failure(
                    "minio export failed for _complete.json",
                    detail=_s3_failure(clear_exc)[1],
                )
        warning = _EXPORT_WARNING
    log_event(
        _LOGGER,
        logging.INFO,
        "export_done",
        "minio export done",
        duration_ms=int((time.monotonic() - started) * 1000),
        objects=len(uploaded),
        ok=warning is None,
    )
    return warning


def _export_warning_recorded(local: Path, relative: str) -> bool:
    parts = Path(relative).parts
    if not parts:
        return False
    meta_path = local.parents[len(parts) - 1] / "meta" / "meta.json"
    try:
        stored = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    warnings = stored.get("warnings") if isinstance(stored, dict) else None
    return isinstance(warnings, list) and _EXPORT_WARNING in warnings


class DuckDBObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def put(self, local: Path, url: str) -> None:
        # shortcut: read_blob under duckdb_memory_limit, fix when the object must land in MinIO
        with limited_connection() as con:
            _configure_s3(con, self._settings)
            con.execute(
                "COPY (SELECT content FROM read_blob(?)) TO ? "
                "(FORMAT BLOB, COMPRESSION 'none', USE_TMP_FILE false)",
                [str(local), url],
            )

    def size(self, url: str) -> int:
        with limited_connection() as con:
            _configure_s3(con, self._settings)
            row = con.execute(_OBJECT_SIZE_SQL, [url]).fetchone()
        if row is None or row[0] is None:
            raise OSError(f"missing object {url}")
        return int(row[0])


def _put_and_check(store: ObjectStore, local: Path, url: str) -> None:
    store.put(local, url)
    if store.size(url) != local.stat().st_size:
        raise OSError(f"size mismatch for {url}")


def _marker_lists(settings: Settings, job_id: str, relative: str) -> bool:
    state, keys = _load_marker(settings, job_id)
    if state != "ok":
        return False
    return object_key(settings.minio_prefix, job_id, relative) in keys


def _load_marker(settings: Settings, job_id: str) -> tuple[str, set[str]]:
    """``missing`` is no object. ``unread`` is a failed or unusable read."""
    marker_key = object_key(settings.minio_prefix, job_id, "_complete.json")
    url = object_url(settings.minio_bucket, marker_key)
    try:
        parsed = json.loads(read_blob_bytes(url, settings))
    except OSError:
        return "missing", set()
    except Exception as exc:
        if _http_404(exc):
            return "missing", set()
        _LOGGER.warning("minio read failed for _complete.json")
        return "unread", set()
    keys = parsed.get("keys") if isinstance(parsed, dict) else None
    if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
        return "unread", set()
    return "ok", set(keys)


def _http_404(exc: Exception) -> bool:
    """DuckDB 2 reports a missing object as HTTPException, not OSError."""
    import duckdb

    return isinstance(exc, duckdb.HTTPException) and "HTTP 404" in str(exc)


def _s3_failure(exc: BaseException) -> tuple[int | None, str | None]:
    """HTTP status and one ``Code: message`` line. The DuckDB essay stays out."""
    text = str(exc)
    status_match = _HTTP_STATUS.search(text)
    if status_match is None:
        return None, None
    status = int(status_match.group(1))
    for code, message in _S3_LINE.findall(text):
        if code == "HTTP":
            continue
        return status, f"HTTP {status} {code}: {message.strip()}"
    return status, f"HTTP {status}"


def _log_export_failure(template: str, *args: object, detail: str | None) -> None:
    if detail is None:
        _LOGGER.warning(template, *args)
        return
    _LOGGER.warning(template + ": %s", *args, detail)


def _publish_lock_path(settings: Settings, job_id: str, local: Path, relative: str) -> Path:
    """Same content hash shares one bucket prefix, so the lock is the shared book."""
    book = shared_book_dir(settings.data_dir, job_id)
    if book.is_dir():
        return book / ".minio-publish.lock"
    dest = _session_dest(local, relative)
    root = dest if dest is not None else local.parent
    return root / ".minio-publish.lock"


def _mark_published_key(
    store: ObjectStore,
    settings: Settings,
    job_id: str,
    relative: str,
    *,
    present: bool,
) -> bool:
    marker_key = object_key(settings.minio_prefix, job_id, "_complete.json")
    url = object_url(settings.minio_bucket, marker_key)
    try:
        parsed = json.loads(read_blob_bytes(url, settings))
    except Exception:
        _LOGGER.warning("minio export failed for _complete.json")
        return False
    keys = parsed.get("keys") if isinstance(parsed, dict) else None
    if not isinstance(keys, list):
        return False
    wanted = object_key(settings.minio_prefix, job_id, relative)
    if present:
        updated = keys if wanted in keys else [*keys, wanted]
    else:
        updated = [key for key in keys if key != wanted]
    if updated == keys:
        return True
    try:
        _put_marker(store, settings.minio_bucket, marker_key, updated)
    except Exception:
        _LOGGER.warning("minio export failed for _complete.json")
        return False
    return True


def _put_marker(store: ObjectStore, bucket: str, key: str, uploaded: list[str]) -> None:
    payload = (json.dumps({"keys": uploaded}, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(prefix=".complete.", suffix=".json") as handle:
        handle.write(payload)
        handle.flush()
        path = Path(handle.name)
        url = object_url(bucket, key)
        store.put(path, url)
        if store.size(url) != path.stat().st_size:
            raise OSError(f"size mismatch for {url}")


# Image build installs httpfs here. The process only loads that file.
_IMAGE_HTTPFS = Path("/app/.duckdb/extensions")


def _configure_s3(con: Any, settings: Settings | None) -> None:
    if settings is None or not settings.minio_configured():
        raise RuntimeError("MINIO settings are empty")
    endpoint, use_ssl = _endpoint(settings.minio_endpoint)
    secret = ""
    if settings.minio_secret_key is not None:
        secret = settings.minio_secret_key.get_secret_value()
    # Autoload would download a missing httpfs. The image already has the file.
    con.execute("SET autoinstall_known_extensions=false")
    con.execute("SET autoload_known_extensions=false")
    if _IMAGE_HTTPFS.is_dir():
        con.execute("SET extension_directories=[" + _sql_literal(str(_IMAGE_HTTPFS)) + "]")
    con.execute("LOAD httpfs")
    con.execute(
        """
        CREATE OR REPLACE SECRET (
            TYPE s3,
            KEY_ID ?,
            SECRET ?,
            REGION ?,
            ENDPOINT ?,
            URL_STYLE 'path',
            USE_SSL ?
        )
        """,
        [settings.minio_access_key, secret, settings.minio_region, endpoint, use_ssl],
    )


def _endpoint(value: str) -> tuple[str, bool]:
    token = value.strip()
    if "://" not in token:
        return token, False
    from urllib.parse import urlsplit

    parts = urlsplit(token)
    return parts.netloc, parts.scheme == "https"


def _spill_job_slots() -> int:
    """How many builds may spill at once. The setting, not whoever has started."""
    return max(1, Settings().max_concurrent_jobs)


def _spill_limit_bytes(anchor: Path, floor: int, slots: int | None = None) -> int:
    """How many bytes one connection may spill beside ``anchor``.

    ``floor`` is ``duckdb_max_temp_directory_size``. A large sort hits that cap
    while the buffer manager is still inside ``duckdb_memory_limit``. When the
    volume can hold the spill and still keep 1 GiB free, the cap rises, at most
    to 2 GiB, and that free space is shared across ``slots`` jobs. A configured
    cap above 2 GiB stays as configured. A tighter volume, a share below the
    floor, or a volume that cannot be measured, keeps the setting.
    """
    ceiling = max(floor, _SPILL_CEILING_BYTES)
    jobs = _spill_job_slots() if slots is None else max(1, slots)
    probe = anchor if anchor.is_dir() else anchor.parent
    if not probe.exists():
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return floor
    if free < floor:
        return floor
    roomy = free - _SPILL_RESERVE_BYTES
    if roomy < floor:
        return floor
    share = roomy // jobs
    if share < floor:
        return floor
    return min(ceiling, share)


def _spill_limit_setting(
    anchor: Path, floor_text: str, floor: int, slots: int | None = None
) -> str:
    size = _spill_limit_bytes(anchor, floor, slots)
    if size == floor:
        return floor_text
    gibibyte = 1 << 30
    mebibyte = 1 << 20
    if size % gibibyte == 0:
        rounded = size
        text = f"{size // gibibyte}GiB"
    else:
        rounded = (size // mebibyte) * mebibyte
        text = f"{size // mebibyte}MiB"
    # ``1GB`` is 1e9 bytes. Rounding the byte cap down to a whole MiB can
    # land under that floor and fail a sort the setting already allowed.
    if rounded < floor:
        return floor_text
    return text


@contextmanager
def limited_connection(anchor: Path | None = None) -> Iterator[Any]:
    """One DuckDB connection with the configured caps and a private temp directory.

    ``memory_limit`` is the per-connection buffer-manager ceiling from
    ``duckdb_memory_limit``. It is not process RSS. Spill files sit next to
    ``anchor`` when the scan has a local file, so a memory-backed ``/tmp`` is
    not the cap. The temp directory is removed when the connection closes.
    """
    import duckdb

    if anchor is None:
        base = Path(tempfile.gettempdir())
    else:
        base = Path(anchor)
        base = base if base.is_dir() else base.parent
        base.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="duckdb-scan-", dir=base))
    con = None
    try:
        con = duckdb.connect()
        _set_duckdb_limits(con, str(tmp), anchor=anchor)
        yield con
    finally:
        if con is not None:
            con.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _set_duckdb_limits(con: Any, temp_directory: str, *, anchor: Path | None = None) -> None:
    settings = Settings()
    temp_cap = settings.duckdb_max_temp_directory_size
    if anchor is not None:
        floor = _duckdb_size_bytes(temp_cap)
        if floor is not None:
            temp_cap = _spill_limit_setting(anchor, temp_cap, floor)
    con.execute(f"SET memory_limit={_sql_literal(settings.duckdb_memory_limit)}")
    con.execute(f"SET temp_directory={_sql_literal(temp_directory)}")
    con.execute(f"SET max_temp_directory_size={_sql_literal(temp_cap)}")
    con.execute(f"SET threads={_DUCKDB_THREADS}")


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
