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
    assert [row.label for row in sheet.blocks[0].rows] == ["DC Capacity"]


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
