"""DuckDB reads and writes of one local file or one MinIO object."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa

from finance_context.settings import Settings
from finance_context.store.paths import shared_book_dir

_LOGGER = logging.getLogger("finance_context.store")
_BATCH_ROWS = 2048
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
_publish_held = threading.local()


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
    import duckdb

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    groups = max(table.num_rows, 1)
    with duckdb.connect() as con:
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
        tmp = _sql_literal(str(self._db) + ".tmp")
        self._con.execute("SET memory_limit='256MB'")
        self._con.execute(f"SET temp_directory={tmp}")
        self._con.execute("SET max_temp_directory_size='1GB'")
        self._con.execute("SET threads=1")
        specs = ", ".join(
            f"{_quote_ident(name)} {_DUCK_TYPES[dtype]}" for name, dtype in columns
        )
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
    with limited_connection() as con:
        # Relation.to_arrow_table avoids the DB-API copy warning in DuckDB 2.0.
        return con.sql("SELECT * FROM read_parquet(?)", params=[str(path)]).to_arrow_table()


def read_parquet_rows(path: Path) -> list[dict[str, Any]]:
    return read_parquet_table(path).to_pylist()


def read_parquet_column_tuples(path: Path, columns: list[str]) -> list[tuple[Any, ...]]:
    selected = ", ".join(_quote_ident(name) for name in columns)
    with limited_connection() as con:
        rows = con.execute(
            f"SELECT {selected} FROM read_parquet(?)",
            [str(path)],
        ).fetchall()
    return [tuple(row) for row in rows]


def read_parquet_dicts(
    path: Path,
    columns: list[str],
    *,
    where: str | None = None,
    params: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Read named columns. ``where`` is SQL with ``?`` placeholders, not cell text."""
    selected = ", ".join(_quote_ident(name) for name in columns)
    sql = f"SELECT {selected} FROM read_parquet(?)"
    bind: list[Any] = [str(path)]
    if where:
        sql = f"{sql} WHERE {where}"
        bind.extend(params or [])
    with limited_connection() as con:
        cursor = con.execute(sql, bind)
        names = [item[0] for item in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


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
    try:
        _put_marker(target, settings.minio_bucket, marker_key, [])
        for local, relative in pairs:
            if not local.is_file():
                raise FileNotFoundError(relative)
            key = object_key(settings.minio_prefix, job_id, relative)
            _put_and_check(target, local, object_url(settings.minio_bucket, key))
            uploaded.append(key)
        _put_marker(target, settings.minio_bucket, marker_key, uploaded)
    except Exception:
        _LOGGER.warning("minio export failed for job %s", job_id)
        try:
            _put_marker(target, settings.minio_bucket, marker_key, [])
        except Exception:
            _LOGGER.warning("minio export failed for _complete.json")
        return _EXPORT_WARNING
    return None


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
        # shortcut: whole-file read_blob under 256MB, fix when the object must land in MinIO
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
            row = con.execute(
                "SELECT octet_length(content) FROM read_blob(?)",
                [url],
            ).fetchone()
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


def _configure_s3(con: Any, settings: Settings | None) -> None:
    if settings is None or not settings.minio_configured():
        raise RuntimeError("MINIO settings are empty")
    endpoint, use_ssl = _endpoint(settings.minio_endpoint)
    secret = ""
    if settings.minio_secret_key is not None:
        secret = settings.minio_secret_key.get_secret_value()
    con.execute("INSTALL httpfs")
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
        [
            settings.minio_access_key,
            secret,
            settings.minio_region,
            endpoint,
            use_ssl,
        ],
    )


def _endpoint(value: str) -> tuple[str, bool]:
    token = value.strip()
    if "://" not in token:
        return token, False
    from urllib.parse import urlsplit

    parts = urlsplit(token)
    return parts.netloc, parts.scheme == "https"


@contextmanager
def limited_connection() -> Iterator[Any]:
    """One short DuckDB scan with a private temp directory.

    ``memory_limit`` is the buffer-manager ceiling, the same 256MB the writer
    already uses. It is not the process RSS.
    """
    import duckdb

    tmp = tempfile.mkdtemp(prefix="duckdb-scan-")
    con = None
    try:
        con = duckdb.connect()
        con.execute("SET memory_limit='256MB'")
        con.execute("SET threads=1")
        con.execute(f"SET temp_directory={_sql_literal(tmp)}")
        con.execute("SET max_temp_directory_size='1GB'")
        yield con
    finally:
        if con is not None:
            con.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
