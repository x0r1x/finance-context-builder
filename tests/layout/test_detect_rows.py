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


def test_check_row_is_marker_not_finding() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2023"),
        _c("P&L", "C1", "2024E"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "100"),
        _c("P&L", "C2", "110"),
        _c("P&L", "A3", "Проверка баланса"),
        _c("P&L", "B3", "0"),
        _c("P&L", "C3", "0"),
    ]
    layout = detect_layout(cells)
    rows = layout.sheets[0].blocks[0].rows
    checks = [r for r in rows if r.check_row]
    assert len(checks) == 1
    assert checks[0].label == "Проверка баланса"
    dumped = layout.model_dump()
    assert "findings" not in dumped
    assert "candidates" not in dumped


def test_section_header_is_abstract_and_week_index_is_index() -> None:
    cells = [
        _c("Dash", "A1", "Item"),
        _c("Dash", "B1", "11.01.2026"),
        _c("Dash", "C1", "18.01.2026"),
        _c("Dash", "D1", "25.01.2026"),
        _c("Dash", "A2", "Week #"),
        _c("Dash", "B2", "1"),
        _c("Dash", "C2", "2"),
        _c("Dash", "D2", "3"),
        _c("Dash", "A3", "CASH INFLOWS"),
        _c("Dash", "A4", "Receipts"),
        _c("Dash", "B4", "10"),
        _c("Dash", "C4", "20"),
        _c("Dash", "D4", "30"),
        _c("Dash", "A5", "Проверка баланса"),
        _c("Dash", "B5", "0"),
        _c("Dash", "C5", "0"),
        _c("Dash", "D5", "0"),
    ]
    layout = detect_layout(cells)
    rows = {r.label: r for r in layout.sheets[0].blocks[0].rows}
    assert rows["Week #"].kind == "index"
    assert rows["CASH INFLOWS"].kind == "abstract"
    assert rows["Receipts"].kind == "fact"
    assert rows["Receipts"].section_path == ["CASH INFLOWS"]
    assert rows["Receipts"].parent_row == rows["CASH INFLOWS"].row
    assert rows["Проверка баланса"].kind == "helper"


def test_total_label_and_check_left_of_axis_stay_rows() -> None:
    cells = [
        _c("BS", "B3", "Year"),
        _c("BS", "F3", "1"),
        _c("BS", "G3", "2"),
        _c("BS", "H3", "3"),
        _c("BS", "B8", "Asset"),
        _c("BS", "C8", "k£"),
        _c("BS", "F8", "10"),
        _c("BS", "G8", "11"),
        _c("BS", "H8", "12"),
        _c("BS", "B9", "Cash in hand"),
        _c("BS", "C9", "k£"),
        _c("BS", "F9", "1"),
        _c("BS", "G9", "2"),
        _c("BS", "H9", "3"),
        _c("BS", "B10", "Total"),
        _c("BS", "C10", "k£"),
        _c("BS", "F10", "11", formula="=SUM(F8:F9)"),
        _c("BS", "G10", "13", formula="=SUM(G8:G9)"),
        _c("BS", "H10", "15", formula="=SUM(H8:H9)"),
        _c("BS", "E19", "CHECK"),
        _c("BS", "F19", "1", formula="=F10-F16<0.01"),
        _c("BS", "G19", "1", formula="=G10-G16<0.01"),
        _c("BS", "H19", "1", formula="=H10-H16<0.01"),
    ]
    layout = detect_layout(cells)
    rows = {row.row: row for row in layout.sheets[0].blocks[0].rows}
    assert rows[10].label == "Total"
    assert rows[10].kind == "fact"
    assert rows[19].label == "CHECK"
    assert rows[19].kind == "helper"
    assert rows[19].check_row is True


def test_nested_label_columns_use_span_indent() -> None:
    cells = [
        _c("PF", "L1", "2024"),
        _c("PF", "M1", "2025"),
        _c("PF", "N1", "2026"),
        _c("PF", "B2", "Cashflow Statement"),
        _c("PF", "C3", "Uses of funds"),
        _c("PF", "D4", "EPC"),
        _c("PF", "L4", "10"),
        _c("PF", "M4", "20"),
        _c("PF", "N4", "30"),
        _c("PF", "D5", "Development"),
        _c("PF", "L5", "4"),
        _c("PF", "M5", "5"),
        _c("PF", "N5", "6"),
        _c("PF", "D6", "Share premium"),
        _c("PF", "L6", "1"),
        _c("PF", "M6", "1"),
        _c("PF", "N6", "1"),
    ]
    layout = detect_layout(cells)
    block = layout.sheets[0].blocks[0]
    assert block.label_col == 4
    rows = {r.label: r for r in block.rows}
    assert rows["Cashflow Statement"].kind == "abstract"
    assert rows["Uses of funds"].kind == "abstract"
    assert rows["EPC"].kind == "fact"
    assert rows["EPC"].section_path == ["Cashflow Statement", "Uses of funds"]
    assert rows["EPC"].label_col == 4
    assert rows["Cashflow Statement"].label_col == 2


