from __future__ import annotations

from finance_context.context.build import build_context
from finance_context.context.timeline import build_axes
from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout
from finance_context.mapping.models import MappingDocument
from finance_context.render.markdown import render_markdown


def _c(sheet: str, addr: str, value: str | None) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": False,
        "formula_raw": None,
        "formula_template": None,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }


def test_flag_rows_set_construction_then_operation_phase() -> None:
    cells = [
        _c("TBA", "A1", "Year"),
        _c("TBA", "B1", "1"),
        _c("TBA", "C1", "2"),
        _c("TBA", "D1", "3"),
        _c("TBA", "A2", "Construction"),
        _c("TBA", "B2", "1"),
        _c("TBA", "C2", "1"),
        _c("TBA", "D2", "0"),
        _c("TBA", "A3", "Operation"),
        _c("TBA", "B3", "0"),
        _c("TBA", "C3", "0"),
        _c("TBA", "D3", "1"),
        _c("TBA", "A4", "Revenue"),
        _c("TBA", "B4", "10"),
        _c("TBA", "C4", "20"),
        _c("TBA", "D4", "30"),
        _c("TBA", "A5", "Beginning of construction"),
        _c("TBA", "B5", "1"),
        _c("TBA", "C5", "0"),
        _c("TBA", "D5", "0"),
        _c("TBA", "A6", "Active"),
        _c("TBA", "B6", "1"),
        _c("TBA", "C6", "1"),
        _c("TBA", "D6", "0"),
    ]
    layout = detect_layout(cells)
    kinds = {row.label: row.kind for row in layout.sheets[0].blocks[0].rows}
    assert kinds["Construction"] == "flag"
    assert kinds["Operation"] == "flag"
    axes, warnings = build_axes(layout, cells)
    assert axes
    assert not warnings
    by_id = {item.period_key: item for item in axes[0].periods}
    assert by_id["Y1"].phase == "construction" and by_id["Y1"].phase_year == 1
    assert by_id["Y2"].phase == "construction" and by_id["Y2"].phase_year == 2
    assert by_id["Y3"].phase == "operation" and by_id["Y3"].phase_year == 1
    assert by_id["Y1"].flags["construction"] is True
    assert by_id["Y3"].flags["operation"] is True
    assert by_id["Y1"].flags["beginning of construction"] is True
    assert by_id["Y1"].calendar_year is None
    doc = build_context(
        job_id="t",
        workbook_meta={"sheets": [{"name": "TBA"}]},
        cells=cells,
        layout=layout,
        mapping=MappingDocument(),
    )
    revenue_block = next(block for block in doc.blocks if block.sheet == "TBA")
    assert revenue_block.periods == []
    rendered = render_markdown(doc)
    assert "## Axes" in rendered
    assert "construction" in rendered
    flag_rows = [row for block in doc.blocks for row in block.rows if row.kind == "flag"]
    assert flag_rows
    assert all(row.concept_id is None for row in flag_rows)


def test_calendar_book_timeline_has_no_phase() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2020"),
        _c("P&L", "C1", "2021"),
        _c("P&L", "D1", "2022"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "1"),
        _c("P&L", "C2", "2"),
        _c("P&L", "D2", "3"),
    ]
    layout = detect_layout(cells)
    axes, warnings = build_axes(layout, cells)
    assert len(axes) == 1
    assert not warnings
    assert axes[0].grain == "year"
    assert all(item.phase is None for item in axes[0].periods)
    assert [item.calendar_year for item in axes[0].periods] == ["2020", "2021", "2022"]


def test_duration_mismatch_warns_without_overriding_flags() -> None:
    cells = [
        _c("TBA", "A1", "Year"),
        _c("TBA", "B1", "1"),
        _c("TBA", "C1", "2"),
        _c("TBA", "D1", "3"),
        _c("TBA", "A2", "Construction"),
        _c("TBA", "B2", "1"),
        _c("TBA", "C2", "1"),
        _c("TBA", "D2", "0"),
        _c("TBA", "A3", "Operation"),
        _c("TBA", "B3", "0"),
        _c("TBA", "C3", "0"),
        _c("TBA", "D3", "1"),
        _c("TBA", "A4", "Revenue"),
        _c("TBA", "B4", "10"),
        _c("TBA", "C4", "20"),
        _c("TBA", "D4", "30"),
        _c("Input Assumptions", "B5", "Parameter"),
        _c("Input Assumptions", "C5", "Units"),
        _c("Input Assumptions", "D5", "Values"),
        _c("Input Assumptions", "B8", "Construction Duration"),
        _c("Input Assumptions", "C8", "years"),
        _c("Input Assumptions", "D8", "5"),
        _c("Input Assumptions", "B9", "Operations Duration"),
        _c("Input Assumptions", "C9", "years"),
        _c("Input Assumptions", "D9", "1"),
        _c("Input Assumptions", "B10", "Concession Duration"),
        _c("Input Assumptions", "C10", "years"),
        _c("Input Assumptions", "D10", "10"),
        _c("Input Assumptions", "B11", "Tax Rate"),
        _c("Input Assumptions", "C11", "%"),
        _c("Input Assumptions", "D11", "0.3"),
    ]
    layout = detect_layout(cells)
    axes, warnings = build_axes(layout, cells)
    assert axes
    assert [item.phase for item in axes[0].periods] == [
        "construction",
        "construction",
        "operation",
    ]
    assert any("Construction Duration" in item for item in warnings)
    assert any("Concession Duration" in item for item in warnings)
    assert not any("Operations Duration" in item for item in warnings)
