from __future__ import annotations

from tests.helpers.fast_sheet import SHEET, YEARS, fast_cells

from finance_context.layout.detect import detect_layout


def _layout():
    return detect_layout(fast_cells())


def _rows(layout) -> dict[int, object]:
    return {row.row: row for block in layout.sheets[0].blocks for row in block.rows}


def test_date_ruler_is_one_axis_without_the_model_start_column() -> None:
    sheet = _layout().sheets[0]
    assert [axis.id for axis in sheet.axes] == [f"{SHEET}!r7"]
    axis = sheet.axes[0]
    assert [period.period_key for period in axis.periods] == [str(year) for year in YEARS]
    assert all(period.col != 12 for period in axis.periods)
    assert axis.periods[0].start_date == "2024-01-01"
    assert axis.periods[0].end_date == "2024-12-31"


def test_phase_flags_under_the_ruler_are_block_rows() -> None:
    rows = _rows(_layout())
    assert rows[8].kind == "flag"
    assert rows[9].kind == "flag"


def test_computed_year_header_reuses_the_date_ruler() -> None:
    sheet = _layout().sheets[0]
    calc = next(block for block in sheet.blocks if block.block_id == f"{SHEET}!r185")
    assert calc.axis_ids == [f"{SHEET}!r7"]
    rows = {row.row: row for row in calc.rows}
    assert rows[186].section_path == ["Electricity price forecast (real terms)"]


def test_scenario_table_is_a_params_block_with_live_and_case_columns() -> None:
    sheet = _layout().sheets[0]
    scenario = next(block for block in sheet.blocks if block.block_id == f"{SHEET}!r49")
    assert scenario.kind == "params"
    headers = [(header.col, header.role, header.text) for header in scenario.axis.headers]
    assert headers == [
        (12, "value", "Live Case"),
        (14, "scenario", "Case 1"),
        (15, "scenario", "Case 2"),
        (16, "scenario", "Case 3"),
    ]
    assert 49 not in {row.row for row in scenario.rows}
    assert {50, 51, 64} <= {row.row for row in scenario.rows}
    timeline = next(block for block in sheet.blocks if block.block_id == f"{SHEET}!r7")
    assert {row.row for row in timeline.rows} >= {8, 9, 165}
    assert not {50, 51, 64} & {row.row for row in timeline.rows}


def test_checks_and_constants_are_a_params_block_with_captioned_columns() -> None:
    sheet = _layout().sheets[0]
    constants = next(block for block in sheet.blocks if block.block_id == f"{SHEET}!r11")
    assert constants.kind == "params"
    assert [header.text for header in constants.axis.headers] == ["Reference", "Result", "Value H"]
    rows = {row.row: row for row in constants.rows}
    assert rows[31].kind == "fact"
    assert rows[13].kind == "helper"


def test_role_cells_carry_units_totals_and_captions() -> None:
    rows = _rows(_layout())
    roles = {cell.col: (cell.role, cell.header) for cell in rows[64].cells}
    assert roles[5] == ("unit", None)
    assert roles[7] == ("value", "Start")
    assert roles[8] == ("value", "End")
    assert roles[12] == ("value", "Live Case")
    assert {cell.col: cell.role for cell in rows[50].cells}[5] == "unit"
    assert {cell.col: cell.role for cell in rows[193].cells}[12] == "total"
    assert all(cell.header is None for cell in rows[31].cells)


def test_scalar_kpi_left_of_the_ruler_is_a_fact() -> None:
    rows = _rows(_layout())
    assert rows[560].kind == "fact"
    assert rows[559].kind == "abstract"


def test_price_cases_are_facts_not_flags() -> None:
    rows = _rows(_layout())
    assert rows[186].kind == "fact"
    assert rows[48].kind == "flag"
