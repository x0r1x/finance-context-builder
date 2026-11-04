"""A block unit caption is RURm in the label, not Year or a currency's name."""

from __future__ import annotations

from tests.helpers.published import published_context as build_context

from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout
from finance_context.layout.units import is_block_unit_caption


def _c(sheet: str, addr: str, value: str) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": False,
        "formula_raw": None,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }


def test_block_unit_caption_rejects_a_timeline_word_and_a_currency_name() -> None:
    assert is_block_unit_caption("RURm")
    assert is_block_unit_caption("RUR")
    assert is_block_unit_caption("k£")
    assert is_block_unit_caption("EUR'000")
    assert is_block_unit_caption("тыс. руб.")
    assert not is_block_unit_caption("Year")
    assert not is_block_unit_caption("years")
    assert not is_block_unit_caption("Euro")
    assert not is_block_unit_caption("Cash Flow Summary")


def test_year_header_and_euro_line_do_not_paint_the_following_rows() -> None:
    cells = [
        _c("P&L", "B1", "Year"),
        _c("P&L", "C1", "2020"),
        _c("P&L", "D1", "2021"),
        _c("P&L", "E1", "2022"),
        _c("P&L", "B2", "Revenue"),
        _c("P&L", "C2", "10"),
        _c("P&L", "D2", "11"),
        _c("P&L", "E2", "12"),
        _c("P&L", "B3", "Euro"),
        _c("P&L", "C3", "1"),
        _c("P&L", "D3", "1"),
        _c("P&L", "E3", "1"),
        _c("P&L", "B4", "Tolerances"),
        _c("P&L", "C4", "2"),
        _c("P&L", "D4", "2"),
        _c("P&L", "E4", "2"),
        _c("CF", "B1", "Item"),
        _c("CF", "D1", "2020"),
        _c("CF", "E1", "2021"),
        _c("CF", "F1", "2022"),
        _c("CF", "B2", "Сырье"),
        _c("CF", "C2", "RUR"),
        _c("CF", "D2", "10"),
        _c("CF", "E2", "11"),
        _c("CF", "F2", "12"),
    ]
    layout = detect_layout(cells)
    doc = build_context(job_id="banner", workbook_meta={"sheets": []}, cells=cells, layout=layout)
    lines = {(row.sheet, row.label): row for block in doc.blocks for row in block.rows}
    revenue = lines[("P&L", "Revenue")]
    assert revenue.hints.unit is None
    assert revenue.hints.currency is None
    tolerances = lines[("P&L", "Tolerances")]
    assert tolerances.hints.currency is None
    assert tolerances.hints.unit is None
    raw = lines[("CF", "Сырье")]
    assert (raw.hints.currency, raw.hints.scale, raw.scale_factor) == ("RUB", "unit", 1)


def test_in_section_unit_row_stops_at_the_next_section() -> None:
    cells = [
        _c("Debt", "B1", "Item"),
        _c("Debt", "C1", "2020"),
        _c("Debt", "D1", "2021"),
        _c("Debt", "E1", "2022"),
        _c("Debt", "B2", "Senior Debt"),
        _c("Debt", "B3", "% p.a."),
        _c("Debt", "B4", "All-in interest rate"),
        _c("Debt", "C4", "0.05"),
        _c("Debt", "D4", "0.05"),
        _c("Debt", "E4", "0.05"),
        _c("Debt", "B5", "Debt summary"),
        _c("Debt", "B6", "Interest"),
        _c("Debt", "C6", "1"),
        _c("Debt", "D6", "1"),
        _c("Debt", "E6", "1"),
    ]
    layout = detect_layout(cells)
    doc = build_context(job_id="banner", workbook_meta={"sheets": []}, cells=cells, layout=layout)
    lines = {row.label: row for block in doc.blocks for row in block.rows}
    assert lines["All-in interest rate"].hints.unit == "rate"
    assert lines["Debt summary"].hints.unit is None
    assert lines["Interest"].hints.unit is None
