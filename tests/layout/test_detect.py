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


def test_two_axes_on_one_sheet_become_two_blocks() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2023"),
        _c("P&L", "C1", "2024E"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "100"),
        _c("P&L", "C2", "110"),
        _c("P&L", "A10", "Item"),
        _c("P&L", "B10", "1 кв. 2025"),
        _c("P&L", "C10", "2 кв. 2025"),
        _c("P&L", "A11", "Revenue"),
        _c("P&L", "B11", "20"),
        _c("P&L", "C11", "30"),
    ]
    layout = detect_layout(cells)
    sheet = next(s for s in layout.sheets if s.name == "P&L")
    assert len(sheet.blocks) == 2
    roles_0 = [h.role for h in sheet.blocks[0].axis.headers]
    assert roles_0 == ["historical", "forecast"]
    assert [h.period_key for h in sheet.blocks[0].axis.headers] == ["2023", "2024"]
    assert all(h.role in {"historical", "forecast"} for h in sheet.blocks[1].axis.headers)
    assert sheet.blocks[0].axis.headers[0].col == 2
    assert "concept_id" not in sheet.blocks[0].rows[0].model_dump()


def test_hidden_column_is_not_label_column() -> None:
    cells = [
        _c("Sheet1", "A1", "Secret", hidden=True),
        _c("Sheet1", "B1", "Item"),
        _c("Sheet1", "C1", "2023"),
        _c("Sheet1", "D1", "2024E"),
        _c("Sheet1", "A2", "hidden-label", hidden=True),
        _c("Sheet1", "B2", "Revenue"),
        _c("Sheet1", "C2", "1"),
        _c("Sheet1", "D2", "2"),
    ]
    layout = detect_layout(cells)
    block = layout.sheets[0].blocks[0]
    assert block.label_col == 2
    assert block.rows[0].label == "Revenue"


def test_period_role_is_not_article_role() -> None:
    cells = [
        _c("BS", "A1", "Item"),
        _c("BS", "B1", "2023"),
        _c("BS", "C1", "2024E"),
        _c("BS", "A2", "Assets"),
        _c("BS", "B2", "10"),
        _c("BS", "C2", "11"),
    ]
    layout = detect_layout(cells)
    block = layout.sheets[0].blocks[0]
    assert {h.role for h in block.axis.headers} <= {
        "historical",
        "forecast",
        "stub",
        "scenario",
        "total",
    }
    for row in block.rows:
        dumped = row.model_dump()
        assert "concept_id" not in dumped
        assert dumped.get("article_role") is None


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


def test_monthly_headers_form_a_period_axis() -> None:
    cells = [
        _c("CF", "A1", "Item"),
        _c("CF", "B1", "янв.25"),
        _c("CF", "C1", "фев.25"),
        _c("CF", "A2", "Cash"),
        _c("CF", "B2", "100"),
        _c("CF", "C2", "90"),
    ]
    layout = detect_layout(cells)
    block = layout.sheets[0].blocks[0]
    keys = [h.period_key for h in block.axis.headers]
    assert keys == ["2025-01", "2025-02"]


def test_plain_year_and_e_suffix_share_calendar_key() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2024"),
        _c("P&L", "C1", "2024E"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "100"),
        _c("P&L", "C2", "80"),
    ]
    layout = detect_layout(cells)
    headers = layout.sheets[0].blocks[0].axis.headers
    assert [h.period_key for h in headers] == ["2024", "2024"]
    assert [h.role for h in headers] == ["historical", "forecast"]


def test_plan_fact_headers_share_period_key() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2024 факт"),
        _c("P&L", "C1", "2024 план"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "100"),
        _c("P&L", "C2", "80"),
    ]
    layout = detect_layout(cells)
    headers = layout.sheets[0].blocks[0].axis.headers
    assert [h.period_key for h in headers] == ["2024", "2024"]
    assert [h.role for h in headers] == ["historical", "forecast"]


def test_date_year_quarter_band_is_one_quarterly_axis() -> None:
    cells = [
        _c("P&L", "A4", None),
        _c("P&L", "B4", "01.07.2022"),
        _c("P&L", "C4", "01.10.2022"),
        _c("P&L", "D4", "01.01.2023"),
        _c("P&L", "E4", "01.04.2023"),
        _c("P&L", "B6", "2022"),
        _c("P&L", "C6", "2022"),
        _c("P&L", "D6", "2023"),
        _c("P&L", "E6", "2023"),
        _c("P&L", "A7", "Квартал"),
        _c("P&L", "B7", "3"),
        _c("P&L", "C7", "4"),
        _c("P&L", "D7", "1"),
        _c("P&L", "E7", "2"),
        _c("P&L", "A8", "Revenue"),
        _c("P&L", "B8", "10"),
        _c("P&L", "C8", "11"),
        _c("P&L", "D8", "12"),
        _c("P&L", "E8", "13"),
    ]
    layout = detect_layout(cells)
    sheet = layout.sheets[0]
    assert len(sheet.blocks) == 1
    keys = [h.period_key for h in sheet.blocks[0].axis.headers]
    assert keys == ["2022Q3", "2022Q4", "2023Q1", "2023Q2"]
    assert [h.role for h in sheet.blocks[0].axis.headers] == ["historical"] * 4
    assert [r.label for r in sheet.blocks[0].rows] == ["Revenue"]


