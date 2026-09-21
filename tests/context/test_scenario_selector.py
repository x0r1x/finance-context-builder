from __future__ import annotations

from finance_context.context.build import build_context
from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout
from finance_context.mapping.models import MappingDocument
from finance_context.render.markdown import render_markdown


def _c(sheet: str, addr: str, value: str | None, formula: str | None = None) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": False,
        "formula_raw": formula,
        "formula_template": None,
        "unparsed": False,
        "number_format": None,
        "comment": None,
    }


def _scenario_matrix() -> list[dict]:
    return [
        _c("Input Assumptions", "C5", "Units"),
        _c("Input Assumptions", "D5", "Values"),
        _c("Input Assumptions", "F2", "Base Case"),
        _c("Input Assumptions", "G2", "Scenario 2"),
        _c("Input Assumptions", "B3", "Scenario Chosen"),
        _c("Input Assumptions", "D3", "1"),
        _c("Input Assumptions", "F3", "1"),
        _c("Input Assumptions", "G3", "2"),
        _c("Input Assumptions", "B7", "TIME ASSUMPTIONS"),
        _c("Input Assumptions", "B8", "Concession Duration"),
        _c("Input Assumptions", "C8", "years"),
        _c("Input Assumptions", "D8", "40", formula="=INDEX(F8:G8,$D$3)"),
        _c("Input Assumptions", "F8", "40"),
        _c("Input Assumptions", "G8", "36.5"),
        _c("Input Assumptions", "B9", "Tax Rate"),
        _c("Input Assumptions", "C9", "%"),
        _c("Input Assumptions", "D9", "0.3"),
        _c("Input Assumptions", "F9", "0.3"),
        _c("Input Assumptions", "G9", "0.3"),
        _c("Input Assumptions", "B10", "Toll Rate"),
        _c("Input Assumptions", "C10", "£"),
        _c("Input Assumptions", "D10", "3.56"),
        _c("Input Assumptions", "F10", "3.56"),
        _c("Input Assumptions", "G10", "3.56"),
        _c("Input Assumptions", "B11", "Gearing"),
        _c("Input Assumptions", "C11", "%"),
        _c("Input Assumptions", "D11", "0.65"),
        _c("Input Assumptions", "F11", "0.65"),
        _c("Input Assumptions", "G11", "0.8"),
    ]


def test_scenario_selector_is_in_inventory_and_markdown() -> None:
    cells = _scenario_matrix()
    layout = detect_layout(cells)
    doc = build_context(
        job_id="sel",
        workbook_meta={"sheets": [{"name": "Input Assumptions"}]},
        cells=cells,
        layout=layout,
        mapping=MappingDocument(),
    )
    rows = [row for block in doc.blocks for row in block.rows]
    selector = next(row for row in rows if row.label == "Scenario Chosen")
    assert selector.kind == "flag"
    assert selector.row == 3
    assert selector.context_role == "scenario_selector"
    assert selector.disposition == "excluded"
    assert any(cell.addr == "D3" and str(cell.cached_value) == "1" for cell in selector.cells)
    assert "1" in selector.values
    rendered = render_markdown(doc)
    assert "## Parameters / Input Assumptions" in rendered
    assert "| Scenario Chosen |" in rendered
    assert "1" in rendered
