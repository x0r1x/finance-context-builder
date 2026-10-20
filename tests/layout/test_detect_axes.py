from __future__ import annotations

from pathlib import Path

import pytest

from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout
from finance_context.layout.periods import marker_token
from finance_context.layout.stage import layout_workbook


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
    kinds_0 = [h.kind for h in sheet.blocks[0].axis.headers]
    assert kinds_0 == ["period", "period"]
    assert [marker_token(h.variant) for h in sheet.blocks[0].axis.headers] == ["", "e"]
    assert [h.period_key for h in sheet.blocks[0].axis.headers] == ["2023", "2024"]
    assert all(h.kind == "period" for h in sheet.blocks[1].axis.headers)
    assert sheet.blocks[0].axis.headers[0].col == 2


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
    assert block.rows[0].hidden is False


def test_hidden_data_rows_join_their_block() -> None:
    cells = [
        _c("Assumptions", "B15", "2015"),
        _c("Assumptions", "C15", "2016"),
        _c("Assumptions", "D15", "2017"),
        _c("Assumptions", "B16", "A. ПРОДАЖИ"),
        _c("Assumptions", "B19", "Продажи продукта 1, ед."),
        _c("Assumptions", "C19", "10"),
        _c("Assumptions", "D19", "11"),
        _c("Assumptions", "B20", "Продажи продукта 2, ед.", hidden=True),
        _c("Assumptions", "C20", "12", hidden=True),
        _c("Assumptions", "D20", "13", hidden=True),
        _c("Assumptions", "B21", "Продажи продукта 3, ед.", hidden=True),
        _c("Assumptions", "C21", "14", hidden=True),
        _c("Assumptions", "D21", "15", hidden=True),
        _c("Assumptions", "B34", "B. РАСХОДЫ НА СЫРЬЕ"),
        _c("Assumptions", "B37", "Расходы на сырье 1"),
        _c("Assumptions", "C37", "1"),
        _c("Assumptions", "D37", "2"),
        _c("Assumptions", "B38", "Расходы на сырье 2", hidden=True),
        _c("Assumptions", "C38", "3", hidden=True),
        _c("Assumptions", "D38", "4", hidden=True),
    ]
    for col, year in zip("BCD", ["2015", "2016", "2017"], strict=True):
        cells.append(_c("Assumptions", f"{col}33", year))
    blocks = detect_layout(cells).sheets[0].blocks
    sales = next(block for block in blocks if any("продукта 1" in row.label for row in block.rows))
    assert sales.label_col == 2
    by_label = {row.label: row for row in sales.rows}
    assert by_label["Продажи продукта 1, ед."].hidden is False
    assert by_label["Продажи продукта 2, ед."].hidden is True
    assert by_label["Продажи продукта 3, ед."].hidden is True
    assert by_label["Продажи продукта 2, ед."].kind == "fact"
    materials = next(
        block for block in blocks if any(row.label == "Расходы на сырье 2" for row in block.rows)
    )
    raw = next(row for row in materials.rows if row.label == "Расходы на сырье 2")
    assert raw.hidden is True
    assert raw.kind == "fact"