def test_forecast_banner_starts_second_block() -> None:
    cells = [
        _c("P&L", "B1", "01.07.2022"),
        _c("P&L", "C1", "01.10.2022"),
        _c("P&L", "B2", "2022"),
        _c("P&L", "C2", "2022"),
        _c("P&L", "A3", "Квартал"),
        _c("P&L", "B3", "3"),
        _c("P&L", "C3", "4"),
        _c("P&L", "A4", "Revenue"),
        _c("P&L", "B4", "10"),
        _c("P&L", "C4", "11"),
        _c("P&L", "A10", "Прогноз"),
        _c("P&L", "B11", "2026"),
        _c("P&L", "C11", "2026"),
        _c("P&L", "B12", "1 кв."),
        _c("P&L", "C12", "2 кв."),
        _c("P&L", "A13", "Revenue"),
        _c("P&L", "B13", "20"),
        _c("P&L", "C13", "21"),
    ]
    layout = detect_layout(cells)
    blocks = layout.sheets[0].blocks
    assert len(blocks) == 2
    assert [h.period_key for h in blocks[0].axis.headers] == ["2022Q3", "2022Q4"]
    assert [h.role for h in blocks[0].axis.headers] == ["historical", "historical"]
    assert [h.period_key for h in blocks[1].axis.headers] == ["2026Q1", "2026Q2"]
    assert [h.role for h in blocks[1].axis.headers] == ["forecast", "forecast"]


def test_fact_marker_overrides_forecast_banner() -> None:
    cells = [
        _c("P&L", "A1", "Прогноз"),
        _c("P&L", "B2", "2025"),
        _c("P&L", "C2", "2026"),
        _c("P&L", "D2", "2026"),
        _c("P&L", "E2", "2026"),
        _c("P&L", "B3", "4 кв."),
        _c("P&L", "C3", "1 кв."),
        _c("P&L", "D3", "2 кв."),
        _c("P&L", "E3", "3 кв."),
        _c("P&L", "B4", "факт"),
        _c("P&L", "A5", "№"),
        _c("P&L", "B5", "1"),
        _c("P&L", "C5", "2"),
        _c("P&L", "D5", "3"),
        _c("P&L", "E5", "4"),
        _c("P&L", "A6", "Revenue"),
        _c("P&L", "B6", "10"),
        _c("P&L", "C6", "20"),
        _c("P&L", "D6", "30"),
        _c("P&L", "E6", "40"),
    ]
    layout = detect_layout(cells)
    assert len(layout.sheets[0].blocks) == 1
    headers = layout.sheets[0].blocks[0].axis.headers
    assert [h.period_key for h in headers] == ["2025Q4", "2026Q1", "2026Q2", "2026Q3"]
    assert [h.role for h in headers] == ["historical", "forecast", "forecast", "forecast"]
    assert [r.label for r in layout.sheets[0].blocks[0].rows] == ["Revenue"]


def test_year_forward_fill_under_forecast_banner() -> None:
    cells = [
        _c("P&L", "A1", "Прогноз"),
        _c("P&L", "B2", "2026"),
        _c("P&L", "D2", "2027"),
        _c("P&L", "B3", "1 кв."),
        _c("P&L", "C3", "2 кв."),
        _c("P&L", "D3", "3 кв."),
        _c("P&L", "E3", "4 кв."),
        _c("P&L", "A4", "Revenue"),
        _c("P&L", "B4", "1"),
        _c("P&L", "C4", "2"),
        _c("P&L", "D4", "3"),
        _c("P&L", "E4", "4"),
    ]
    layout = detect_layout(cells)
    headers = layout.sheets[0].blocks[0].axis.headers
    assert [h.period_key for h in headers] == ["2026Q1", "2026Q2", "2027Q3", "2027Q4"]
    assert all(h.role == "forecast" for h in headers)


def test_start_end_period_pair_prefers_end() -> None:
    cells = [
        _c("CF", "A1", "Начало периода"),
        _c("CF", "B1", "01.01.2024"),
        _c("CF", "C1", "01.04.2024"),
        _c("CF", "A2", "Конец периода"),
        _c("CF", "B2", "31.03.2024"),
        _c("CF", "C2", "30.06.2024"),
        _c("CF", "A3", "Cash"),
        _c("CF", "B3", "8"),
        _c("CF", "C3", "9"),
    ]
    layout = detect_layout(cells)
    keys = [h.period_key for h in layout.sheets[0].blocks[0].axis.headers]
    assert keys == ["2024Q1", "2024Q2"]


