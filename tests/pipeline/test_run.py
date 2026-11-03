from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

import finance_context.app.pipeline as pipeline_mod
from finance_context.app.pipeline import Pipeline, _emit_stage, _stage, run_job_process
from finance_context.excel import parse_workbook
from finance_context.jobqueue.progress import enable_progress, reset_progress
from finance_context.observability import JsonFormatter
from finance_context.settings import Settings


def _model(path: Path) -> Path:
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


def test_pipeline_writes_json_and_markdown(tmp_path: Path, dest: Path) -> None:
    source = _model(tmp_path / "model.xlsx")
    parse_workbook(source, dest)
    (dest / "source" / "source.xlsx").parent.mkdir(parents=True, exist_ok=True)
    (dest / "source" / "source.xlsx").write_bytes(source.read_bytes())
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    doc = pipeline.run(dest, job_id="job-test", source_filename="model.xlsx")
    assert (dest / "context" / "context.json").is_file()
    assert (dest / "graph" / "graph.json").is_file()
    assert (dest / "graph" / "graph.md").is_file()
    assert not (dest / "graph-edges.json").exists()
    assert not (dest / "graph-dangling.json").exists()
    assert not (dest / "formulas.json").exists()
    assert (dest / "context" / "context.md").is_file()
    assert doc.workbook.cell_count >= 1
    sheets = {row.sheet for block in doc.blocks for row in block.rows}
    assert "P&L" in sheets
    again = pipeline.run(dest, job_id="job-test")
    assert again.meta.job_id == doc.meta.job_id
    document = json.loads((dest / "context" / "context.json").read_text(encoding="utf-8"))
    assert "generation" not in document["meta"]
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert meta["generation"] == 0
    assert set(meta["stages"]) == {"compile", "layout", "graph", "publish"}


def test_run_job_process_emits_stage_done_on_stdout(tmp_path: Path, capsys, monkeypatch) -> None:
    logger = logging.getLogger("finance_context")
    saved = (list(logger.handlers), logger.level, logger.propagate)

    def fake_run(self, dest, *, job_id, **kwargs):
        with _stage("parse", root=Path(dest), saved=()):
            return None

    monkeypatch.setattr(Pipeline, "run", fake_run)
    try:
        logger.handlers.clear()
        logger.propagate = True
        run_job_process(str(tmp_path), "job-child")
        captured = capsys.readouterr().out
    finally:
        logger.handlers[:] = saved[0]
        logger.setLevel(saved[1])
        logger.propagate = saved[2]
    assert '"event": "stage_done"' in captured
    assert '"stage": "parse"' in captured


def test_relations_stage_is_the_job_status(tmp_path: Path, dest: Path, monkeypatch) -> None:
    source = _model(tmp_path / "model.xlsx")
    target = dest / "source" / "source.xlsx"
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    seen: dict[str, object] = {}
    real = pipeline_mod.formula_relations

    def spy(layout, cells):
        meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
        seen["stage"] = meta["stage"]
        seen["warnings"] = meta["warnings"]
        return real(layout, cells)

    monkeypatch.setattr(pipeline_mod, "formula_relations", spy)
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    pipeline.run(dest, job_id="job-test", source_filename="model.xlsx")
    assert seen["stage"] == "relations"
    assert seen["warnings"] == []


def test_relations_keeps_the_open_layout_event(monkeypatch) -> None:
    events: list[dict[str, object]] = []

    def record(settings, **fields):
        events.append(fields)

    monkeypatch.setattr("finance_context.app.pipeline.emit_progress", record)
    token = enable_progress()
    try:
        settings = Settings(_env_file=None)
        open_stage = _emit_stage(
            settings, job_id="job-test", generation=0, stage="layout", open_stage=None
        )
        still = _emit_stage(
            settings, job_id="job-test", generation=0, stage="relations", open_stage=open_stage
        )
        nxt = _emit_stage(
            settings, job_id="job-test", generation=0, stage="graph", open_stage=still
        )
    finally:
        reset_progress(token)
    assert open_stage == "layout"
    assert still == "layout"
    assert nxt == "graph"
    assert [(row["event"], row["stage"]) for row in events] == [
        ("stage_start", "layout"),
        ("stage_done", "layout"),
        ("stage_start", "graph"),
    ]


def _log_lines(run) -> list[dict]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("finance_context.pipeline")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        run()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_pipeline_logs_each_stage_with_the_filename_and_saved_paths(
    tmp_path: Path, dest: Path
) -> None:
    source = _model(tmp_path / "model.xlsx")
    target = dest / "source" / "source.xlsx"
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    first = _log_lines(lambda: pipeline.run(dest, job_id="job-test", source_filename="model.xlsx"))
    done = {row["stage"]: row for row in first if row["event"] == "stage_done"}
    assert list(done) == [
        "parse",
        "compile",
        "layout",
        "relations",
        "graph",
        "build",
        "render",
    ]
    assert done["relations"]["saved"] == []
    assert isinstance(done["relations"]["cells"], int)
    for key in ("duration_ms", "rss_in_mb", "rss_peak_mb", "rss_out_mb"):
        assert key in done["relations"]
    for stage in done:
        assert any(row["event"] == "stage_start" and row["stage"] == stage for row in first)
        row = done[stage]
        assert row["source_filename"] == "model.xlsx"
        assert row["job_id"] == "job-test"
        assert Path(row["dir"]).is_absolute()
    assert "raw/cells.parquet" in done["parse"]["saved"]
    assert "raw/cell_presence.parquet" in done["parse"]["saved"]
    assert "raw/workbook.json" in done["parse"]["saved"]
    assert "ir/cells.parquet" in done["compile"]["saved"]
    assert "ir/edges.parquet" in done["compile"]["saved"]
    assert "ir/cell_edges.parquet" in done["compile"]["saved"]
    assert done["layout"]["saved"] == ["layout/layout.json"]
    assert "graph/graph.json" in done["graph"]["saved"]
    assert "graph/graph.md" in done["graph"]["saved"]
    assert "graph/index.parquet" in done["graph"]["saved"]
    assert "graph/edges.parquet" in done["graph"]["saved"]
    assert done["build"]["saved"] == ["context/context.json"]
    assert done["render"]["saved"] == ["context/context.md"]

    (dest / "owner.json").write_text(
        json.dumps({"source_filename": "model.xlsx"}), encoding="utf-8"
    )
    second = _log_lines(lambda: pipeline.run(dest, job_id="job-test"))
    reused = [row for row in second if row["event"] == "job_reused"]
    assert len(reused) == 1
    assert reused[0]["source_filename"] == "model.xlsx"
    assert reused[0]["job_id"] == "job-test"
    for name in (
        "context/context.json",
        "context/context.md",
        "graph/graph.json",
        "graph/graph.md",
    ):
        assert name in reused[0]["saved"]
    assert not any(row["event"] == "stage_start" and row["stage"] == "parse" for row in second)
