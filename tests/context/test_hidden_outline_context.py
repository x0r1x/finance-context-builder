from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx
from tests.synthetic.support import run_book


@pytest.mark.parametrize("streaming", [True, False])
@pytest.mark.parametrize("summary_below", [True, False])
def test_only_rows_inside_a_collapsed_outline_get_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, streaming: bool, summary_below: bool
) -> None:
    monkeypatch.setenv("PARSE_STREAMING", str(streaming))
    summary = 6 if summary_below else 2
    sheet = SheetSpec(
        name="Model",
        hidden_rows=[3, 4, 5, 8, 9],
        row_outline={3: 1, 4: 2, 5: 1, 8: 1},
        collapsed_rows={summary},
        summary_below=summary_below,
        cells=[
            CellSpec("A1", "Item", type="inlineStr"),
            CellSpec("B1", "2024", type="inlineStr"),
            CellSpec("C1", "2025", type="inlineStr"),
            *[
                cell
                for row in range(2, 10)
                for cell in (
                    CellSpec(f"A{row}", f"Item {row}", type="inlineStr"),
                    CellSpec(f"B{row}", str(row * 2)),
                    CellSpec(f"C{row}", str(row * 3)),
                )
            ],
        ],
    )
    source = build_xlsx(tmp_path / "outline.xlsx", sheets=[sheet])
    dest = tmp_path / "job"
    run_book(source, dest, filename=source.name, job_id="outline")
    payload = json.loads((dest / "context/context.json").read_text(encoding="utf-8"))
    rows = {row["row"]: row for block in payload["blocks"] for row in block["rows"]}
    assert {n for n, row in rows.items() if row["hidden"]} == {3, 4, 5, 8, 9}
    assert {n for n, row in rows.items() if row.get("hidden_by_outline")} == {3, 4, 5}
    assert rows[4]["values"] == ["8", "12"]
    assert "hidden_by_outline" not in rows[8]
    assert "hidden_by_outline" not in rows[9]
