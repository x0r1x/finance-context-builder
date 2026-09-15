from __future__ import annotations

from pathlib import Path

import pytest

from finance_context.excel.stage import parse_workbook
from finance_context.formulas.engine import FormulaEngine
from finance_context.formulas.stage import compile_workbook
from finance_context.layout.stage import layout_workbook
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.taxonomy import load_taxonomy
from finance_context.store.fs import read_parquet

XLSX = Path("/Users/alekseykashin/projects/finance-context-builder/resources/cashflow.xlsx")


@pytest.mark.skipif(not XLSX.is_file(), reason="cashflow.xlsx fixture missing")
def test_cashflow_layout_weeks_and_row_kinds(tmp_path: Path) -> None:
    dest = tmp_path / "job"
    dest.mkdir()
    parse_workbook(XLSX, dest)
    compile_workbook(dest)
    layout = layout_workbook(dest)
    weekly_blocks = [
        block
        for sheet in layout.sheets
        for block in sheet.blocks
        if len({h.period_key for h in block.axis.headers}) >= 13
    ]
    assert weekly_blocks
    weekly = weekly_blocks[0]
    keys = [h.period_key for h in weekly.axis.headers]
    assert len(set(keys)) == len(keys)
    fact_labels = [row.label for row in weekly.rows if row.kind == "fact"]
    assert all("week #" not in label.casefold() for label in fact_labels)

    cells = read_parquet(dest / "ir" / "cells.parquet")
    by_ref = {(c["sheet"], c["addr"]): c for c in cells}
    for sheet, addr in (("Cover", "C20"), ("Dashboard", "H11")):
        cell = by_ref.get((sheet, addr))
        if cell is None or not cell.get("formula_raw"):
            continue
        assert not cell.get("unparsed"), cell.get("formula_raw")
        if "Sub_Growth_M" in str(cell.get("formula_raw")):
            assert cell.get("formula_template") and "(1+Sub_Growth_M)" in str(
                cell.get("formula_template")
            )

    engine = FormulaEngine(locale_hint="en")
    growth = [
        cell
        for cell in cells
        if cell.get("formula_raw") and "Sub_Growth_M" in str(cell.get("formula_raw"))
    ]
    if growth:
        parsed = engine.parse(
            str(growth[0]["formula_raw"]),
            sheet=str(growth[0]["sheet"]),
            addr=str(growth[0]["addr"]),
        )
        assert parsed.template is None or "(1+Sub_Growth_M)" in parsed.template or parsed.unparsed

    doc = map_layout(
        layout,
        taxonomy=load_taxonomy(),
        glossary={},
        cells=cells,
        embed=None,
        chat=None,
    )
    mapped_labels = {row.label.casefold() for row in doc.rows}
    assert "week #" not in mapped_labels
    assert not any("helper" in label for label in mapped_labels)
    sources = {row.source for row in doc.rows if row.concept_id}
    assert "chat" not in sources
