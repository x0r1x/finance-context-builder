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
    DuckDBBatch,
    export_succeeded_book,
    object_key,
    read_blob_bytes,
    read_published_file,
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
        self.fail = fail
        self.mismatch = mismatch

    def put(self, local: Path, url: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.objects[url] = local.read_bytes()
        self.order.append(url)

    def size(self, url: str) -> int:
        if self.mismatch:
            return 0
        return len(self.objects[url])


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
    (dest / "context" / "context.md.renderer").write_text("structure-columns\n", encoding="utf-8")


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
    assert store.order == []
    assert (dest / "meta" / "meta.json").is_file()
    mismatch = _MemoryStore(mismatch=True)
    assert (
        export_succeeded_book(_minio(), job_id=_JOB, book=book, dest=dest, store=mismatch)
        == "minio export failed"
    )
    assert all(not url.endswith("/_complete.json") for url in mismatch.order)


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
    assert body == b'{"ok": true}\n'


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

    def fake_export(settings: Settings, *, job_id: str, book: Path, dest: Path, store=None) -> str:
        del settings, book, dest, store
        calls.append(job_id)
        return "minio export failed"

    monkeypatch.setattr("finance_context.app.pipeline.export_succeeded_book", fake_export)
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    pipeline.run(dest, job_id="job-test", source_filename="model.xlsx")
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "succeeded"
    assert meta["warnings"] == ["minio export failed"]
    assert (dest / "context" / "context.json").is_file()
    pipeline.run(dest, job_id="job-test")
    assert calls == ["job-test"]


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