def test_small_integers_with_formulas_are_fact() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2023"),
        _c("P&L", "C1", "2024E"),
        _c("P&L", "D1", "2025E"),
        _c("P&L", "A2", "Units"),
        _c("P&L", "B2", "1", formula="=Assumptions!B2", formula_template="=Assumptions!R[0]C[0]"),
        _c("P&L", "C2", "2", formula="=Assumptions!C2", formula_template="=Assumptions!R[0]C[0]"),
        _c("P&L", "D2", "3", formula="=Assumptions!D2", formula_template="=Assumptions!R[0]C[0]"),
    ]
    layout = detect_layout(cells)
    rows = {r.label: r for r in layout.sheets[0].blocks[0].rows}
    assert rows["Units"].kind == "fact"


def test_flag_values_are_not_index() -> None:
    cells = [
        _c("PF", "A1", "Item"),
        _c("PF", "B1", "2024"),
        _c("PF", "C1", "2025"),
        _c("PF", "D1", "2026"),
        _c("PF", "A2", "Construction flag"),
        _c("PF", "B2", "1"),
        _c("PF", "C2", "1"),
        _c("PF", "D2", "0"),
        _c("PF", "A3", "Revenue"),
        _c("PF", "B3", "10"),
        _c("PF", "C3", "20"),
        _c("PF", "D3", "30"),
    ]
    layout = detect_layout(cells)
    rows = {r.label: r for r in layout.sheets[0].blocks[0].rows}
    assert rows["Construction flag"].kind == "flag"
    assert rows["Revenue"].kind == "fact"


def test_placeholder_and_binary_timing_are_not_facts() -> None:
    cells = [
        _c("PF", "A1", "Item"),
        _c("PF", "B1", "2024"),
        _c("PF", "C1", "2025"),
        _c("PF", "D1", "2026"),
        _c("PF", "A2", "Spare"),
        _c("PF", "B2", "0"),
        _c("PF", "C2", "0"),
        _c("PF", "D2", "0"),
        _c("PF", "A3", "Construction"),
        _c("PF", "B3", "1"),
        _c("PF", "C3", "1"),
        _c("PF", "D3", "0"),
        _c("PF", "A4", "Merchant price choice"),
        _c("PF", "B4", "Mid"),
        _c("PF", "C4", "Low"),
        _c("PF", "D4", "Mid"),
        _c("PF", "A5", "Revenue"),
        _c("PF", "B5", "10"),
        _c("PF", "C5", "20"),
        _c("PF", "D5", "30"),
    ]
    layout = detect_layout(cells)
    rows = {r.label: r for r in layout.sheets[0].blocks[0].rows}
    assert rows["Spare"].kind == "helper"
    assert rows["Construction"].kind == "flag"
    assert rows["Merchant price choice"].kind == "flag"
    assert rows["Revenue"].kind == "fact"


def test_scenario_and_covenant_breach_are_flags() -> None:
    cells = [
        _c("PF", "A1", "Item"),
        _c("PF", "B1", "2024"),
        _c("PF", "C1", "2025"),
        _c("PF", "D1", "2026"),
        _c("PF", "A2", "Mid case"),
        _c("PF", "B2", "1"),
        _c("PF", "C2", "0"),
        _c("PF", "D2", "0"),
        _c("PF", "A3", "Low case"),
        _c("PF", "B3", "0"),
        _c("PF", "C3", "1"),
        _c("PF", "D3", "0"),
        _c("PF", "A4", "Applied (real terms)"),
        _c("PF", "B4", "1"),
        _c("PF", "C4", "1"),
        _c("PF", "D4", "1"),
        _c("PF", "A5", "Covenant breach"),
        _c("PF", "B5", "0"),
        _c("PF", "C5", "0"),
        _c("PF", "D5", "1"),
        _c("PF", "A6", "Revenue"),
        _c("PF", "B6", "10"),
        _c("PF", "C6", "20"),
        _c("PF", "D6", "30"),
    ]
    layout = detect_layout(cells)
    rows = {r.label: r for r in layout.sheets[0].blocks[0].rows}
    assert rows["Mid case"].kind == "flag"
    assert rows["Low case"].kind == "flag"
    assert rows["Applied (real terms)"].kind == "flag"
    assert rows["Covenant breach"].kind == "flag"
    assert rows["Revenue"].kind == "fact"


