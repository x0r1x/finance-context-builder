"""Content completeness counts source cells, not layout rows.

A number or a formula in the workbook is in the context when a block row, a
role cell, or an axis header carries it. A lost sheet shows up as missing cells.
"""

from __future__ import annotations

from finance_context.context.build import build_context
from finance_context.context.summary import build_summary
from finance_context.excel.a1 import format_addr
from finance_context.graph.models import GraphDocument
from finance_context.layout.detect import detect_layout
from finance_context.models.context import ContextDocument
from finance_context.render.markdown import render_markdown


def _cell(sheet: str, col: int, row: int, value: object, formula: str | None = None) -> dict:
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": format_addr(col, row),
        "cached_value": None if value is None else str(value),
        "hidden": False,
        "formula_raw": formula,
        "formula_template": None,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }


def _timeline(sheet: str = "CF") -> list[dict]:
    cells = [_cell(sheet, 1, 1, "Item")]
    cells += [_cell(sheet, col, 1, year) for col, year in ((2, 2023), (3, 2024), (4, 2025))]
    for row, label in ((2, "Revenue"), (3, "Costs")):
        cells.append(_cell(sheet, 1, row, label))
        cells += [_cell(sheet, col, row, 10 * row + col) for col in (2, 3)]
        cells.append(_cell(sheet, 4, row, 10 * row + 4, formula=f"=C{row}+1"))
    return cells


def _grid(sheet: str = "Sensitivity") -> list[dict]:
    """A two-way data table: layout keeps its title only."""
    cells = [_cell(sheet, 2, 2, "Sensitivity of NPV to price and volume")]
    cells.append(_cell(sheet, 2, 4, "Price \\ Volume"))
    cells += [_cell(sheet, col, 4, 100 * col) for col in range(3, 7)]
    for row in range(5, 9):
        cells.append(_cell(sheet, 2, row, row))
        cells += [
            _cell(sheet, col, row, row * col, formula=f"=TABLE({row},{col})")
            for col in range(3, 7)
        ]
    return cells


def _build(cells: list[dict]) -> ContextDocument:
    sheets = list(dict.fromkeys(cell["sheet"] for cell in cells))
    return build_context(
        job_id="job",
        workbook_meta={"sheets": [{"name": name} for name in sheets]},
        cells=cells,
        layout=detect_layout(cells),
    )


def test_block_values_and_axis_headers_are_in_context() -> None:
    doc = _build(_timeline())
    sheets = {item.sheet: item for item in doc.completeness.sheets}
    assert (sheets["CF"].cells, sheets["CF"].in_context, sheets["CF"].missing) == (9, 9, 0)
    assert doc.completeness.ratio == 1.0
    assert not any("Content completeness" in warning for warning in doc.warnings)


def test_year_banner_over_quarters_is_in_context() -> None:
    sheet = "Q"
    cells = [_cell(sheet, 2, 1, 2025), _cell(sheet, 6, 1, 2026), _cell(sheet, 1, 2, "Item")]
    cells += [_cell(sheet, 1 + index, 2, f"Q{(index - 1) % 4 + 1}") for index in range(1, 9)]
    cells.append(_cell(sheet, 1, 3, "Revenue"))
    cells += [_cell(sheet, col, 3, col) for col in range(2, 10)]
    doc = _build(cells)
    assert doc.completeness.cells == 10
    assert doc.completeness.missing == 0


def test_lost_sheet_counts_as_missing_and_warns() -> None:
    doc = _build([*_timeline(), *_grid()])
    sheets = {item.sheet: item for item in doc.completeness.sheets}
    assert (sheets["Sensitivity"].cells, sheets["Sensitivity"].missing) == (24, 24)
    assert sheets["CF"].missing == 0
    assert doc.completeness.cells == 33
    assert doc.completeness.in_context == 9
    assert round(doc.completeness.ratio, 2) == 0.27
    assert (
        "Content completeness: 24 of 24 numeric and formula cells on Sensitivity"
        " are not in context" in doc.warnings
    )


def test_a_few_stray_cells_are_counted_without_a_warning() -> None:
    cells = [*_timeline(), _cell("CF", 9, 20, 1), _cell("CF", 9, 21, 2)]
    doc = _build(cells)
    sheets = {item.sheet: item for item in doc.completeness.sheets}
    assert (sheets["CF"].cells, sheets["CF"].missing) == (11, 2)
    assert not any("Content completeness" in warning for warning in doc.warnings)


def test_markdown_reports_source_cells_and_the_sheets_that_lost_them() -> None:
    rendered = render_markdown(_build([*_timeline(), *_grid()]))
    assert "- Content completeness: 0.27 (9/33 numeric and formula cells in context)" in rendered
    assert "  - `Sensitivity`: 24/24 not in context" in rendered
    assert "`CF`" not in rendered.split("## ")[0]


def test_summary_carries_completeness() -> None:
    doc = _build([*_timeline(), *_grid()])
    graph = GraphDocument(job_id="job")
    summary = build_summary(doc.model_dump(mode="json"), graph.model_dump(mode="json"))
    assert summary.completeness is not None
    assert summary.completeness.missing == 24
    assert [item.sheet for item in summary.completeness.sheets] == ["CF", "Sensitivity"]
