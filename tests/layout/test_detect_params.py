from __future__ import annotations

from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout


def _c(
    sheet: str,
    addr: str,
    value: str | None,
    *,
    hidden: bool = False,
    formula: str | None = None,
    formula_template: str | None = None,
    number_format: str | None = None,
) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": hidden,
        "formula_raw": formula,
        "formula_template": formula_template,
        "unparsed": False,
        "number_format": number_format,
        "comment": None,
    }


def test_params_block_from_scenario_matrix() -> None:
    cells = [
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
        _c("Input Assumptions", "B12", "TRAFFIC & REVENUE ASSUMPTIONS"),
        _c("Input Assumptions", "F12", "TRAFFIC & REVENUE ASSUMPTIONS"),
        _c("Input Assumptions", "B13", "Maintenance (including heavy maintenance & SPV costs)"),
        _c("Input Assumptions", "C13", "£/year"),
        _c("Input Assumptions", "D13", "8900000"),
        _c("Input Assumptions", "F13", "8900000"),
        _c("Input Assumptions", "G13", "8900000"),
    ]
    layout = detect_layout(cells)
    sheet = layout.sheets[0]
    assert sheet.blocks
    block = sheet.blocks[0]
    assert block.kind == "params"
    labels = {row.label: row for row in block.rows}
    assert labels["TIME ASSUMPTIONS"].kind == "abstract"
    assert labels["TRAFFIC & REVENUE ASSUMPTIONS"].kind == "abstract"
    assert labels["Concession Duration"].kind == "fact"
    selector = labels["Scenario Chosen"]
    assert selector.kind == "flag"
    assert selector.row == 3
    assert selector.label_col == 2
    assert {cell.col: cell.role for cell in selector.cells}[4] == "value"
    assert all(cell.role != "scenario" for cell in selector.cells)
    by_col = {header.col: header for header in block.axis.headers}
    assert by_col[6].text == "Base Case"
    assert by_col[6].role == "scenario"
    assert by_col[7].text == "Scenario 2"
    assert block.label_col == 2
    unit_cols = {cell.role for cell in labels["Tax Rate"].cells}
    assert "unit" in unit_cols
    assert "value" in unit_cols
    from finance_context.layout.params import unit_kind_from_text

    assert unit_kind_from_text("£/year") == "money"
    assert unit_kind_from_text("руб") == "money"
    assert unit_kind_from_text("РУБ") == "money"
    assert unit_kind_from_text("gbp") == "money"
    assert unit_kind_from_text("EUR") == "money"
    assert unit_kind_from_text("долл") == "money"
    assert unit_kind_from_text("years") == "count"
    assert unit_kind_from_text("veh/year") == "count"
    assert unit_kind_from_text("MWh p.a.") == "count"
    assert unit_kind_from_text("Index") == "ratio"


def test_params_block_from_parameter_table() -> None:
    cells = [
        _c("Assumptions", "B6", "Parameter"),
        _c("Assumptions", "C6", "Base"),
        _c("Assumptions", "D6", "Bull"),
        _c("Assumptions", "E6", "Bear"),
        _c("Assumptions", "F6", "Active"),
        _c("Assumptions", "G6", "Unit"),
        _c("Assumptions", "H6", "Notes"),
        _c("Assumptions", "B8", "Company"),
        _c("Assumptions", "C8", "Acme Corp"),
        _c("Assumptions", "F8", "Acme Corp"),
        _c("Assumptions", "G8", "text"),
        _c("Assumptions", "H8", "Legal name of the SPV"),
        _c("Assumptions", "B9", "Model version"),
        _c("Assumptions", "C9", "v3"),
        _c("Assumptions", "F9", "v3"),
        _c("Assumptions", "G9", "text"),
        _c("Assumptions", "B10", "Tax Rate"),
        _c("Assumptions", "C10", "0.25"),
        _c("Assumptions", "D10", "0.2"),
        _c("Assumptions", "E10", "0.3"),
        _c("Assumptions", "F10", "0.25"),
        _c("Assumptions", "G10", "%"),
        _c("Assumptions", "B11", "Start date"),
        _c("Assumptions", "C11", "44927"),
        _c("Assumptions", "F11", "44927"),
        _c("Assumptions", "G11", "date"),
        _c("Assumptions", "B12", "Gearing"),
        _c("Assumptions", "C12", "0.65"),
        _c("Assumptions", "D12", "0.7"),
        _c("Assumptions", "E12", "0.5"),
        _c("Assumptions", "F12", "0.65"),
        _c("Assumptions", "G12", "%"),
    ]
    layout = detect_layout(cells)
    block = layout.sheets[0].blocks[0]
    assert block.kind == "params"
    tax = next(row for row in block.rows if row.label == "Tax Rate")
    roles = {cell.role for cell in tax.cells}
    assert "unit" in roles
    assert "value" in roles
    assert "scenario" in roles
