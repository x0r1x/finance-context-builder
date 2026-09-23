from __future__ import annotations

import logging
from pathlib import Path

from tests.helpers.ports import FakeChat, FakeEmbed, GrantSlots
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline, _timed, run_job_process
from finance_context.excel import parse_workbook
from finance_context.settings import Settings

VECS = {
    "revenue": [1.0, 0.0, 0.0],
    "выручка": [1.0, 0.0, 0.0],
    "sales": [1.0, 0.0, 0.0],
}


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
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    embed = FakeEmbed(VECS)
    pipeline = Pipeline(
        Settings(data_dir=tmp_path / "data"),
        embed=embed,
        chat=FakeChat("pnl.revenue"),
        slots=GrantSlots(),
    )
    doc = pipeline.run(dest, job_id="job-test", source_filename="model.xlsx")
    assert (dest / "context.json").is_file()
    assert (dest / "graph.json").is_file()
    assert (dest / "graph.md").is_file()
    assert not (dest / "graph-edges.json").exists()
    assert not (dest / "graph-dangling.json").exists()
    assert not (dest / "formulas.json").exists()
    assert (dest / "context.md").is_file()
    assert doc.workbook.cell_count >= 1
    sheets = {row.sheet for block in doc.blocks for row in block.rows}
    assert "P&L" in sheets
    again = pipeline.run(dest, job_id="job-test")
    assert again.meta.job_id == doc.meta.job_id


def test_run_job_process_emits_stage_done_on_stdout(tmp_path: Path, capsys, monkeypatch) -> None:
    logger = logging.getLogger("finance_context")
    saved = (list(logger.handlers), logger.level, logger.propagate)
    monkeypatch.setattr(Settings, "embed", lambda self: None)
    monkeypatch.setattr(Settings, "chat", lambda self: None)

    def fake_run(self, dest, *, job_id, **kwargs):
        _timed("parse", lambda: None)

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