def test_index_names_a_value_column_and_stays_a_unit_cell() -> None:
    from finance_context.layout.params import column_header_text, is_unit_text

    assert is_unit_text("Index")
    assert not is_unit_text("Base Index")
    titled = {
        5: [{"col": 7, "cached_value": "Index"}],
        6: [{"col": 7, "cached_value": "1.02"}],
    }
    assert column_header_text(titled, 7, 6, 5, False) == "Index"
    currency = {
        5: [{"col": 5, "cached_value": "EUR'000"}],
        6: [{"col": 5, "cached_value": "10"}],
    }
    assert column_header_text(currency, 5, 6, 5, False) is None


def test_prose_and_shortcut_sheets_are_rejected() -> None:
    cells = [
        _c("Cover", "A1", "Strictly confidential educational material for modelling."),
        _c(
            "Cover",
            "A2",
            "No representation or warranty of any kind is made in relation to accuracy.",
        ),
        _c(
            "Cover",
            "A3",
            "This spreadsheet is for educational purposes only and must not be relied on.",
        ),
        _c("Shortcuts", "A1", "CTRL"),
        _c("Shortcuts", "B1", "+"),
        _c("Shortcuts", "C1", "Arrow Keys"),
        _c("Shortcuts", "D1", "Move to the edge of the current data region in a worksheet."),
        _c("Shortcuts", "A2", "CTRL"),
        _c("Shortcuts", "B2", "+"),
        _c("Shortcuts", "C2", "Home"),
        _c("Shortcuts", "D2", "Move to the beginning of the worksheet."),
        _c("Shortcuts", "A3", "TAB"),
        _c("Shortcuts", "D3", "Move to the next cell within a menu window."),
    ]
    layout = detect_layout(cells)
    assert all(not sheet.blocks for sheet in layout.sheets)


def test_sensitivity_grid_keeps_title_only() -> None:
    cells = [
        _c("Sensitivity", "A1", "GRID 1: MO-12 CLOSING CASH"),
        _c("Sensitivity", "B2", "0.75"),
        _c("Sensitivity", "C2", "1.00"),
        _c("Sensitivity", "D2", "1.25"),
        _c("Sensitivity", "A3", "-0.2"),
        _c("Sensitivity", "B3", "100"),
        _c("Sensitivity", "C3", "200"),
        _c("Sensitivity", "D3", "300"),
        _c("Sensitivity", "A4", "0"),
        _c("Sensitivity", "B4", "110"),
        _c("Sensitivity", "C4", "210"),
        _c("Sensitivity", "D4", "310"),
        _c("Sensitivity", "A5", "0.2"),
        _c("Sensitivity", "B5", "120"),
        _c("Sensitivity", "C5", "220"),
        _c("Sensitivity", "D5", "320"),
    ]
    layout = detect_layout(cells)
    block = layout.sheets[0].blocks[0]
    assert len(block.rows) == 1
    assert block.rows[0].kind == "abstract"
    assert "GRID" in block.rows[0].label


def test_timeline_stub_cells_get_roles() -> None:
    cells = [
        _c("Construction", "A1", "Item"),
        _c("Construction", "E1", "2023"),
        _c("Construction", "F1", "2024"),
        _c("Construction", "G1", "2025"),
        _c("Construction", "A2", "CAPEX"),
        _c("Construction", "C2", "300", formula="=SUM(E2:G2)"),
        _c("Construction", "E2", "100", formula_template="=RC[1]"),
        _c("Construction", "F2", "100"),
        _c("Construction", "G2", "100"),
        _c("Construction", "A3", "Equity target"),
        _c("Construction", "C3", "115", formula="=$C$2*0.35"),
        _c("Construction", "E3", "50"),
        _c("Construction", "F3", "65"),
        _c("Construction", "G3", "0"),
        _c("Construction", "A4", "Debt"),
        _c("Construction", "C4", "k£"),
        _c("Construction", "E4", "10"),
        _c("Construction", "F4", "20"),
        _c("Construction", "G4", "30"),
    ]
    layout = detect_layout(cells)
    rows = {row.label: row for row in layout.sheets[0].blocks[0].rows}
    capex_roles = {cell.role for cell in rows["CAPEX"].cells}
    assert "total" in capex_roles
    equity_roles = {cell.role for cell in rows["Equity target"].cells}
    assert "value" in equity_roles
    debt_roles = {cell.role for cell in rows["Debt"].cells}
    assert "unit" in debt_roles