def test_hidden_period_column_stays_in_the_run() -> None:
    cells = [
        _c("CF", "A1", "Item"),
        _c("CF", "B1", "2023"),
        _c("CF", "C1", "2024", hidden=True),
        _c("CF", "D1", "2025E"),
        _c("CF", "A2", "Revenue"),
        _c("CF", "B2", "1"),
        _c("CF", "C2", "2", hidden=True),
        _c("CF", "D2", "3"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert len(sheet.axes) == 1
    periods = sheet.axes[0].periods
    assert [period.period_key for period in periods] == ["2023", "2024", "2025"]
    assert [period.hidden for period in periods] == [False, True, False]
    assert sheet.blocks[0].rows[0].label == "Revenue"
    assert sheet.blocks[0].rows[0].hidden is False


def test_empty_hidden_column_does_not_split_the_period_run() -> None:
    cells = [
        _c("CF", "A1", "Item"),
        _c("CF", "B1", "2023"),
        _c("CF", "D1", "2024"),
        _c("CF", "E1", "2025E"),
        _c("CF", "A2", "Revenue"),
        _c("CF", "B2", "1"),
        _c("CF", "D2", "2"),
        _c("CF", "E2", "3"),
    ]
    sheet = detect_layout(cells, hidden_columns={"CF": {3}}).sheets[0]
    assert len(sheet.axes) == 1
    assert [period.period_key for period in sheet.axes[0].periods] == ["2023", "2024", "2025"]
    assert [period.hidden for period in sheet.axes[0].periods] == [False, False, False]


def test_hidden_scratch_prices_do_not_open_an_axis() -> None:
    cells = [
        _c("Inputs", "A1", "Item"),
        _c("Inputs", "A2", "Zinc"),
        _c("Inputs", "A3", "Отраслевые"),
        _c("Inputs", "J3", "1965", hidden=True),
        _c("Inputs", "K3", "1938", hidden=True),
        _c("Inputs", "L3", "2166", hidden=True),
        _c("Inputs", "M3", "1939"),
        _c("Inputs", "N3", "2101"),
    ]
    for col, year in zip("JKLMN", ["2012", "2013", "2014", "2015", "2016"], strict=True):
        hidden = col in "JKL"
        cells.append(_c("Inputs", f"{col}1", year, hidden=hidden))
        cells.append(_c("Inputs", f"{col}2", "1", hidden=hidden))
    sheet = detect_layout(cells).sheets[0]
    assert [axis.header_row for axis in sheet.axes] == [1]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2012",
        "2013",
        "2014",
        "2015",
        "2016",
    ]
    assert [period.hidden for period in sheet.axes[0].periods] == [
        True,
        True,
        True,
        False,
        False,
    ]
    assert [row.label for row in sheet.blocks[0].rows] == ["Zinc", "Отраслевые"]


def test_hidden_year_prefix_continues_across_an_empty_hidden_column() -> None:
    cells = [
        _c("CF", "A1", "Item"),
        _c("CF", "B1", "2023", hidden=True),
        _c("CF", "C1", "2024", hidden=True),
        _c("CF", "E1", "2025"),
        _c("CF", "F1", "2026E"),
        _c("CF", "A2", "Revenue"),
        _c("CF", "B2", "1", hidden=True),
        _c("CF", "C2", "2", hidden=True),
        _c("CF", "E2", "3"),
        _c("CF", "F2", "4"),
    ]
    sheet = detect_layout(cells, hidden_columns={"CF": {2, 3, 4}}).sheets[0]
    assert len(sheet.axes) == 1
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2023",
        "2024",
        "2025",
        "2026",
    ]


def test_hidden_relative_prefix_continues_across_an_empty_hidden_column() -> None:
    cells = [
        _c("CF", "A1", "Year"),
        _c("CF", "B1", "1", hidden=True),
        _c("CF", "C1", "2", hidden=True),
        _c("CF", "E1", "3"),
        _c("CF", "A2", "Revenue"),
        _c("CF", "B2", "10", hidden=True),
        _c("CF", "C2", "11", hidden=True),
        _c("CF", "E2", "12"),
    ]
    sheet = detect_layout(cells, hidden_columns={"CF": {2, 3, 4}}).sheets[0]
    assert len(sheet.axes) == 1
    assert [period.period_key for period in sheet.axes[0].periods] == ["Y1", "Y2", "Y3"]


def test_hidden_scratch_years_do_not_bridge_onto_the_visible_axis() -> None:
    cells = [
        _c("CF", "A1", "Item"),
        _c("CF", "B1", "1965", hidden=True),
        _c("CF", "D1", "1939"),
        _c("CF", "E1", "1940"),
        _c("CF", "A2", "Revenue"),
        _c("CF", "D2", "1"),
        _c("CF", "E2", "2"),
    ]
    sheet = detect_layout(cells, hidden_columns={"CF": {2, 3}}).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == ["1939", "1940"]


