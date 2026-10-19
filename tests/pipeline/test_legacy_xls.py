"""Legacy `.xls` through the pipeline and a job that fails with a known error code."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.helpers.converter import FakeConverter
from tests.helpers.ole2 import XLS_STREAMS, write_ole2
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline, run_job_process
from finance_context.errors import ContextError
from finance_context.settings import Settings
from finance_context.store.paths import session_job_dir


def _prepared(path: Path) -> Path:
    return build_xlsx(
        path,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024E", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="100"),
                    CellSpec(addr="C2", value="110", formula="=B2+10"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "2024E", "Revenue"],
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", _env_file=None)


def test_legacy_xls_is_converted_before_parse(tmp_path: Path, dest: Path) -> None:
    write_ole2(dest / "source.xls", XLS_STREAMS)
    converter = FakeConverter(_prepared(tmp_path / "prepared.xlsx"))
    doc = Pipeline(_settings(tmp_path), converter=converter).run(
        dest, job_id="job-xls", source_filename="model.xls"
    )
    assert doc.meta.status == "succeeded"
    assert (dest / "source.xlsx").is_file()
    assert (dest / "source.xls").is_file()
    assert converter.calls == [dest / "source.xls"]
    labels = {row.label for block in doc.blocks for row in block.rows}
    assert "Revenue" in labels


def test_legacy_xls_without_libreoffice_fails_cleanly(tmp_path: Path, dest: Path) -> None:
    write_ole2(dest / "source.xls", XLS_STREAMS)
    pipeline = Pipeline(_settings(tmp_path), converter=FakeConverter(available=False))
    with pytest.raises(ContextError) as ei:
        pipeline.run(dest, job_id="job-xls", source_filename="model.xls")
    assert ei.value.code == "unsupported_media_type"
    meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert meta["stage"] == "parse"


def _fail_with(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def fake_run(self, dest, **kwargs):
        raise error

    monkeypatch.setattr(Pipeline, "run", fake_run)


def test_job_process_records_the_error_code(tmp_path: Path, monkeypatch) -> None:
    _fail_with(monkeypatch, ContextError("zip_rejected", "not a zip"))
    run_job_process(str(tmp_path), "job-bad", 2)
    meta = json.loads(
        (session_job_dir(tmp_path, "job-bad") / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["status"] == "failed"
    assert meta["error"] == "zip_rejected"
    assert meta["generation"] == 2


def test_job_process_keeps_raising_an_unexpected_error(tmp_path: Path, monkeypatch) -> None:
    _fail_with(monkeypatch, RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        run_job_process(str(tmp_path), "job-bug")
    meta = json.loads(
        (session_job_dir(tmp_path, "job-bug") / "meta.json").read_text(encoding="utf-8")
    )
    assert meta["status"] == "failed"
    assert meta["error"] == "RuntimeError"