def test_check_result_table_is_helper_not_junk() -> None:
    cells = [
        _c("Checks", "A1", "Check"),
        _c("Checks", "B1", "Result"),
        _c("Checks", "C1", "Note"),
        _c("Checks", "A2", "Balance sheet ties"),
        _c("Checks", "B2", "1"),
        _c("Checks", "C2", "assets vs equity plus debt"),
        _c("Checks", "A3", "Cashflow ties"),
        _c("Checks", "B3", "0"),
        _c("Checks", "A4", "Sources equal uses"),
        _c("Checks", "B4", "1"),
        _c("Checks", "A5", "Debt schedule ties"),
        _c("Checks", "B5", "1"),
    ]
    layout = detect_layout(cells)
    rows = layout.sheets[0].blocks[0].rows
    assert len(rows) >= 3
    assert all(row.check_row or row.kind == "helper" for row in rows)


def test_graph_selects_active_value_column() -> None:
    cells = [
        _c("Input Assumptions", "B5", "Parameter"),
        _c("Input Assumptions", "C5", "Base"),
        _c("Input Assumptions", "D5", "Values"),
        _c("Input Assumptions", "B8", "Gearing"),
        _c("Input Assumptions", "C8", "0.7"),
        _c("Input Assumptions", "D8", "0.65"),
        _c("Input Assumptions", "B9", "Tax Rate"),
        _c("Input Assumptions", "C9", "0.3"),
        _c("Input Assumptions", "D9", "0.3"),
        _c("Input Assumptions", "B10", "Duration"),
        _c("Input Assumptions", "C10", "30"),
        _c("Input Assumptions", "D10", "40"),
        _c("Input Assumptions", "B11", "Toll"),
        _c("Input Assumptions", "C11", "3"),
        _c("Input Assumptions", "D11", "3.56"),
    ]
    edges = [
        {"source": "Construction!E21", "target": "Input Assumptions!D8"},
        {"source": "P&L!C9", "target": "Input Assumptions!D9"},
        {"source": "CFS!C10", "target": "Input Assumptions!D10"},
    ]
    layout = detect_layout(cells, edges=edges)
    block = layout.sheets[0].blocks[0]
    value_cols = {header.col for header in block.axis.headers if header.role == "value"}
    assert value_cols == {4}


def test_unit_caption_does_not_replace_the_debt_section() -> None:
    cells = [
        _c("PF", "L1", "2024"),
        _c("PF", "M1", "2025"),
        _c("PF", "B2", "Senior Debt"),
        _c("PF", "C3", "Linear repayment"),
        _c("PF", "B4", "% p.a."),
        _c("PF", "D5", "Balance b/f"),
        _c("PF", "L5", "10"),
        _c("PF", "D6", "Principal repayment"),
        _c("PF", "L6", "2"),
        _c("PF", "D7", "Balance c/f"),
        _c("PF", "L7", "8"),
    ]
    layout = detect_layout(cells)
    rows = {row.label: row for row in layout.sheets[0].blocks[0].rows}
    assert "Linear repayment" in rows["Balance b/f"].section_path
    assert "% p.a." not in rows["Principal repayment"].section_path
    assert rows["Principal repayment"].parent_row == rows["Linear repayment"].row
    assert rows["Balance c/f"].parent_row == rows["Linear repayment"].row


def test_totals_take_the_asset_section_as_parent() -> None:
    cells = [
        _c("PF", "L1", "2024"),
        _c("PF", "M1", "2025"),
        _c("PF", "B2", "Balance Sheet"),
        _c("PF", "C3", "Non-current assets"),
        _c("PF", "C4", "Total"),
        _c("PF", "L4", "10"),
        _c("PF", "C5", "Current assets"),
        _c("PF", "C6", "Total"),
        _c("PF", "L6", "4"),
    ]
    layout = detect_layout(cells)
    totals = [row for row in layout.sheets[0].blocks[0].rows if row.label == "Total"]
    by_section = {tuple(row.section_path): row for row in totals}
    noncurrent = by_section[("Balance Sheet", "Non-current assets")]
    current = by_section[("Balance Sheet", "Current assets")]
    labels = {row.row: row.label for row in layout.sheets[0].blocks[0].rows}
    assert labels[noncurrent.parent_row] == "Non-current assets"
    assert labels[current.parent_row] == "Current assets"
