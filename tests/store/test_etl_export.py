"""MinIO export is a second copy. Empty settings stay on DATA_DIR. No network."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline, _append_export_warning
from finance_context.settings import Settings
from finance_context.store.duckdb_io import (
    _EXPORT_ATTEMPTS,
    _EXPORT_RETRY_PAUSE_SEC,
    _IMAGE_HTTPFS,
    _UPLOAD_CHUNK_BYTES,
    DuckDBBatch,
    DuckDBObjectStore,
    PublishedFile,
    _configure_s3,
    _s3_filesystem,
    _sql_literal,
    export_succeeded_book,
    object_key,
    read_blob_bytes,
    read_published_file,
    republish_file,
    stream_local_file,
)
from finance_context.store.fs import RowGroupWriter, read_parquet, write_json, write_parquet

_JOB = "ab" * 32
_SECRET = "super-secret-value"
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
_LOCAL_ONLY = (".lock", "stages.json", "context/context.md.renderer")


class _MemoryStore:
    def __init__(self, *, fail: Exception | None = None, mismatch: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.order: list[str] = []
        self.attempts = 0
        self.fail = fail
        self.mismatch = mismatch
        self.fail_on: str | None = None

    def put(self, local: Path, url: str) -> None:
        self.attempts += 1
        if self.fail is not None:
            raise self.fail
        if self.fail_on is not None and self.fail_on in url:
            raise RuntimeError("stopped")
        self.objects[url] = local.read_bytes()
        self.order.append(url)

    def get(self, url: str) -> bytes:
        try:
            return self.objects[url]
        except KeyError:
            raise FileNotFoundError(url) from None

    def size(self, url: str) -> int:
        if self.mismatch:
            return 0
        try:
            return len(self.objects[url])
        except KeyError:
            raise FileNotFoundError(url) from None


class _MemoryHandle:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0

    def read(self, size: int) -> bytes:
        chunk = self._payload[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def close(self) -> None:
        return None


def _body(published: PublishedFile) -> bytes:
    data = b"".join(published)
    assert published.content_length == len(data)
    return data


def _serve_memory(monkeypatch: pytest.MonkeyPatch, store: _MemoryStore) -> None:
    """Bucket reads in these tests stay on the memory store, not a live endpoint."""

    def open_object(settings: Settings, url: str) -> PublishedFile:
        del settings
        payload = store.objects[url]
        return PublishedFile(len(payload), _MemoryHandle(payload))

    monkeypatch.setattr("finance_context.store.duckdb_io._open_published_object", open_object)


def _minio(**overrides: object) -> Settings:
    fields: dict[str, object] = {
        "minio_endpoint": "http://minio:9000",
        "minio_bucket": "finance-context",
        "minio_access_key": "minioadmin",
        "minio_secret_key": _SECRET,
        "minio_prefix": "team/a",
        "minio_region": "us-east-1",
    }
    fields.update(overrides)
    return Settings(_env_file=None, **fields)  # type: ignore[arg-type]


def _fill(book: Path, dest: Path) -> None:
    for name in _SHARED:
        path = book / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"shared:{name}\n".encode())
    for name in _SESSION:
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"session:{name}\n".encode())
    (book / ".lock").write_text("lock", encoding="utf-8")
    (book / "stages.json").write_text("{}\n", encoding="utf-8")
    (dest / "context" / "context.md.renderer").parent.mkdir(parents=True, exist_ok=True)
    (dest / "context" / "context.md.renderer").write_text("transposed-cells\n", encoding="utf-8")


def test_empty_minio_settings_do_not_touch_the_store(tmp_path: Path) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    store = _MemoryStore()
    assert (
        export_succeeded_book(
            Settings(_env_file=None),
            job_id=_JOB,
            book=book,
            dest=dest,
            store=store,
        )
        is None
    )
    assert store.order == []
    assert (book / "raw" / "cells.parquet").read_bytes().startswith(b"shared:")


def test_export_lists_every_book_key_and_writes_the_marker_last(tmp_path: Path) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    store = _MemoryStore()
    assert (
        export_succeeded_book(
            _minio(),
            job_id=_JOB,
            book=book,
            dest=dest,
            store=store,
        )
        is None
    )
    prefix = f"s3://finance-context/team/a/books/{_JOB}/"
    expected = [prefix + name for name in (*_SHARED, *_SESSION)]
    assert store.order[:-1] == expected
    assert store.order[-1] == prefix + "_complete.json"
    for name in _LOCAL_ONLY:
        assert all(name not in url for url in store.order)
    marker = json.loads(store.objects[store.order[-1]])
    assert marker["keys"] == [object_key("team/a", _JOB, name) for name in (*_SHARED, *_SESSION)]
    assert store.objects[prefix + "raw/cells.parquet"] == b"shared:raw/cells.parquet\n"
    assert store.objects[prefix + "context/context.json"] == b"session:context/context.json\n"
    assert store.objects[prefix + "context/context.md"] == b"session:context/context.md\n"


def test_partial_and_blank_minio_fields_fail_startup() -> None:
    with pytest.raises(ValidationError, match="MINIO_ENDPOINT"):
        _minio(minio_bucket="")
    with pytest.raises(ValidationError, match="MINIO_ENDPOINT"):
        _minio(minio_secret_key="   ")
    with pytest.raises(ValidationError, match="MINIO_PREFIX"):
        _minio(minio_prefix="team/../secret")
    settings = Settings(_env_file=None)
    assert settings.minio_configured() is False
    assert settings.minio_region == "us-east-1"
    assert _minio(minio_region="  ").minio_region == "us-east-1"
    assert _minio(minio_region=" eu-central-1 ").minio_region == "eu-central-1"
    for bad in (
        "minio:9000",
        "https://s3.internal/s3",
        "http://user:pass@minio:9000",
        "http://minio:9000?x=1",
    ):
        with pytest.raises(ValidationError, match="MINIO_ENDPOINT"):
            _minio(minio_endpoint=bad)
    assert _minio(minio_endpoint="https://s3.internal:9000/").minio_endpoint == (
        "https://s3.internal:9000/"
    )


def test_s3_secret_passes_the_region() -> None:
    class _Con:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def execute(self, sql: str, params: object = None) -> None:
            self.calls.append((sql, params))

    con = _Con()
    _configure_s3(con, _minio(minio_region="eu-central-1"))
    sqls = [sql for sql, _params in con.calls]
    assert sqls[0] == "SET autoinstall_known_extensions=false"
    assert sqls[1] == "SET autoload_known_extensions=false"
    assert "LOAD httpfs" in sqls
    assert all("INSTALL" not in sql for sql in sqls)
    assert all("SET extension_directory=" not in sql for sql in sqls)
    if _IMAGE_HTTPFS.is_dir():
        assert sqls[2].startswith("SET extension_directories=")
        assert sqls[3] == "LOAD httpfs"
    else:
        assert all("extension_directories" not in sql for sql in sqls)
        assert sqls[2] == "LOAD httpfs"
    sql, params = con.calls[-1]
    assert "REGION ?" in sql
    assert "SESSION_TOKEN" not in sql
    assert "KMS_KEY_ID" not in sql
    assert params == ["minioadmin", _SECRET, "eu-central-1", "minio:9000", False]


def test_s3_secret_uses_the_image_extension_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("finance_context.store.duckdb_io._IMAGE_HTTPFS", tmp_path)

    class _Con:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def execute(self, sql: str, params: object = None) -> None:
            self.calls.append((sql, params))

    con = _Con()
    _configure_s3(con, _minio())
    sqls = [sql for sql, _params in con.calls]
    assert sqls[2] == "SET extension_directories=[" + _sql_literal(str(tmp_path)) + "]"
    assert sqls[3] == "LOAD httpfs"
    assert all("INSTALL" not in sql for sql in sqls)


def test_copy_failure_keeps_the_local_files_and_hides_the_secret(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    store = _MemoryStore(fail=RuntimeError(_SECRET))
    caplog.set_level(logging.WARNING, logger="finance_context.store")
    warning = export_succeeded_book(
        _minio(),
        job_id=_JOB,
        book=book,
        dest=dest,
        store=store,
    )
    assert warning == "minio export failed"
    assert _SECRET not in warning
    assert _SECRET not in caplog.text
    assert store.attempts == 1
    assert store.order == []
    assert (dest / "meta" / "meta.json").is_file()
    mismatch = _MemoryStore(mismatch=True)
    assert (
        export_succeeded_book(_minio(), job_id=_JOB, book=book, dest=dest, store=mismatch)
        == "minio export failed"
    )
    prefix = f"s3://finance-context/team/a/books/{_JOB}/"
    assert mismatch.order == [prefix + "source/source.xlsx"]
    assert prefix + "_complete.json" not in mismatch.objects


class _OutOfMemory(Exception):
    pass


_OutOfMemory.__name__ = "OutOfMemoryException"


def test_memory_failure_logs_the_object_path_and_size(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    context = dest / "context" / "context.json"

    class _Store(_MemoryStore):
        def put(self, local: Path, url: str) -> None:
            if url.endswith("context/context.json"):
                raise _OutOfMemory("Out of Memory Error: failed to allocate data of size 1.0 GiB")
            super().put(local, url)

    caplog.set_level(logging.WARNING, logger="finance_context.store")
    assert (
        export_succeeded_book(_minio(), job_id=_JOB, book=book, dest=dest, store=_Store())
        == "minio export failed"
    )
    assert f"at context/context.json ({context.stat().st_size} bytes)" in caplog.text
    assert "OutOfMemoryException: Out of Memory Error:" in caplog.text
    assert _SECRET not in caplog.text


_DENIED = (
    "HTTP Error: HTTP PUT error uploading to 's3://bucket/books/job/_complete.json' "
    "(HTTP 403 Forbidden)\n\n"
    "AccessDenied: Access Denied.\n\n"
    "Authentication Failure - this is usually caused by invalid or missing credentials.\n"
    "* Credentials are provided, but they did not work.\n"
)


def test_access_denied_is_logged_once_without_the_duckdb_essay(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    store = _MemoryStore(fail=RuntimeError(_DENIED))
    caplog.set_level(logging.WARNING, logger="finance_context.store")
    assert (
        export_succeeded_book(_minio(), job_id=_JOB, book=book, dest=dest, store=store)
        == "minio export failed"
    )
    assert store.attempts == 1
    assert "HTTP 403 AccessDenied: Access Denied." in caplog.text
    assert "Authentication Failure" not in caplog.text
    assert "Credentials are provided" not in caplog.text
    assert "minio export failed for _complete.json" not in caplog.text


_SLOW = (
    "HTTP Error: HTTP PUT error uploading to 's3://bucket/books/job/_complete.json' "
    "(HTTP 500 Internal Server Error)\n\n"
    "SlowDown: Please reduce your request rate.\n\n"
    "Authentication Failure - this is usually caused by invalid or missing credentials.\n"
)


def test_http_500_retries_the_upload_and_logs_the_s3_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    store = _MemoryStore(fail=RuntimeError(_SLOW))
    pauses: list[float] = []
    monkeypatch.setattr(
        "finance_context.store.duckdb_io.time.sleep",
        lambda seconds: pauses.append(seconds),
    )
    caplog.set_level(logging.WARNING, logger="finance_context.store")
    assert (
        export_succeeded_book(_minio(), job_id=_JOB, book=book, dest=dest, store=store)
        == "minio export failed"
    )
    assert store.attempts == _EXPORT_ATTEMPTS
    assert pauses == [_EXPORT_RETRY_PAUSE_SEC] * (_EXPORT_ATTEMPTS - 1)
    assert "HTTP 500 SlowDown: Please reduce your request rate." in caplog.text
    assert "Authentication Failure" not in caplog.text


def test_second_export_skips_a_key_whose_size_matches(tmp_path: Path) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    store = _MemoryStore()
    settings = _minio()
    assert export_succeeded_book(settings, job_id=_JOB, book=book, dest=dest, store=store) is None
    copied = list(store.order)
    assert export_succeeded_book(settings, job_id=_JOB, book=book, dest=dest, store=store) is None
    assert store.order == copied
    prefix = f"s3://finance-context/team/a/books/{_JOB}/"
    local = dest / "context" / "context.json"
    local.write_bytes(b'{"v":2}\n')
    assert export_succeeded_book(settings, job_id=_JOB, book=book, dest=dest, store=store) is None
    # The key was already listed, so a same-size check does not rewrite the marker.
    assert store.order[len(copied) :] == [prefix + "context/context.json"]
    marker = json.loads(store.objects[prefix + "_complete.json"])
    assert object_key("team/a", _JOB, "context/context.json") in marker["keys"]
    assert len(marker["keys"]) == len(_SHARED) + len(_SESSION)


def test_missing_local_file_does_not_drop_the_other_keys(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    store = _MemoryStore()
    settings = _minio()
    assert export_succeeded_book(settings, job_id=_JOB, book=book, dest=dest, store=store) is None
    missing = dest / "context" / "context.json"
    missing.unlink()
    caplog.set_level(logging.WARNING, logger="finance_context.store")
    assert export_succeeded_book(settings, job_id=_JOB, book=book, dest=dest, store=store) is None
    assert "minio export skipped missing context/context.json" in caplog.text
    keys = json.loads(store.objects[f"s3://finance-context/team/a/books/{_JOB}/_complete.json"])[
        "keys"
    ]
    assert object_key("team/a", _JOB, "context/context.json") in keys
    assert object_key("team/a", _JOB, "raw/cells.parquet") in keys
    assert len(keys) == len(_SHARED) + len(_SESSION)


def test_http_404_is_not_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    store = _MemoryStore(fail=RuntimeError("HTTP Error: missing object (HTTP 404 Not Found)"))
    pauses: list[float] = []
    monkeypatch.setattr(
        "finance_context.store.duckdb_io.time.sleep",
        lambda seconds: pauses.append(seconds),
    )
    assert (
        export_succeeded_book(_minio(), job_id=_JOB, book=book, dest=dest, store=store)
        == "minio export failed"
    )
    assert store.attempts == 1
    assert pauses == []


def test_local_read_blob_does_not_open_duckdb(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    path = tmp_path / "graph.json"
    payload = b'{"nodes": 1}\n'
    path.write_bytes(payload)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("duckdb.connect")

    monkeypatch.setattr(duckdb, "connect", boom)
    assert read_blob_bytes(str(path)) == payload


def test_local_duckdb_reads_match_the_serializer(tmp_path: Path) -> None:
    table = tmp_path / "cells.parquet"
    write_parquet(
        table,
        (("sheet", "VARCHAR"), ("row", "INTEGER"), ("hidden", "BOOLEAN")),
        [("P&L", 2, False)],
    )
    rows = read_parquet(table)
    assert rows == [{"sheet": "P&L", "row": 2, "hidden": False}]
    assert pq.ParquetFile(table).schema_arrow.names == ["sheet", "row", "hidden"]
    document = tmp_path / "context" / "context.json"
    write_json(document, {"a": 1})
    assert read_blob_bytes(str(document)) == document.read_bytes()
    markdown = tmp_path / "context" / "context.md"
    markdown.write_bytes(b"# Financial context\n")
    assert read_blob_bytes(str(markdown)) == b"# Financial context\n"


def test_published_read_falls_back_to_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "context" / "context.json"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(b'{"ok": true}\n')

    def fake_read(uri: str, settings: Settings | None = None) -> bytes:
        del settings
        if uri.startswith("s3://"):
            raise OSError("minio down")
        return Path(uri).read_bytes()

    monkeypatch.setattr("finance_context.store.duckdb_io.read_blob_bytes", fake_read)
    body = read_published_file(_minio(), local, _JOB, "context/context.json")
    assert _body(body) == b'{"ok": true}\n'


def test_failed_recopy_hides_the_bucket_until_the_marker_is_rewritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    store = _MemoryStore()
    settings = _minio()
    assert export_succeeded_book(settings, job_id=_JOB, book=book, dest=dest, store=store) is None
    prefix = f"s3://finance-context/team/a/books/{_JOB}/"
    local = dest / "context" / "context.json"
    published = store.objects[prefix + "context/context.json"]
    local.write_bytes(b'{"source":"budget.xlsx","v":2}\n')

    def fake_read(uri: str, settings: Settings | None = None) -> bytes:
        del settings
        if uri.startswith("s3://"):
            if uri not in store.objects:
                raise OSError(uri)
            return store.objects[uri]
        return Path(uri).read_bytes()

    monkeypatch.setattr("finance_context.store.duckdb_io.read_blob_bytes", fake_read)
    _serve_memory(monkeypatch, store)
    assert _body(read_published_file(settings, local, _JOB, "context/context.json")) == published

    store.fail_on = "context/context.json"
    assert (
        export_succeeded_book(settings, job_id=_JOB, book=book, dest=dest, store=store)
        == "minio export failed"
    )
    keys = json.loads(store.objects[prefix + "_complete.json"])["keys"]
    assert object_key("team/a", _JOB, "context/context.json") not in keys
    assert object_key("team/a", _JOB, "raw/cells.parquet") in keys
    assert _body(read_published_file(settings, local, _JOB, "context/context.json")) == (
        local.read_bytes()
    )
    cells = book / "raw" / "cells.parquet"
    cells.write_bytes(b"local-only-cells\n")
    assert store.objects[prefix + "raw/cells.parquet"] != b"local-only-cells\n"
    assert _body(read_published_file(settings, cells, _JOB, "raw/cells.parquet")) == (
        store.objects[prefix + "raw/cells.parquet"]
    )


def test_failed_republish_drops_the_key_so_the_next_read_is_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "context.md"
    local.write_bytes(b"new-md\n")
    prefix = f"s3://finance-context/team/a/books/{_JOB}/"
    marker_url = prefix + "_complete.json"
    markdown_key = object_key("team/a", _JOB, "context/context.md")
    other_key = object_key("team/a", _JOB, "context/context.json")
    store = _MemoryStore()
    store.objects[marker_url] = (
        json.dumps({"keys": [markdown_key, other_key]}, indent=2) + "\n"
    ).encode()
    store.objects[prefix + "context/context.md"] = b"old-md\n"
    store.fail_on = "context/context.md"

    def fake_read(uri: str, settings: Settings | None = None) -> bytes:
        del settings
        if uri.startswith("s3://"):
            if uri not in store.objects:
                raise OSError(uri)
            return store.objects[uri]
        return Path(uri).read_bytes()

    monkeypatch.setattr("finance_context.store.duckdb_io.read_blob_bytes", fake_read)
    settings = _minio()
    with pytest.raises(RuntimeError, match="stopped"):
        republish_file(settings, local, _JOB, "context/context.md", store=store)
    marker = json.loads(store.objects[marker_url])
    assert markdown_key not in marker["keys"]
    assert other_key in marker["keys"]
    assert _body(read_published_file(settings, local, _JOB, "context/context.md")) == b"new-md\n"


def test_failed_marker_write_keeps_the_listed_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    settings = _minio()
    store = _MemoryStore()
    assert export_succeeded_book(settings, job_id=_JOB, book=book, dest=dest, store=store) is None
    prefix = f"s3://finance-context/team/a/books/{_JOB}/"
    marker_url = prefix + "_complete.json"
    dropped = object_key("team/a", _JOB, "graph/graph.md")
    listed = json.loads(store.objects[marker_url])
    listed["keys"] = [key for key in listed["keys"] if key != dropped]
    store.objects[marker_url] = (json.dumps(listed, indent=2) + "\n").encode()
    local = dest / "context" / "context.json"
    local.write_bytes(b'{"v":2}\n')
    skipped = {"done": False}
    original_put = store.put

    def put(local_path: Path, url: str) -> None:
        if url.endswith("/_complete.json") and not skipped["done"]:
            skipped["done"] = True
            raise RuntimeError("marker down")
        original_put(local_path, url)

    monkeypatch.setattr(store, "put", put)

    def fake_read(uri: str, settings: Settings | None = None) -> bytes:
        del settings
        if uri.startswith("s3://"):
            if uri not in store.objects:
                raise OSError(uri)
            return store.objects[uri]
        return Path(uri).read_bytes()

    monkeypatch.setattr("finance_context.store.duckdb_io.read_blob_bytes", fake_read)
    _serve_memory(monkeypatch, store)
    assert export_succeeded_book(settings, job_id=_JOB, book=book, dest=dest, store=store) == (
        "minio export failed"
    )
    kept = json.loads(store.objects[marker_url])["keys"]
    assert dropped not in kept
    assert object_key("team/a", _JOB, "raw/cells.parquet") in kept
    assert len(kept) == len(_SHARED) + len(_SESSION) - 1
    assert _body(read_published_file(settings, local, _JOB, "context/context.json")) == b'{"v":2}\n'
    meta = dest / "meta" / "meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text('{"warnings": ["minio export failed"]}\n', encoding="utf-8")
    store.objects[prefix + "_complete.json"] = (
        json.dumps({"keys": [object_key("team/a", _JOB, "context/context.json")]}, indent=2) + "\n"
    ).encode()
    store.objects[prefix + "context/context.json"] = b'{"v":1}\n'
    assert _body(read_published_file(settings, local, _JOB, "context/context.json")) == b'{"v":2}\n'


def _memory_read(store: _MemoryStore):
    def fake_read(uri: str, settings: Settings | None = None) -> bytes:
        del settings
        if uri.startswith("s3://"):
            if uri not in store.objects:
                raise OSError(uri)
            return store.objects[uri]
        return Path(uri).read_bytes()

    return fake_read


def test_http_404_is_a_missing_marker_and_a_connection_error_is_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb

    root = tmp_path / "data"
    book = root / "shared" / "books" / _JOB
    dest = root / "sessions" / "local" / "jobs" / _JOB
    _fill(book, dest)
    (dest / "meta" / "meta.json").write_text(
        json.dumps({"status": "succeeded", "warnings": []}),
        encoding="utf-8",
    )
    settings = _minio(data_dir=root)
    local = dest / "context" / "context.json"
    store = _MemoryStore()
    monkeypatch.setattr(
        "finance_context.store.duckdb_io.DuckDBObjectStore",
        lambda _settings: store,
    )

    def unread(uri: str, settings: Settings | None = None) -> bytes:
        del settings
        if uri.startswith("s3://"):
            raise duckdb.IOException("Could not connect to server")
        return Path(uri).read_bytes()

    monkeypatch.setattr("finance_context.store.duckdb_io.read_blob_bytes", unread)
    _serve_memory(monkeypatch, store)
    assert _body(read_published_file(settings, local, _JOB, "context/context.json")) == (
        local.read_bytes()
    )
    assert store.order == []

    def missing(uri: str, settings: Settings | None = None) -> bytes:
        del settings
        if uri.startswith("s3://") and uri not in store.objects:
            raise duckdb.HTTPException("HTTP Error: HTTP HEAD error (HTTP 404 Not Found)")
        if uri.startswith("s3://"):
            return store.objects[uri]
        return Path(uri).read_bytes()

    monkeypatch.setattr("finance_context.store.duckdb_io.read_blob_bytes", missing)
    assert _body(read_published_file(settings, local, _JOB, "context/context.json")) == (
        local.read_bytes()
    )
    prefix = f"s3://finance-context/team/a/books/{_JOB}/"
    marker = json.loads(store.objects[prefix + "_complete.json"])
    assert object_key("team/a", _JOB, "context/context.json") in marker["keys"]


def test_missing_marker_is_filled_from_the_local_book(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    book = root / "shared" / "books" / _JOB
    dest = root / "sessions" / "local" / "jobs" / _JOB
    _fill(book, dest)
    (dest / "meta" / "meta.json").write_text(
        json.dumps({"status": "succeeded", "warnings": ["minio export failed"]}),
        encoding="utf-8",
    )
    settings = _minio(data_dir=root)
    store = _MemoryStore()
    monkeypatch.setattr(
        "finance_context.store.duckdb_io.DuckDBObjectStore",
        lambda _settings: store,
    )
    monkeypatch.setattr("finance_context.store.duckdb_io.read_blob_bytes", _memory_read(store))
    _serve_memory(monkeypatch, store)
    local = dest / "context" / "context.json"
    assert _body(read_published_file(settings, local, _JOB, "context/context.json")) == (
        local.read_bytes()
    )
    assert store.order == []

    (dest / "meta" / "meta.json").write_text(
        json.dumps({"status": "succeeded", "warnings": []}),
        encoding="utf-8",
    )
    assert _body(read_published_file(settings, local, _JOB, "context/context.json")) == (
        local.read_bytes()
    )
    prefix = f"s3://finance-context/team/a/books/{_JOB}/"
    marker = json.loads(store.objects[prefix + "_complete.json"])
    assert object_key("team/a", _JOB, "context/context.json") in marker["keys"]
    copied = len(store.order)
    assert _body(read_published_file(settings, local, _JOB, "context/context.json")) == (
        local.read_bytes()
    )
    assert len(store.order) == copied
    stored = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert stored["warnings"] == []


def test_one_missing_key_is_republished_without_clearing_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data"
    book = root / "shared" / "books" / _JOB
    dest = root / "sessions" / "local" / "jobs" / _JOB
    _fill(book, dest)
    (dest / "meta" / "meta.json").write_text(
        json.dumps({"status": "succeeded", "warnings": []}),
        encoding="utf-8",
    )
    settings = _minio(data_dir=root)
    store = _MemoryStore()
    prefix = f"s3://finance-context/team/a/books/{_JOB}/"
    other = object_key("team/a", _JOB, "graph/graph.json")
    store.objects[prefix + "_complete.json"] = (json.dumps({"keys": [other]}) + "\n").encode()
    store.objects[prefix + "graph/graph.json"] = b"keep\n"
    monkeypatch.setattr(
        "finance_context.store.duckdb_io.DuckDBObjectStore",
        lambda _settings: store,
    )
    monkeypatch.setattr("finance_context.store.duckdb_io.read_blob_bytes", _memory_read(store))
    _serve_memory(monkeypatch, store)
    local = dest / "context" / "context.json"
    assert _body(read_published_file(settings, local, _JOB, "context/context.json")) == (
        local.read_bytes()
    )
    marker = json.loads(store.objects[prefix + "_complete.json"])
    assert other in marker["keys"]
    assert object_key("team/a", _JOB, "context/context.json") in marker["keys"]
    written = _marker_bodies(store)
    assert written
    assert all(body["keys"] for body in written)


def _marker_bodies(store: _MemoryStore) -> list[dict]:
    bodies = []
    for url in store.order:
        if url.endswith("/_complete.json"):
            bodies.append(json.loads(store.objects[url]))
    return bodies


def test_truncated_republish_is_not_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = tmp_path / "context.md"
    local.write_bytes(b"new-md\n")
    prefix = f"s3://finance-context/team/a/books/{_JOB}/"
    marker_url = prefix + "_complete.json"
    markdown_key = object_key("team/a", _JOB, "context/context.md")
    store = _MemoryStore()
    store.objects[marker_url] = (json.dumps({"keys": [markdown_key]}, indent=2) + "\n").encode()
    store.objects[prefix + "context/context.md"] = b"old-md\n"

    original_put = store.put
    original_size = store.size

    def put(local_path: Path, url: str) -> None:
        if url.endswith("/context/context.md"):
            store.objects[url] = b"x"
            store.order.append(url)
            return
        original_put(local_path, url)

    def size(url: str) -> int:
        if url.endswith("/context/context.md"):
            return 1
        return original_size(url)

    monkeypatch.setattr(store, "put", put)
    monkeypatch.setattr(store, "size", size)

    def fake_read(uri: str, settings: Settings | None = None) -> bytes:
        del settings
        if uri.startswith("s3://"):
            if uri not in store.objects:
                raise OSError(uri)
            return store.objects[uri]
        return Path(uri).read_bytes()

    monkeypatch.setattr("finance_context.store.duckdb_io.read_blob_bytes", fake_read)
    settings = _minio()
    with pytest.raises(OSError, match="size mismatch"):
        republish_file(settings, local, _JOB, "context/context.md", store=store)
    assert markdown_key not in json.loads(store.objects[marker_url])["keys"]
    assert _body(read_published_file(settings, local, _JOB, "context/context.md")) == b"new-md\n"


def test_requested_row_groups_are_separate_files_stitched_by_pyarrow(tmp_path: Path) -> None:
    path = tmp_path / "cell_edges.parquet"
    columns = (("source", "VARCHAR"), ("row", "INTEGER"))
    with RowGroupWriter(path, columns, 1) as writer:
        for index in range(3):
            writer.write_lists([[f"P&L!A{index}"], [index]])
    assert pq.ParquetFile(path).num_row_groups == 3
    assert read_parquet(path) == [
        {"source": "P&L!A0", "row": 0},
        {"source": "P&L!A1", "row": 1},
        {"source": "P&L!A2", "row": 2},
    ]
    single = tmp_path / "one.parquet"
    with RowGroupWriter(single, columns, 0) as writer:
        writer.write_lists([["P&L!A1", "P&L!A2"], [1, 2]])
    assert pq.ParquetFile(single).num_row_groups == 1
    assert read_parquet(single)[0]["row"] == 1


def test_newer_generation_skips_the_export_warning() -> None:
    stored = {"generation": 4, "status": "succeeded", "warnings": ["kept"]}
    assert _append_export_warning(stored, generation=3, warning="minio export failed") is stored
    same = {"generation": 3, "status": "succeeded", "warnings": ["minio export failed"]}
    assert _append_export_warning(same, generation=3, warning="minio export failed") is same
    updated = _append_export_warning(
        {"generation": 3, "status": "succeeded", "warnings": ["kept"]},
        generation=3,
        warning="minio export failed",
    )
    assert updated["status"] == "succeeded"
    assert updated["warnings"] == ["kept", "minio export failed"]


def test_pipeline_records_the_warning_and_does_not_export_a_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "job"
    dest.mkdir()
    source = build_xlsx(
        dest / "source" / "source.xlsx",
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2024", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="10"),
                ],
            )
        ],
        shared_strings=["Item", "2024", "Revenue"],
    )
    del source
    calls: list[str] = []
    published: list[str] = []

    def fake_export(settings: Settings, *, job_id: str, book: Path, dest: Path, store=None) -> str:
        del settings, book, dest, store
        calls.append(job_id)
        return "minio export failed"

    def fake_republish(
        settings: Settings, local: Path, job_id: str, relative: str, store=None
    ) -> None:
        del settings, local, job_id, store
        published.append(relative)

    monkeypatch.setattr("finance_context.app.pipeline.export_succeeded_book", fake_export)
    monkeypatch.setattr("finance_context.app.pipeline.republish_file", fake_republish)
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    pipeline.run(dest, job_id="job-test", source_filename="model.xlsx")
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "succeeded"
    assert meta["warnings"] == ["minio export failed"]
    assert (dest / "context" / "context.json").is_file()
    pipeline.run(dest, job_id="job-test")
    assert calls == ["job-test"]
    assert published == []
    (dest / "context" / "context.md.renderer").write_text("old\n", encoding="utf-8")
    pipeline.run(dest, job_id="job-test")
    assert calls == ["job-test"]
    assert published == ["context/context.md"]
    assert (dest / "context" / "context.md.renderer").read_text(encoding="utf-8") == (
        "transposed-cells\n"
    )

    def fail_republish(
        settings: Settings, local: Path, job_id: str, relative: str, store=None
    ) -> None:
        del settings, local, job_id, store
        published.append(relative)
        raise OSError("marker")

    monkeypatch.setattr("finance_context.app.pipeline.republish_file", fail_republish)
    (dest / "context" / "context.md.renderer").write_text("old\n", encoding="utf-8")
    pipeline.run(dest, job_id="job-test")
    assert (dest / "context" / "context.md.renderer").read_text(encoding="utf-8") == "old\n"
    pipeline.run(dest, job_id="job-test")
    assert published[-1] == "context/context.md"


def test_duckdb_batch_keeps_append_order_and_int64(tmp_path: Path) -> None:
    dest = tmp_path / "cells.parquet"
    batch = DuckDBBatch(
        Path(str(dest) + ".build.duckdb"),
        (("row", "INTEGER"), ("addr", "VARCHAR")),
    )
    batch.add_rows([(1, "A1"), (2, "A2")])
    batch.add_rows([(3, "A3")])
    batch.finish(dest, 0)
    table = pq.read_table(dest)
    assert table.schema.field("row").type == pa.int64()
    assert table.column("addr").to_pylist() == ["A1", "A2", "A3"]
    assert not Path(str(dest) + ".build.duckdb").exists()
    assert not Path(str(dest) + ".build.duckdb.wal").exists()


def test_duckdb_batch_set_formulas_writes_two_different_formulas(tmp_path: Path) -> None:
    dest = tmp_path / "cells.parquet"
    batch = DuckDBBatch(
        Path(str(dest) + ".build.duckdb"),
        (("sheet", "VARCHAR"), ("addr", "VARCHAR"), ("formula_raw", "VARCHAR")),
    )
    batch.add_rows([("P&L", "A2", None), ("P&L", "B2", None)])
    batch.set_formulas("P&L", ["A2", "B2"], ["A2", "$A$1"])
    batch.finish(dest, 0)
    rows = {row["addr"]: row["formula_raw"] for row in pq.read_table(dest).to_pylist()}
    assert rows == {"A2": "A2", "B2": "$A$1"}


def test_duckdb_batch_copy_failure_leaves_no_database(tmp_path: Path) -> None:
    blocked = tmp_path / "not-a-file"
    blocked.mkdir()
    db = tmp_path / "batch.duckdb"
    batch = DuckDBBatch(db, (("addr", "VARCHAR"),))
    batch.add_rows([("A1",)])
    with pytest.raises(OSError):
        batch.finish(blocked, 0)
    assert not db.exists()
    assert not Path(str(db) + ".wal").exists()
    assert not (tmp_path / "not-a-file.partial").exists()
    assert not (tmp_path / "not-a-file.parquet").exists()
    assert blocked.is_dir()
    with pytest.raises(RuntimeError, match="closed"):
        batch.finish(tmp_path / "later.parquet", 0)


def test_compose_documents_minio_without_starting_it_in_production() -> None:
    root = Path(__file__).resolve().parents[2]
    production = (root / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    override = (root / "deploy" / "docker-compose.override.yml").read_text(encoding="utf-8")
    dockerfile = (root / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    assert "image: minio/" not in production
    assert "${MINIO_" not in production
    assert "MINIO_ENDPOINT" in production
    for name in ("MINIO_ENDPOINT", "MINIO_BUCKET", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"):
        assert f"{name}:" in override
    assert "alpine/minio:RELEASE.2025-10-15T17-29-55Z" in override
    assert "minio-data:/data" in override
    assert "minio/health/live" in override
    assert "local/finance-context" in override
    assert "bitnamilegacy/" not in override
    assert "pgsty/" not in override
    assert dockerfile.index("USER app") < dockerfile.index("INSTALL httpfs")


def test_put_copies_in_chunks_and_size_does_not_read_the_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb
    from pyarrow.fs import FileType

    payload = b"a" * (_UPLOAD_CHUNK_BYTES + 3)
    local = tmp_path / "context.json"
    local.write_bytes(payload)
    written: list[bytes] = []
    built: list[object] = []

    class _Dest:
        def write(self, data: bytes) -> None:
            written.append(bytes(data))

        def close(self) -> None:
            return None

        def __enter__(self) -> _Dest:
            return self

        def __exit__(self, *_args: object) -> None:
            self.close()

    class _Info:
        type = FileType.File
        size = len(payload)

    class _Missing:
        type = FileType.NotFound
        size = None

    class _ChunkFS:
        def __init__(self) -> None:
            self.paths: list[str] = []

        def open_output_stream(self, path: str) -> _Dest:
            self.paths.append(path)
            return _Dest()

        def get_file_info(self, path: str) -> _Info | _Missing:
            self.paths.append("size:" + path)
            if path.endswith("/missing"):
                return _Missing()
            return _Info()

        def open_input_stream(self, path: str) -> None:
            raise AssertionError(path)

    def factory(settings: Settings) -> _ChunkFS:
        del settings
        filesystem = _ChunkFS()
        built.append(filesystem)
        return filesystem

    def connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("duckdb.connect")

    monkeypatch.setattr(duckdb, "connect", connect)
    monkeypatch.setattr("finance_context.store.duckdb_io._s3_filesystem", factory)
    store = DuckDBObjectStore(_minio())
    url = "s3://finance-context/team/a/books/job/context/context.json"
    other = "s3://finance-context/team/a/books/job/context/context.md"
    store.put(local, url)
    store.put(local, other)
    assert store.size(url) == len(payload)
    assert len(built) == 1
    assert built[0].paths == [
        "finance-context/team/a/books/job/context/context.json",
        "finance-context/team/a/books/job/context/context.md",
        "size:finance-context/team/a/books/job/context/context.json",
    ]
    assert written == [
        payload[:_UPLOAD_CHUNK_BYTES],
        payload[_UPLOAD_CHUNK_BYTES:],
        payload[:_UPLOAD_CHUNK_BYTES],
        payload[_UPLOAD_CHUNK_BYTES:],
    ]
    with pytest.raises(OSError, match="missing object"):
        store.size(url + "/missing")


def test_s3_filesystem_uses_path_style(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FS:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("pyarrow.fs.S3FileSystem", _FS)
    _s3_filesystem(
        _minio(minio_region="eu-central-1", minio_endpoint="https://s3.internal:9000/")
    )
    assert captured["endpoint_override"] == "s3.internal:9000"
    assert captured["scheme"] == "https"
    assert captured["region"] == "eu-central-1"
    assert captured["access_key"] == "minioadmin"
    assert captured["secret_key"] == _SECRET
    assert captured["force_virtual_addressing"] is False
    assert captured["background_writes"] is False
    http = {}

    class _HTTP:
        def __init__(self, **kwargs: object) -> None:
            http.update(kwargs)

    monkeypatch.setattr("pyarrow.fs.S3FileSystem", _HTTP)
    _s3_filesystem(_minio())
    assert http["endpoint_override"] == "minio:9000"
    assert http["scheme"] == "http"
    assert http["force_virtual_addressing"] is False


def test_published_read_streams_the_bucket_in_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import duckdb
    from pyarrow.fs import FileType

    local = tmp_path / "context" / "context.json"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"local-not-served")
    payload = b"b" * (_UPLOAD_CHUNK_BYTES + 5)
    marker = (
        json.dumps({"keys": [object_key("team/a", _JOB, "context/context.json")]}) + "\n"
    ).encode()

    def fake_read(uri: str, settings: Settings | None = None) -> bytes:
        del settings
        if uri.endswith("/_complete.json"):
            return marker
        raise AssertionError(uri)

    class _In:
        def __init__(self) -> None:
            self._offset = 0

        def read(self, size: int) -> bytes:
            chunk = payload[self._offset : self._offset + size]
            self._offset += len(chunk)
            return chunk

        def close(self) -> None:
            return None

    class _Info:
        type = FileType.File
        size = len(payload)

    class _FS:
        def get_file_info(self, path: str) -> _Info:
            del path
            return _Info()

        def open_input_stream(self, path: str) -> _In:
            del path
            return _In()

    def connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("duckdb.connect")

    monkeypatch.setattr(duckdb, "connect", connect)
    monkeypatch.setattr("finance_context.store.duckdb_io.read_blob_bytes", fake_read)
    monkeypatch.setattr("finance_context.store.duckdb_io._s3_filesystem", lambda settings: _FS())
    body = read_published_file(_minio(), local, _JOB, "context/context.json")
    assert body.content_length == len(payload)
    assert list(body) == [payload[:_UPLOAD_CHUNK_BYTES], payload[_UPLOAD_CHUNK_BYTES:]]


def test_bucket_read_logs_a_memory_error_and_hides_the_essay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    local = tmp_path / "context" / "context.json"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"local-bytes")
    marker = (
        json.dumps({"keys": [object_key("team/a", _JOB, "context/context.json")]}) + "\n"
    ).encode()

    def fake_read(uri: str, settings: Settings | None = None) -> bytes:
        del settings
        if uri.endswith("/_complete.json"):
            return marker
        return Path(uri).read_bytes()

    monkeypatch.setattr("finance_context.store.duckdb_io.read_blob_bytes", fake_read)

    def out_of_memory(settings: Settings, url: str) -> PublishedFile:
        del settings, url
        raise _OutOfMemory("Out of Memory Error: failed to allocate data of size 1.0 GiB")

    monkeypatch.setattr("finance_context.store.duckdb_io._open_published_object", out_of_memory)
    caplog.set_level(logging.WARNING, logger="finance_context.store")
    assert _body(read_published_file(_minio(), local, _JOB, "context/context.json")) == (
        b"local-bytes"
    )
    assert "minio read failed for context/context.json: OutOfMemoryException:" in caplog.text

    def denied(settings: Settings, url: str) -> PublishedFile:
        del settings, url
        raise RuntimeError(_DENIED + _SECRET)

    monkeypatch.setattr("finance_context.store.duckdb_io._open_published_object", denied)
    caplog.clear()
    assert _body(read_published_file(_minio(), local, _JOB, "context/context.json")) == (
        b"local-bytes"
    )
    assert "HTTP 403 AccessDenied: Access Denied." in caplog.text
    assert "Authentication Failure" not in caplog.text
    assert _SECRET not in caplog.text


def _error_server(status: int, code: str, message: str) -> tuple[object, dict[str, int]]:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    payload = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Error><Code>{code}</Code><Message>{message}</Message></Error>"
    ).encode()
    counts = {"PUT": 0, "POST": 0, "GET": 0, "HEAD": 0, "DELETE": 0}

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self) -> None:
            counts[self.command] = counts.get(self.command, 0) + 1
            self._drain()
            length = 0 if self.command == "HEAD" else len(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/xml")
            self.send_header("Content-Length", str(length))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

        def _drain(self) -> None:
            encoding = self.headers.get("Transfer-Encoding", "")
            if "chunked" in encoding.lower():
                while True:
                    line = self.rfile.readline()
                    size = int(line.split(b";", 1)[0] or b"0", 16)
                    if size == 0:
                        while True:
                            trailer = self.rfile.readline()
                            if trailer in {b"\r\n", b"\n", b""}:
                                break
                        break
                    self.rfile.read(size)
                    self.rfile.read(2)
                return
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length:
                self.rfile.read(length)

        def handle_expect_100(self) -> bool:
            self.send_response_only(100)
            self.end_headers()
            return True

        do_PUT = do_GET = do_POST = do_HEAD = do_DELETE = _reply

        def log_message(self, fmt: str, *args: object) -> None:
            del fmt, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, counts


def test_pyarrow_access_denied_skips_the_second_marker_put(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("finance_context.store.duckdb_io.time.sleep", lambda _seconds: None)
    server, counts = _error_server(403, "AccessDenied", "Access Denied.")
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    raw: list[BaseException] = []
    try:
        port = server.server_address[1]
        settings = _minio(minio_endpoint=f"http://127.0.0.1:{port}")
        store = DuckDBObjectStore(settings)
        original = store.put

        def put(local: Path, url: str) -> None:
            try:
                original(local, url)
            except Exception as exc:
                raw.append(exc)
                raise

        store.put = put  # type: ignore[method-assign]
        caplog.set_level(logging.WARNING, logger="finance_context.store")
        assert (
            export_succeeded_book(settings, job_id=_JOB, book=book, dest=dest, store=store)
            == "minio export failed"
        )
    finally:
        server.shutdown()
    detail = str(raw[0]) if raw else caplog.text
    # The marker read is a HEAD, so the XML message is not in the error.
    assert "HTTP 403 AccessDenied: No response body." in caplog.text, detail
    assert "Authentication Failure" not in caplog.text
    assert _SECRET not in caplog.text
    assert "minio export failed for _complete.json" not in caplog.text
    # AccessDenied is not retried, and a 403 does not write the marker again.
    assert sum(counts.values()) == 1, (counts, detail)


def test_pyarrow_http_500_retries_without_the_essay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("finance_context.store.duckdb_io.time.sleep", lambda _seconds: None)
    server, counts = _error_server(500, "SlowDown", "Please reduce your request rate.")
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    raw: list[BaseException] = []
    try:
        port = server.server_address[1]
        settings = _minio(minio_endpoint=f"http://127.0.0.1:{port}")
        store = DuckDBObjectStore(settings)
        original = store.put

        def put(local: Path, url: str) -> None:
            try:
                original(local, url)
            except Exception as exc:
                raw.append(exc)
                raise

        store.put = put  # type: ignore[method-assign]
        caplog.set_level(logging.WARNING, logger="finance_context.store")
        assert (
            export_succeeded_book(settings, job_id=_JOB, book=book, dest=dest, store=store)
            == "minio export failed"
        )
    finally:
        server.shutdown()
    detail = str(raw[0]) if raw else caplog.text
    # HeadObject has no XML body, so the status is logged without the SlowDown name.
    assert "HTTP 500: No response body." in caplog.text, detail
    assert "Authentication Failure" not in caplog.text
    assert _SECRET not in caplog.text
    assert counts["HEAD"] >= _EXPORT_ATTEMPTS, (counts, detail)
    assert counts["PUT"] + counts["POST"] == 0, (counts, detail)


def test_export_logs_its_own_duration(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    book = tmp_path / "book"
    dest = tmp_path / "dest"
    _fill(book, dest)
    caplog.set_level(logging.INFO, logger="finance_context.store")
    assert (
        export_succeeded_book(
            _minio(),
            job_id=_JOB,
            book=book,
            dest=dest,
            store=_MemoryStore(),
        )
        is None
    )
    events = [getattr(record, "event", None) for record in caplog.records]
    assert "export_start" in events
    assert "export_done" in events
    done = next(
        record for record in caplog.records if getattr(record, "event", None) == "export_done"
    )
    assert done.ok is True
    assert isinstance(done.duration_ms, int)
    assert done.objects == len(_SHARED) + len(_SESSION)

    caplog.clear()
    assert (
        export_succeeded_book(
            Settings(_env_file=None),
            job_id=_JOB,
            book=book,
            dest=dest,
            store=_MemoryStore(),
        )
        is None
    )
    assert [getattr(record, "event", None) for record in caplog.records] == []


def test_document_response_streams_with_content_length(tmp_path: Path) -> None:
    import anyio
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from finance_context.api.routes.documents import _document_response

    payload = b"z" * (_UPLOAD_CHUNK_BYTES + 9)
    path = tmp_path / "context.json"
    path.write_bytes(payload)

    class _Ctx:
        thread_limiter: object

    app = FastAPI()

    @app.get("/context.json")
    async def get_context() -> object:
        ctx = _Ctx()
        ctx.thread_limiter = anyio.to_thread.current_default_thread_limiter()
        return _document_response(ctx, stream_local_file(path), "application/json")

    with TestClient(app) as client:
        response = client.get("/context.json")
    assert response.status_code == 200
    assert response.headers["content-length"] == str(len(payload))
    assert response.content == payload
    assert response.headers["content-type"].startswith("application/json")
