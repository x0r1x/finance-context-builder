from __future__ import annotations

from pathlib import Path

from tests.helpers.ports import FakeChat, FakeEmbed, GrantSlots
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
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