def test_excel_serial_with_date_format_is_period() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "44743", number_format="mm-dd-yy"),
        _c("P&L", "C1", "44835", number_format="mm-dd-yy"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "1"),
        _c("P&L", "C2", "2"),
    ]
    layout = detect_layout(cells)
    keys = [h.period_key for h in layout.sheets[0].blocks[0].axis.headers]
    assert keys == ["2022Q3", "2022Q4"]


def test_plain_serial_without_date_format_is_not_a_period() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "44743"),
        _c("P&L", "C1", "44835"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "1"),
        _c("P&L", "C2", "2"),
    ]
    layout = detect_layout(cells)
    assert layout.sheets[0].blocks == []


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


def test_weekly_dates_keep_distinct_period_keys() -> None:
    cells = [
        _c("Dash", "A1", "Item"),
        _c("Dash", "B1", "11.01.2026"),
        _c("Dash", "C1", "18.01.2026"),
        _c("Dash", "D1", "25.01.2026"),
        _c("Dash", "A2", "Receipts"),
        _c("Dash", "B2", "10"),
        _c("Dash", "C2", "20"),
        _c("Dash", "D2", "30"),
    ]
    layout = detect_layout(cells)
    keys = [h.period_key for h in layout.sheets[0].blocks[0].axis.headers]
    assert keys == ["2026-01-11", "2026-01-18", "2026-01-25"]


def test_model_year_row_forms_relative_axis() -> None:
    cells = [
        _c("CFS", "B2", "Year"),
        _c("CFS", "E2", "1"),
        _c("CFS", "F2", "2"),
        _c("CFS", "G2", "3"),
        _c("CFS", "B6", "P&L"),
        _c("CFS", "B9", "Gross revenues"),
        _c("CFS", "E9", "10"),
        _c("CFS", "F9", "20"),
        _c("CFS", "G9", "30"),
        _c("CFS", "B13", "EBITDA"),
        _c("CFS", "E13", "400"),
        _c("CFS", "F13", "500"),
        _c("CFS", "G13", "600"),
    ]
    layout = detect_layout(cells)
    blocks = layout.sheets[0].blocks
    assert len(blocks) == 1
    block = blocks[0]
    assert [h.period_key for h in block.axis.headers] == ["Y1", "Y2", "Y3"]
    assert all(h.role == "relative" for h in block.axis.headers)
    assert block.label_col == 2
    rows = {r.label: r for r in block.rows}
    assert rows["Gross revenues"].kind == "fact"
    assert rows["EBITDA"].kind == "fact"
    assert rows["P&L"].kind == "abstract"
    assert rows["Gross revenues"].section_path == ["P&L"]


def test_start_end_dates_left_of_timeline_are_not_a_block() -> None:
    cells = [
        _c("PF", "D1", "Item"),
        _c("PF", "L1", "2024"),
        _c("PF", "M1", "2025"),
        _c("PF", "N1", "2026"),
        _c("PF", "D2", "Revenue"),
        _c("PF", "L2", "100"),
        _c("PF", "M2", "110"),
        _c("PF", "N2", "120"),
        _c("PF", "D3", "Lease"),
        _c("PF", "G3", "01.01.2026"),
        _c("PF", "H3", "31.12.2035"),
        _c("PF", "D4", "EPC"),
        _c("PF", "L4", "10"),
        _c("PF", "M4", "20"),
        _c("PF", "N4", "30"),
        _c("PF", "D5", "O&M"),
        _c("PF", "L5", "11"),
        _c("PF", "M5", "22"),
        _c("PF", "N5", "33"),
    ]
    layout = detect_layout(cells)
    blocks = layout.sheets[0].blocks
    assert len(blocks) == 1
    assert [h.period_key for h in blocks[0].axis.headers] == ["2024", "2025", "2026"]
    labels = [r.label for r in blocks[0].rows if r.kind == "fact"]
    assert labels == ["Revenue", "EPC", "O&M"]


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


def test_unlabeled_model_years_align_with_formula_run() -> None:
    cells = [_c("CFS", "A1", "Item")]
    for index, col in enumerate("CDEFGHIJ", start=1):
        cells.append(_c("CFS", f"{col}1", str(index)))
    cells.append(_c("CFS", "A2", "Revenue"))
    for index, col in enumerate("CDEFGHIJ", start=1):
        cells.append(
            _c(
                "CFS",
                f"{col}2",
                str(index * 10),
                formula=f"={col}1*10",
                formula_template="=R[-1]C[0]*10",
            )
        )
    layout = detect_layout(cells)
    blocks = layout.sheets[0].blocks
    assert len(blocks) == 1
    assert [h.period_key for h in blocks[0].axis.headers] == [f"P{i}" for i in range(1, 9)]
    assert all(h.role == "relative" for h in blocks[0].axis.headers)
    assert blocks[0].rows[0].kind == "fact"


