"""Indent parent is the nearest shallower non-unit row.

A flat table used to scan every previous row and run the unit-text regex on
each. These cases lock the parent that scan produced, and the call count locks
the linear replacement.
"""

from __future__ import annotations

from finance_context.excel.a1 import parse_addr
from finance_context.layout import rows as row_mod
from finance_context.layout.detect import detect_layout
from finance_context.layout.rows import _data_rows


def _c(
    sheet: str,
    addr: str,
    value: str | None,
    *,
    style: int = 0,
    outline: int = 0,
) -> dict:
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
        "style_indent": style,
        "outline_level": outline,
    }


def _years(sheet: str) -> list[dict]:
    return [_c(sheet, "B1", "2020"), _c(sheet, "C1", "2021")]


def _line(sheet: str, row: int, label: str, value: str | None, **kw) -> list[dict]:
    cells = [_c(sheet, f"A{row}", label, **kw)]
    if value is not None:
        cells.append(_c(sheet, f"B{row}", value))
        cells.append(_c(sheet, f"C{row}", "20"))
    return cells


def _parents(
    cells: list[dict], summary_below: dict[str, bool] | None = None
) -> dict[str, str | None]:
    layout = detect_layout(cells, summary_below=summary_below)
    out: dict[str, str | None] = {}
    for sheet in layout.sheets:
        by_row = {row.row: row.label for block in sheet.blocks for row in block.rows}
        for block in sheet.blocks:
            for row in block.rows:
                out[row.label] = by_row.get(row.parent_row) if row.parent_row else None
    return out


def test_indent_parent_skips_unit_text_and_section_boundaries() -> None:
    """Deeper, shallower, equal, unit-text, and a new section."""
    cells = [_c("S", "A1", "Item"), *_years("S")]
    specs = [
        (2, "Balance Sheet", None, 0),
        (3, "Current", None, 1),
        (4, "Cash", "10", 2),
        (5, "k£", None, 0),
        (6, "Deposits", "40", 2),
        (7, "Other", "15", 1),
        (8, "Total", "50", 1),
        (9, "Income Statement", None, 0),
        (10, "Revenue", "90", 0),
        (11, "Costs", "30", 1),
        (12, "Tax", "80", 0),
    ]
    for row, label, value, style in specs:
        cells.extend(_line("S", row, label, value, style=style))
    parents = _parents(cells)
    assert parents["Cash"] == "Current"
    assert parents["Deposits"] == "Current"
    assert parents["k£"] is None
    assert parents["Other"] == "Balance Sheet"
    assert parents["Total"] == "Current"
    assert parents["Income Statement"] is None
    assert parents["Revenue"] == "Income Statement"
    assert parents["Costs"] == "Revenue"
    assert parents["Tax"] == "Income Statement"


def test_equal_indent_rows_have_no_indent_parent() -> None:
    cells = [_c("E", "A1", "Item"), *_years("E")]
    for row, label in enumerate(("Alpha", "Beta", "Gamma"), start=2):
        cells.extend(_line("E", row, label, "10"))
    parents = _parents(cells)
    assert parents == {"Alpha": None, "Beta": None, "Gamma": None}


def test_outline_summary_ignores_unit_text_and_resets_at_the_next_section() -> None:
    below = [_c("O", "A1", "Item"), *_years("O")]
    for row, label, value, outline in (
        (2, "IaaS", "10", 2),
        (3, "k£", None, 2),
        (4, "DC", "30", 2),
        (5, "CAPEX", "40", 1),
        (6, "Income", None, 0),
        (7, "Revenue", "90", 1),
    ):
        below.extend(_line("O", row, label, value, outline=outline))
    parents = _parents(below, {"O": True})
    assert parents["IaaS"] == "CAPEX"
    assert parents["DC"] == "CAPEX"
    assert parents["k£"] is None
    assert parents["Revenue"] == "Income"

    above = [_c("A", "A1", "Item"), *_years("A")]
    for row, label, value, outline in (
        (2, "CAPEX", "40", 1),
        (3, "IaaS", "10", 2),
        (4, "k£", None, 2),
        (5, "DC", "30", 2),
        (6, "Income", None, 0),
        (7, "Revenue", "90", 0),
    ):
        above.extend(_line("A", row, label, value, outline=outline))
    parents = _parents(above, {"A": False})
    assert parents["IaaS"] == "CAPEX"
    assert parents["DC"] == "CAPEX"
    assert parents["k£"] is None
    assert parents["Revenue"] == "Income"


def _flat_facts(n: int) -> None:
    by_row = {
        row: [
            {
                "row": row,
                "col": 1,
                "cached_value": f"Line {row}",
                "hidden": False,
                "formula_raw": None,
                "style_indent": 0,
                "outline_level": 0,
            },
            {
                "row": row,
                "col": 2,
                "cached_value": "10",
                "hidden": False,
                "formula_raw": None,
                "style_indent": 0,
                "outline_level": 0,
            },
        ]
        for row in range(2, n + 2)
    }
    _data_rows(
        by_row,
        sorted(by_row),
        set(),
        [1],
        False,
        {2},
    )


def test_flat_parent_search_calls_is_unit_text_linearly(monkeypatch) -> None:
    """Doubling a flat table must not quadruple unit-text checks.

    The old scan called ``is_unit_text`` on every previous row. A flat indent
    never broke that loop, so the checks grew with the square of the row count.
    """
    calls = 0
    real = row_mod.is_unit_text

    def wrapped(text: str | None) -> bool:
        nonlocal calls
        calls += 1
        return real(text)

    monkeypatch.setattr(row_mod, "is_unit_text", wrapped)
    _flat_facts(200)
    small = calls
    calls = 0
    _flat_facts(400)
    large = calls
    assert large <= small * 2.6
    assert large <= 4 * 400