def test_visible_gap_still_splits_the_period_run() -> None:
    cells = [
        _c("CF", "A1", "Item"),
        _c("CF", "B1", "2023"),
        _c("CF", "D1", "2024"),
        _c("CF", "E1", "2025E"),
        _c("CF", "A2", "Revenue"),
        _c("CF", "B2", "1"),
        _c("CF", "D2", "2"),
        _c("CF", "E2", "3"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == ["2024", "2025"]


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
    assert {h.kind for h in block.axis.headers} <= {
        "period",
        "stub",
        "scenario",
        "total",
        "relative",
    }
    for row in block.rows:
        dumped = row.model_dump()
        assert dumped.get("article_role") is None


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
    assert [h.period_key for h in headers] == ["2024", "2024@e"]
    assert [h.kind for h in headers] == ["period", "period"]
    assert [marker_token(h.variant) for h in headers] == ["", "e"]


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
    assert [h.period_key for h in headers] == ["2024@факт", "2024@план"]
    assert [h.kind for h in headers] == ["period", "period"]
    assert [marker_token(h.variant) for h in headers] == ["факт", "план"]


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
    assert [h.kind for h in sheet.blocks[0].axis.headers] == ["period"] * 4
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
    assert [h.kind for h in blocks[0].axis.headers] == ["period", "period"]
    assert [h.period_key for h in blocks[1].axis.headers] == ["2026Q1", "2026Q2"]
    assert [marker_token(h.variant) for h in blocks[1].axis.headers] == ["прогноз", "прогноз"]


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
    assert [marker_token(h.variant) for h in headers] == ["факт", "прогноз", "прогноз", "прогноз"]
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
    assert all(marker_token(h.variant) == "прогноз" for h in headers)


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
    assert all(h.kind == "relative" for h in block.axis.headers)
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
    assert all(h.kind == "relative" for h in blocks[0].axis.headers)
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
    assert len(sheet.blocks) == 1
    assert len(sheet.axes) == 2
    year_axis = next(axis for axis in sheet.axes if axis.grain == "year")
    month_axis = next(axis for axis in sheet.axes if axis.grain == "month")
    block = sheet.blocks[0]
    assert block.axis_ids == [year_axis.id, month_axis.id]
    assert [period.period_key for period in year_axis.periods] == [str(y) for _, y in years]
    assert max(period.col for period in year_axis.periods) < min(
        period.col for period in month_axis.periods
    )
    assert [row.label for row in block.rows] == ["DC Capacity"]


def test_repeated_year_banner_is_group_key_not_an_axis() -> None:
    cells = [
        _c("Output", "N1", "2020"),
        _c("Output", "O1", "2020"),
        _c("Output", "P1", "2020"),
        _c("Output", "Q1", "2021"),
        _c("Output", "A3", "Operational Results"),
        _c("Output", "B6", "Item"),
        _c("Output", "C6", "2020"),
        _c("Output", "D6", "2021"),
        _c("Output", "E6", "2022"),
        _c("Output", "N6", "янв.20"),
        _c("Output", "O6", "фев.20"),
        _c("Output", "P6", "мар.20"),
        _c("Output", "Q6", "янв.21"),
        _c("Output", "B7", "DC Capacity"),
        _c("Output", "C7", "10"),
        _c("Output", "D7", "11"),
        _c("Output", "E7", "12"),
        _c("Output", "N7", "1"),
        _c("Output", "O7", "2"),
        _c("Output", "P7", "3"),
        _c("Output", "Q7", "4"),
    ]
    layout = detect_layout(cells)
    sheet = layout.sheets[0]
    assert len(sheet.blocks) == 1
    grains = {axis.grain for axis in sheet.axes}
    assert grains == {"year", "month"}
    month = next(axis for axis in sheet.axes if axis.grain == "month")
    assert [period.group_key for period in month.periods] == ["2020", "2020", "2020", "2021"]
    assert all(
        len(axis.periods) == len({p.period_key for p in axis.periods}) for axis in sheet.axes
    )
    assert sheet.blocks[0].axis_ids == [axis.id for axis in sheet.axes]
    assert [row.label for row in sheet.blocks[0].rows] == ["Operational Results", "DC Capacity"]
    assert sheet.blocks[0].rows[1].section_path == ["Operational Results"]


def test_repeated_section_headers_share_one_axis() -> None:
    years = list(zip("CDE", range(2020, 2023), strict=True))
    months = [("N", "янв.20"), ("O", "фев.20"), ("P", "мар.20"), ("Q", "апр.20")]
    cells = []
    for header_row, label_row, label in ((6, 7, "Revenue"), (21, 22, "Costs")):
        cells.append(_c("Output", f"B{header_row}", "Item"))
        for col, year in years:
            cells.append(_c("Output", f"{col}{header_row}", str(year)))
        for col, text in months:
            cells.append(_c("Output", f"{col}{header_row}", text))
        cells.append(_c("Output", f"B{label_row}", label))
        for col, _year in years:
            cells.append(_c("Output", f"{col}{label_row}", "1"))
        for col, _text in months:
            cells.append(_c("Output", f"{col}{label_row}", "2"))
    layout = detect_layout(cells)
    sheet = layout.sheets[0]
    assert len(sheet.blocks) == 2
    assert len(sheet.axes) == 2
    assert {axis.grain for axis in sheet.axes} == {"year", "month"}
    assert sheet.blocks[0].axis_ids == sheet.blocks[1].axis_ids
    assert sheet.blocks[0].block_id != sheet.blocks[1].block_id


def test_conflicting_section_flags_keep_separate_axes() -> None:
    cells = [
        _c("Output", "B6", "Item"),
        _c("Output", "C6", "2020"),
        _c("Output", "D6", "2021"),
        _c("Output", "E6", "2022"),
        _c("Output", "B7", "Construction"),
        _c("Output", "C7", "1"),
        _c("Output", "D7", "1"),
        _c("Output", "E7", "0"),
        _c("Output", "B8", "Revenue"),
        _c("Output", "C8", "10"),
        _c("Output", "D8", "11"),
        _c("Output", "E8", "12"),
        _c("Output", "B12", "Item"),
        _c("Output", "C12", "2020"),
        _c("Output", "D12", "2021"),
        _c("Output", "E12", "2022"),
        _c("Output", "B13", "Construction"),
        _c("Output", "C13", "0"),
        _c("Output", "D13", "0"),
        _c("Output", "E13", "1"),
        _c("Output", "B14", "Costs"),
        _c("Output", "C14", "1"),
        _c("Output", "D14", "2"),
        _c("Output", "E14", "3"),
    ]
    layout = detect_layout(cells)
    sheet = layout.sheets[0]
    year_axes = [axis for axis in sheet.axes if axis.grain == "year"]
    assert len(sheet.blocks) == 2
    assert len(year_axes) == 2
    assert sheet.blocks[0].axis_ids != sheet.blocks[1].axis_ids


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


def test_roman_quarter_headers_form_a_timeline_axis() -> None:
    heads = ["I кв. 2025", "II кв. 2025", "III кв. 2025", "IV кв. 2025"]
    cells = [_c("Кв", "A2", "Показатель")]
    cells += [_c("Кв", f"{col}2", h) for col, h in zip("BCDE", heads, strict=True)]
    for r, label in enumerate(["Выручка", "Расходы", "Прибыль"], start=3):
        cells.append(_c("Кв", f"A{r}", label))
        cells += [_c("Кв", f"{col}{r}", str(r * 10 + i)) for i, col in enumerate("BCDE")]
    block = detect_layout(cells).sheets[0].blocks[0]
    assert block.kind == "timeline"
    assert [h.period_key for h in block.axis.headers] == ["2025Q1", "2025Q2", "2025Q3", "2025Q4"]


def test_half_year_headers_keep_both_halves_on_the_axis() -> None:
    heads = ["1 полугодие 2025", "2 полугодие 2025", "1 полугодие 2026", "2 полугодие 2026"]
    cells = [_c("ПГ", "A2", "Показатель")]
    cells += [_c("ПГ", f"{col}2", h) for col, h in zip("BCDE", heads, strict=True)]
    for r, label in enumerate(["Выручка", "Расходы", "Прибыль"], start=3):
        cells.append(_c("ПГ", f"A{r}", label))
        cells += [_c("ПГ", f"{col}{r}", str(r * 10 + i)) for i, col in enumerate("BCDE")]
    layout = detect_layout(cells)
    block = layout.sheets[0].blocks[0]
    assert block.kind == "timeline"
    assert [h.period_key for h in block.axis.headers] == ["2025H1", "2025H2", "2026H1", "2026H2"]


def test_quantity_with_a_kv_unit_under_years_stays_a_row() -> None:
    cells = [_c("S", "A1", "Показатель")]
    years = ["2023", "2024", "2025", "2026"]
    cells += [_c("S", f"{col}1", y) for col, y in zip("BCDE", years, strict=True)]
    cells += [_c("S", "A2", "Мощность")]
    cells += [_c("S", f"{col}2", f"{i} кВт") for i, col in enumerate("BCDE", start=1)]
    cells += [_c("S", "A3", "Выручка")]
    cells += [_c("S", f"{col}3", str(10 * i)) for i, col in enumerate("BCDE", start=1)]
    block = detect_layout(cells).sheets[0].blocks[0]
    assert [h.period_key for h in block.axis.headers] == ["2023", "2024", "2025", "2026"]
    assert [row.label for row in block.rows] == ["Мощность", "Выручка"]


@pytest.mark.parametrize(
    "halves",
    [["H1", "H2", "H1", "H2"], ["1 полугодие", "2 полугодие", "1 полугодие", "2 полугодие"]],
)
def test_year_row_over_half_tokens_forms_a_half_year_axis(halves: list[str]) -> None:
    years = ["2025", "2025", "2026", "2026"]
    cells = [_c("S", f"{col}1", y) for col, y in zip("BCDE", years, strict=True)]
    cells += [_c("S", "A2", "Показатель")]
    cells += [_c("S", f"{col}2", h) for col, h in zip("BCDE", halves, strict=True)]
    for r, label in enumerate(["Выручка", "Расходы", "Прибыль"], start=3):
        cells.append(_c("S", f"A{r}", label))
        cells += [_c("S", f"{col}{r}", str(r * 10 + i)) for i, col in enumerate("BCDE")]
    block = detect_layout(cells).sheets[0].blocks[0]
    assert [h.period_key for h in block.axis.headers] == ["2025H1", "2025H2", "2026H1", "2026H2"]


def test_on_column_fact_and_forecast_banners_paint_until_the_next_banner() -> None:
    quarters = ["4 кв. 2016", "1 кв. 2017", "2 кв. 2017", "3 кв. 2017", "4 кв. 2017", "1 кв. 2018"]
    cells = [
        _c("CF", "B1", "Факт"),
        _c("CF", "F1", "Прогноз"),
        _c("CF", "A3", "Поступления"),
    ]
    for col, quarter in zip("BCDEFG", quarters, strict=True):
        cells.append(_c("CF", f"{col}2", quarter))
        cells.append(_c("CF", f"{col}3", "10"))
    headers = detect_layout(cells).sheets[0].blocks[0].axis.headers
    assert [h.period_key for h in headers] == [
        "2016Q4",
        "2017Q1",
        "2017Q2",
        "2017Q3",
        "2017Q4",
        "2018Q1",
    ]
    assert [marker_token(h.variant) for h in headers] == ["факт"] * 4 + ["прогноз"] * 2


def test_repeated_quarter_header_keeps_the_banner_roles() -> None:
    quarters = ["4 кв. 2016", "1 кв. 2017", "2 кв. 2017", "3 кв. 2017", "4 кв. 2017", "1 кв. 2018"]
    cells = [_c("CF", "B1", "Факт"), _c("CF", "F1", "Прогноз"), _c("CF", "A3", "Поступления")]
    for col, quarter in zip("BCDEFG", quarters, strict=True):
        cells.append(_c("CF", f"{col}2", quarter))
        cells.append(_c("CF", f"{col}3", "10"))
        cells.append(_c("CF", f"{col}8", quarter))
        cells.append(_c("CF", f"{col}9", "20"))
    cells.append(_c("CF", "A9", "Долг"))
    blocks = detect_layout(cells).sheets[0].blocks
    assert len(blocks) == 2
    expected = ["факт"] * 4 + ["прогноз"] * 2
    assert [marker_token(h.variant) for h in blocks[0].axis.headers] == expected
    assert [marker_token(h.variant) for h in blocks[1].axis.headers] == expected


def test_stale_year_banner_does_not_override_the_quarter_year() -> None:
    cells = [
        _c("RSBU", "F1", "2008"),
        _c("RSBU", "G1", "2008"),
        _c("RSBU", "H1", "2009"),
        _c("RSBU", "A2", "Старый шаблон"),
        _c("RSBU", "F2", "1"),
        _c("RSBU", "G2", "1"),
        _c("RSBU", "H2", "1"),
        _c("RSBU", "E6", "2 кв. 2014"),
        _c("RSBU", "F6", "3 кв. 2014"),
        _c("RSBU", "G6", "4 кв. 2014"),
        _c("RSBU", "H6", "1 кв. 2015"),
        _c("RSBU", "A7", "Выручка"),
        _c("RSBU", "E7", "1"),
        _c("RSBU", "F7", "2"),
        _c("RSBU", "G7", "3"),
        _c("RSBU", "H7", "4"),
        _c("RSBU", "E20", "2014"),
        _c("RSBU", "F20", "2014"),
        _c("RSBU", "G20", "2014"),
        _c("RSBU", "H20", "2015"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert {axis.grain for axis in sheet.axes} == {"quarter"}
    quarter = next(axis for axis in sheet.axes if axis.grain == "quarter")
    assert [period.group_key for period in quarter.periods] == ["2014", "2014", "2014", "2015"]
    assert [period.period_key for period in quarter.periods] == [
        "2014Q2",
        "2014Q3",
        "2014Q4",
        "2015Q1",
    ]


def test_contradicting_year_banner_is_dropped_instead_of_becoming_group_key() -> None:
    cells = [
        _c("RSBU", "B1", "2008"),
        _c("RSBU", "C1", "2008"),
        _c("RSBU", "D1", "2009"),
        _c("RSBU", "A2", "Старый шаблон"),
        _c("RSBU", "B2", "1"),
        _c("RSBU", "C2", "1"),
        _c("RSBU", "D2", "1"),
        _c("RSBU", "B6", "2 кв. 2014"),
        _c("RSBU", "C6", "3 кв. 2014"),
        _c("RSBU", "D6", "1 кв. 2015"),
        _c("RSBU", "A7", "Выручка"),
        _c("RSBU", "B7", "1"),
        _c("RSBU", "C7", "2"),
        _c("RSBU", "D7", "3"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert len(sheet.axes) == 1
    assert [period.group_key for period in sheet.axes[0].periods] == [None, None, None]
    assert [period.period_key for period in sheet.axes[0].periods] == ["2014Q2", "2014Q3", "2015Q1"]


def test_vertically_separated_headers_keep_their_own_blocks() -> None:
    cells = [
        _c("Steps", "A3", "Продажи"),
        _c("Steps", "A4", "Сырье"),
        _c("Steps", "A21", "EBITDA"),
    ]
    quarters = ["4 кв. 2015", "1 кв. 2016", "2 кв. 2016", "3 кв. 2016"]
    for col, quarter in zip("BCDE", quarters, strict=True):
        cells.append(_c("Steps", f"{col}2", quarter))
        cells.append(_c("Steps", f"{col}3", "10"))
        cells.append(_c("Steps", f"{col}4", "11"))
    for col, year in zip("BCDEFGHIJ", range(2015, 2024), strict=True):
        cells.append(_c("Steps", f"{col}20", str(year)))
        cells.append(_c("Steps", f"{col}21", "20"))
    blocks = detect_layout(cells).sheets[0].blocks
    assert len(blocks) == 2
    assert [row.label for row in blocks[0].rows] == ["Продажи", "Сырье"]
    assert [row.label for row in blocks[1].rows] == ["EBITDA"]
    assert [h.period_key for h in blocks[0].axis.headers] == [
        "2015Q4",
        "2016Q1",
        "2016Q2",
        "2016Q3",
    ]


def test_section_title_above_the_next_header_labels_that_block() -> None:
    cells = [
        _c("Strategy", "B2", "Стресс-сценарий"),
        _c("Strategy", "B3", "Обновленный прогноз"),
        _c("Strategy", "B7", "Выручка"),
        _c("Strategy", "B8", "EBITDA"),
        _c("Strategy", "B9", "Итого"),
        _c("Strategy", "B10", "ТЕКУЩИЙ ПРОГНОЗ"),
        _c("Strategy", "B13", "Выручка"),
    ]
    for col, year in zip("CDE", ["2015", "2016", "2017"], strict=True):
        cells.append(_c("Strategy", f"{col}6", year))
        cells.append(_c("Strategy", f"{col}7", "10"))
        cells.append(_c("Strategy", f"{col}8", "11"))
        cells.append(_c("Strategy", f"{col}9", "12"))
        cells.append(_c("Strategy", f"{col}12", year))
        cells.append(_c("Strategy", f"{col}13", "30"))
    blocks = detect_layout(cells).sheets[0].blocks
    assert len(blocks) == 2
    assert [row.label for row in blocks[0].rows] == [
        "Стресс-сценарий",
        "Обновленный прогноз",
        "Выручка",
        "EBITDA",
        "Итого",
    ]
    assert [row.label for row in blocks[1].rows] == ["ТЕКУЩИЙ ПРОГНОЗ", "Выручка"]
    assert blocks[1].rows[1].section_path == ["ТЕКУЩИЙ ПРОГНОЗ"]


def _stacked_blocks(period_values: list[str | None], *, formula: bool) -> list:
    cells = [
        _c("Steps", "A3", "Продажи"),
        _c("Steps", "A4", "Сырье"),
        _c("Steps", "A21", "EBITDA"),
    ]
    quarters = ["4 кв. 2015", "1 кв. 2016", "2 кв. 2016", "3 кв. 2016"]
    for col, quarter, value in zip("BCDE", quarters, period_values, strict=True):
        cells.append(_c("Steps", f"{col}2", quarter))
        cells.append(_c("Steps", f"{col}3", "10"))
        cells.append(
            _c(
                "Steps",
                f"{col}4",
                value,
                formula=f"={col}3" if formula else None,
            )
        )
    for col, year in zip("BCDEFGHIJ", range(2015, 2024), strict=True):
        cells.append(_c("Steps", f"{col}20", str(year)))
        cells.append(_c("Steps", f"{col}21", "20"))
    return cells


def test_uncached_formula_row_stays_in_the_block_above() -> None:
    blocks = detect_layout(_stacked_blocks([None, None, None, None], formula=True)).sheets[0].blocks
    assert [row.label for row in blocks[0].rows] == ["Продажи", "Сырье"]
    assert [row.label for row in blocks[1].rows] == ["EBITDA"]


def test_period_text_row_stays_in_the_block_above() -> None:
    blocks = detect_layout(_stacked_blocks(["n/a", "n/a", "n/a", "n/a"], formula=False)).sheets[
        0
    ].blocks
    assert [row.label for row in blocks[0].rows] == ["Продажи", "Сырье"]
    assert [row.label for row in blocks[1].rows] == ["EBITDA"]


def test_hidden_section_titles_label_the_next_block() -> None:
    cells = [
        _c("Strategy", "B2", "Стресс-сценарий", hidden=True),
        _c("Strategy", "B3", "Обновленный прогноз", hidden=True),
        _c("Strategy", "B4", "Текущий прогноз", hidden=True),
        _c("Strategy", "B7", "EBITDA"),
    ]
    for col, year in zip("CDE", ["2015", "2016", "2017"], strict=True):
        cells.append(_c("Strategy", f"{col}6", year))
        cells.append(_c("Strategy", f"{col}7", "10"))
    rows = detect_layout(cells).sheets[0].blocks[0].rows
    assert [row.label for row in rows] == [
        "Стресс-сценарий",
        "Обновленный прогноз",
        "Текущий прогноз",
        "EBITDA",
    ]
    assert [row.hidden for row in rows[:3]] == [True, True, True]
    assert rows[3].section_path == ["Текущий прогноз"]


def test_visible_row_with_only_hidden_column_text_is_not_a_section(tmp_path: Path) -> None:
    import json

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "workbook.json").write_text(
        json.dumps(
            {
                "date1904": False,
                "sheets": [{"name": "CF", "hidden_rows": [], "hidden_cols": [1]}],
            }
        ),
        encoding="utf-8",
    )
    cells = [
        _c("CF", "C1", "2023"),
        _c("CF", "D1", "2024"),
        _c("CF", "E1", "2025"),
        _c("CF", "A2", "Scratch title", hidden=True),
        _c("CF", "B3", "Revenue"),
        _c("CF", "C3", "10"),
        _c("CF", "D3", "11"),
        _c("CF", "E3", "12"),
    ]
    rows = layout_workbook(tmp_path, cells=cells, edges=[]).sheets[0].blocks[0].rows
    assert [row.label for row in rows] == ["Revenue"]
    assert rows[0].section_path == []


def test_hidden_row_metadata_still_reveals_a_section_title(tmp_path: Path) -> None:
    import json

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "workbook.json").write_text(
        json.dumps(
            {
                "date1904": False,
                "sheets": [{"name": "CF", "hidden_rows": [2], "hidden_cols": []}],
            }
        ),
        encoding="utf-8",
    )
    cells = [
        _c("CF", "C1", "2023"),
        _c("CF", "D1", "2024"),
        _c("CF", "E1", "2025"),
        _c("CF", "A2", "Hidden title", hidden=True),
        _c("CF", "B3", "Revenue"),
        _c("CF", "C3", "10"),
        _c("CF", "D3", "11"),
        _c("CF", "E3", "12"),
    ]
    rows = layout_workbook(tmp_path, cells=cells, edges=[]).sheets[0].blocks[0].rows
    assert [row.label for row in rows] == ["Hidden title", "Revenue"]
    assert rows[1].section_path == ["Hidden title"]


def test_hidden_sheet_flag_is_recorded_and_rows_stay(tmp_path: Path) -> None:
    import json

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "workbook.json").write_text(
        json.dumps({"date1904": False, "sheets": [{"name": "IFRS", "hidden": True}]}),
        encoding="utf-8",
    )
    cells = [
        _c("IFRS", "C2", "Выручка от реализации"),
        _c("IFRS", "D1", "2 кв. 2009"),
        _c("IFRS", "E1", "3 кв. 2009"),
        _c("IFRS", "F1", "4 кв. 2009"),
        _c("IFRS", "D2", "10"),
        _c("IFRS", "E2", "11"),
        _c("IFRS", "F2", "12"),
    ]
    layout = layout_workbook(tmp_path, cells=cells, edges=[])
    sheet = layout.sheets[0]
    assert sheet.model_dump().get("hidden") is True
    block = sheet.blocks[0]
    assert block.label_col == 3
    assert [row.label for row in block.rows] == ["Выручка от реализации"]


def test_hidden_year_row_groups_quarters_that_omit_the_year() -> None:
    years = ["2009", "2009", "2009", "2010", "2010", "2010", "2010", "2011", "2011"]
    quarters = ["2 кв.", "3 кв.", "4 кв.", "1 кв.", "2 кв.", "3 кв.", "4 кв.", "1 кв.", "2 кв."]
    cols = list("BCDEFGHIJ")
    cells = [
        _c("МСФО", f"{col}28", year, hidden=True) for col, year in zip(cols, years, strict=True)
    ]
    cells += [
        _c("МСФО", f"{col}29", quarter) for col, quarter in zip(cols, quarters, strict=True)
    ]
    cells.append(_c("МСФО", "A30", "Выручка"))
    cells += [_c("МСФО", f"{col}30", "10") for col in cols]
    sheet = detect_layout(cells).sheets[0]
    quarter = next(axis for axis in sheet.axes if axis.grain == "quarter")
    assert [period.period_key for period in quarter.periods] == [
        "2009Q2",
        "2009Q3",
        "2009Q4",
        "2010Q1",
        "2010Q2",
        "2010Q3",
        "2010Q4",
        "2011Q1",
        "2011Q2",
    ]
    assert [period.group_key for period in quarter.periods] == years
    assert [axis.header_row for axis in sheet.axes if axis.grain == "year"] == []


def test_year_like_data_row_is_not_a_header() -> None:
    cells = [
        _c("Inputs", "A1", "Year"),
        _c("Inputs", "B1", "2016"),
        _c("Inputs", "C1", "2017"),
        _c("Inputs", "D1", "2018"),
        _c("Inputs", "E1", "2019"),
        _c("Inputs", "A2", "Revenue"),
        _c("Inputs", "B2", "10"),
        _c("Inputs", "C2", "11"),
        _c("Inputs", "D2", "12"),
        _c("Inputs", "E2", "13"),
        _c("Inputs", "A3", "Объем закупок полувагонов"),
        _c("Inputs", "B3", "2074"),
        _c("Inputs", "C3", "2155"),
        _c("Inputs", "D3", "2035.0000000000002"),
        _c("Inputs", "E3", "2035"),
        _c("Inputs", "F3", "1988"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert [axis.header_row for axis in sheet.axes] == [1]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2016",
        "2017",
        "2018",
        "2019",
    ]
    labels = [row.label for block in sheet.blocks for row in block.rows]
    assert labels == ["Revenue", "Объем закупок полувагонов"]


def test_dotted_role_word_keeps_the_year_on_the_axis() -> None:
    cells = [
        _c("CF", "A1", "Показатель"),
        _c("CF", "B1", "2024"),
        _c("CF", "C1", "2025 ожид."),
        _c("CF", "D1", "2026"),
        _c("CF", "A2", "Выручка"),
        _c("CF", "B2", "1"),
        _c("CF", "C2", "2"),
        _c("CF", "D2", "3"),
    ]
    block = detect_layout(cells).sheets[0].blocks[0]
    assert block.kind == "timeline"
    assert [h.period_key for h in block.axis.headers] == ["2024", "2025", "2026"]