def test_short_calendar_right_of_timeline_is_dropped() -> None:
    cells = [
        _c("PF", "A1", "Item"),
        _c("PF", "B1", "2024"),
        _c("PF", "C1", "2025"),
        _c("PF", "D1", "2026"),
        _c("PF", "E1", "2027"),
        _c("PF", "A2", "Revenue"),
        _c("PF", "B2", "10"),
        _c("PF", "C2", "20"),
        _c("PF", "D2", "30"),
        _c("PF", "E2", "40"),
        _c("PF", "A10", "COD"),
        _c("PF", "G10", "01.01.2030"),
        _c("PF", "H10", "31.12.2030"),
        _c("PF", "A11", "O&M"),
        _c("PF", "B11", "11"),
        _c("PF", "C11", "22"),
        _c("PF", "D11", "33"),
        _c("PF", "E11", "44"),
    ]
    layout = detect_layout(cells)
    blocks = layout.sheets[0].blocks
    assert len(blocks) == 1
    assert [h.period_key for h in blocks[0].axis.headers] == ["2024", "2025", "2026", "2027"]
    assert [r.label for r in blocks[0].rows if r.kind == "fact"] == ["Revenue", "O&M"]


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


def test_params_block_from_scenario_matrix() -> None:
    cells = [
        _c("Input Assumptions", "C5", "Units"),
        _c("Input Assumptions", "D5", "Values"),
        _c("Input Assumptions", "F2", "Base Case"),
        _c("Input Assumptions", "G2", "Scenario 2"),
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
    unit_cols = {cell.role for cell in labels["Tax Rate"].cells}
    assert "unit" in unit_cols
    assert "value" in unit_cols
    from finance_context.layout.params import unit_kind_from_text

    assert unit_kind_from_text("£/year") == "money"
    assert unit_kind_from_text("years") == "count"
    assert unit_kind_from_text("veh/year") == "count"


def test_prose_and_shortcut_sheets_are_rejected() -> None:
    cells = [
        _c("Cover", "A1", "Strictly confidential educational material for modelling."),
        _c("Cover", "A2", "No representation or warranty of any kind is made in relation to accuracy."),
        _c("Cover", "A3", "This spreadsheet is for educational purposes only and must not be relied on."),
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


def test_side_by_side_year_and_month_tables() -> None:
    years = list(zip("CDEFGHIJKL", range(2020, 2030), strict=True))
    months = [("N", "янв.20"), ("O", "фев.20"), ("P", "мар.20"), ("Q", "апр.20")]
    cells = [_c("Output", "B6", "Item")]
    for col, year in years:
        cells.append(_c("Output", f"{col}6", str(year)))
    for col, text in months:
        cells.append(_c("Output", f"{col}6", text))
    cells.append(_c("Output", "B7", "DC Capacity"))
    for col, _year in years:
        cells.append(_c("Output", f"{col}7", "100"))
    for col, _text in months:
        cells.append(_c("Output", f"{col}7", "10"))
    layout = detect_layout(cells)
    sheet = layout.sheets[0]
    assert len(sheet.blocks) == 2
    year_block = next(
        block
        for block in sheet.blocks
        if all(h.period_key.isdigit() for h in block.axis.headers)
    )
    month_block = next(block for block in sheet.blocks if block is not year_block)
    assert [h.period_key for h in year_block.axis.headers] == [str(y) for _, y in years]
    assert max(h.col for h in year_block.axis.headers) < min(
        h.col for h in month_block.axis.headers
    )
    assert year_block.rows[0].label == "DC Capacity"
    assert month_block.rows[0].label == "DC Capacity"


def test_short_start_end_left_of_timeline_is_dropped() -> None:
    cells = [
        _c("PF", "A1", "Start"),
        _c("PF", "B1", "01.01.2020"),
        _c("PF", "C1", "31.12.2024"),
        _c("PF", "E1", "2020"),
        _c("PF", "F1", "2021"),
        _c("PF", "G1", "2022"),
        _c("PF", "H1", "2023"),
        _c("PF", "A2", "Revenue"),
        _c("PF", "B2", "1"),
        _c("PF", "C2", "2"),
        _c("PF", "E2", "10"),
        _c("PF", "F2", "11"),
        _c("PF", "G2", "12"),
        _c("PF", "H2", "13"),
    ]
    layout = detect_layout(cells)
    sheet = layout.sheets[0]
    assert len(sheet.blocks) == 1
    keys = [h.period_key for h in sheet.blocks[0].axis.headers]
    assert keys == ["2020", "2021", "2022", "2023"]

