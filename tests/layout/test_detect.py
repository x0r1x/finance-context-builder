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
        "formula_template": None,
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

