"""Quarter bands, merged years, totals, nested rulers, and vertical periods."""

from __future__ import annotations

from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout
from finance_context.layout.models import Block, Layout, LayoutRow, SheetLayout
from finance_context.layout.relations import _CellIndex, formula_relations


def _c(
    sheet: str,
    addr: str,
    value: str | None,
    *,
    merge_end_col: int | None = None,
) -> dict:
    col, row = parse_addr(addr)
    cell = {
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
    }
    if merge_end_col is not None:
        cell["merge_end_col"] = merge_end_col
    return cell


def test_quarter_tokens_start_a_band_and_a_merged_year_fills_right() -> None:
    cells = [
        _c("IT", "B1", "2024", merge_end_col=5),
        _c("IT", "F1", "2025", merge_end_col=9),
    ]
    quarters = ["1 кв.", "2 кв.", "3 кв.", "4 кв.", "1 кв.", "2 кв.", "3 кв.", "4 кв."]
    for col, quarter in zip("BCDEFGHI", quarters, strict=True):
        cells.append(_c("IT", f"{col}2", quarter))
        cells.append(_c("IT", f"{col}3", "10"))
    cells.append(_c("IT", "A3", "Выручка"))
    sheet = detect_layout(cells).sheets[0]
    assert len(sheet.axes) == 1
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
        "2025Q1",
        "2025Q2",
        "2025Q3",
        "2025Q4",
    ]
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка"]


def test_row_of_4q_starts_a_band_under_one_merged_year() -> None:
    cells = [_c("IT", "B1", "2024", merge_end_col=5), _c("IT", "A3", "Выручка")]
    for col in "BCDE":
        cells.append(_c("IT", f"{col}2", "4Q"))
        cells.append(_c("IT", f"{col}3", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == ["2024Q4"] * 4


def test_total_between_quarter_groups_does_not_split_the_axis() -> None:
    labels = [
        "1 кв. 2024",
        "2 кв. 2024",
        "3 кв. 2024",
        "4 кв. 2024",
        "Итого",
        "1 кв. 2025",
        "2 кв. 2025",
        "3 кв. 2025",
        "4 кв. 2025",
    ]
    cells = [_c("CF", "A2", "Выручка")]
    for col, label in zip("BCDEFGHIJ", labels, strict=True):
        cells.append(_c("CF", f"{col}1", label))
        if label != "Итого":
            cells.append(_c("CF", f"{col}2", "10"))
    sheet = detect_layout(cells).sheets[0]
    quarters = [
        axis
        for axis in sheet.axes
        if axis.grain == "quarter"
        and any(period.period_key.startswith("2024Q") for period in axis.periods)
    ]
    assert len(quarters) == 1
    assert [period.period_key for period in quarters[0].periods if period.kind == "period"] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
        "2025Q1",
        "2025Q2",
        "2025Q3",
        "2025Q4",
    ]


def test_a_gap_that_does_not_continue_stays_two_axes() -> None:
    cells = [_c("CF", "A2", "Выручка")]
    for col, label in zip("BC", ["1 кв. 2024", "2 кв. 2024"], strict=True):
        cells.append(_c("CF", f"{col}1", label))
        cells.append(_c("CF", f"{col}2", "10"))
    for col, label in zip("EF", ["1 кв. 2026", "2 кв. 2026"], strict=True):
        cells.append(_c("CF", f"{col}1", label))
        cells.append(_c("CF", f"{col}2", "20"))
    sheet = detect_layout(cells).sheets[0]
    keys = [
        [period.period_key for period in axis.periods]
        for axis in sheet.axes
        if axis.grain == "quarter"
    ]
    assert keys == [["2024Q1", "2024Q2"], ["2026Q1", "2026Q2"]]


def test_narrow_axis_does_not_take_rows_of_the_wide_table() -> None:
    cells = [_c("PL", "A2", "Выручка"), _c("PL", "A6", "Деталь"), _c("PL", "A10", "Итого")]
    for index, year in enumerate(range(2020, 2026)):
        col = "BCDEFG"[index]
        cells.append(_c("PL", f"{col}1", str(year)))
        cells.append(_c("PL", f"{col}2", "1"))
        cells.append(_c("PL", f"{col}10", "8"))
    for col, year in zip("BCD", ["2020", "2021", "2022"], strict=True):
        cells.append(_c("PL", f"{col}5", year))
        cells.append(_c("PL", f"{col}6", "3"))
    sheet = detect_layout(cells).sheets[0]
    wide = next(axis for axis in sheet.axes if len(axis.periods) == 6)
    narrow = next(axis for axis in sheet.axes if len(axis.periods) == 3)
    by_axis = {block.axis_ids[0]: [row.label for row in block.rows] for block in sheet.blocks}
    assert "Выручка" in by_axis[wide.id]
    assert "Итого" in by_axis[wide.id]
    assert "Итого" not in by_axis[narrow.id]
    assert "Деталь" in by_axis[narrow.id]
    assert "Деталь" not in by_axis[wide.id]


def test_year_and_quarter_beside_each_other_stay_two_series() -> None:
    cells = [_c("CF", "A2", "Выручка")]
    for col, year in zip("CDE", ["2025", "2026", "2027"], strict=True):
        cells.append(_c("CF", f"{col}1", year))
        cells.append(_c("CF", f"{col}2", "10"))
    quarters = ["1 кв. 2025", "2 кв. 2025", "3 кв. 2025", "4 кв. 2025"]
    for col, quarter in zip("GHIJ", quarters, strict=True):
        cells.append(_c("CF", f"{col}1", quarter))
        cells.append(_c("CF", f"{col}2", "1"))
    sheet = detect_layout(cells).sheets[0]
    assert sorted(axis.grain for axis in sheet.axes) == ["quarter", "year"]
    timeline = [block for block in sheet.blocks if block.kind == "timeline"]
    assert len(timeline) == 1
    assert len(timeline[0].axis_ids) == 2
    assert [row.label for row in timeline[0].rows] == ["Выручка"]


def test_periods_down_a_column_are_transposed() -> None:
    cells = [
        _c("Debt", "B1", "Выручка"),
        _c("Debt", "C1", "EBITDA"),
        _c("Debt", "A2", "2020"),
        _c("Debt", "B2", "10"),
        _c("Debt", "C2", "20"),
        _c("Debt", "A3", "2021"),
        _c("Debt", "B3", "11"),
        _c("Debt", "C3", "21"),
        _c("Debt", "A4", "2022"),
        _c("Debt", "B4", "12"),
        _c("Debt", "C4", "22"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert len(sheet.axes) == 1
    axis = sheet.axes[0]
    assert axis.transposed is True
    assert [period.period_key for period in axis.periods] == ["2020", "2021", "2022"]
    assert [period.row for period in axis.periods] == [2, 3, 4]
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка", "EBITDA"]
    dumped = axis.model_dump(mode="json")
    assert dumped["transposed"] is True
    assert "transposed" not in detect_layout(
        [
            _c("PL", "B1", "2020"),
            _c("PL", "C1", "2021"),
            _c("PL", "A2", "Выручка"),
            _c("PL", "B2", "1"),
            _c("PL", "C2", "2"),
        ]
    ).sheets[0].axes[0].model_dump(mode="json")


def test_two_quarter_tokens_do_not_eat_the_year_ruler() -> None:
    cells = [
        _c("N", "A1", "см."),
        _c("N", "B1", "1 кв."),
        _c("N", "C1", "2 кв."),
        _c("N", "D1", "комментарий"),
        _c("N", "A4", "Выручка"),
    ]
    for col, year in zip("BCDE", ["2020", "2021", "2022", "2023"], strict=True):
        cells.append(_c("N", f"{col}3", year))
        cells.append(_c("N", f"{col}4", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2020",
        "2021",
        "2022",
        "2023",
    ]
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка"]


def test_a_year_per_column_under_quarter_tokens_stays_a_year_axis() -> None:
    cells = [_c("N", "A4", "Выручка")]
    for col, quarter in zip("BCDE", ["1 кв.", "2 кв.", "3 кв.", "4 кв."], strict=True):
        cells.append(_c("N", f"{col}1", quarter))
    for col, year in zip("BCDE", ["2020", "2021", "2022", "2023"], strict=True):
        cells.append(_c("N", f"{col}2", year))
        cells.append(_c("N", f"{col}4", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2020",
        "2021",
        "2022",
        "2023",
    ]


def test_repeated_year_under_quarter_tokens_fills_the_quarters() -> None:
    cells = [_c("Q", "A3", "Выручка")]
    quarters = {"B": "1 кв.", "C": "2 кв.", "D": "3 кв.", "E": "4 кв."}
    for col in "BCDE":
        cells.append(_c("Q", f"{col}1", quarters[col]))
        cells.append(_c("Q", f"{col}2", "2024"))
        cells.append(_c("Q", f"{col}3", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
    ]


def test_line_name_beside_one_year_is_the_section() -> None:
    cells = [
        _c("P", "A1", "Выручка"),
        _c("P", "B1", "2024"),
        _c("P", "A3", "Себестоимость"),
    ]
    for col, quarter, value in zip(
        "BCDE", ["1 кв.", "2 кв.", "3 кв.", "4 кв."], ["1", "2", "3", "4"], strict=True
    ):
        cells.append(_c("P", f"{col}2", quarter))
        cells.append(_c("P", f"{col}3", value))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
    ]
    assert [row.label for row in sheet.blocks[0].rows] == ["Себестоимость"]
    assert sheet.blocks[0].rows[0].section_path == ["Выручка"]


def test_founded_years_stay_a_list() -> None:
    cells = [
        _c("C", "A1", "Company"),
        _c("C", "B1", "Founded"),
        _c("C", "C1", "Revenue"),
        _c("C", "A2", "A"),
        _c("C", "B2", "2010"),
        _c("C", "C2", "100"),
        _c("C", "A3", "B"),
        _c("C", "B3", "2011"),
        _c("C", "C3", "200"),
        _c("C", "A4", "C"),
        _c("C", "B4", "2012"),
        _c("C", "C4", "300"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert [axis for axis in sheet.axes if axis.transposed] == []
    timeline = [block for block in sheet.blocks if block.kind == "timeline"]
    assert timeline == []


def test_a_date_journal_is_not_a_timeline() -> None:
    cells = [_c("L", "A1", "Date"), _c("L", "B1", "Amount")]
    for row, day, amount in (
        (2, "15.01.2024", "10"),
        (3, "16.01.2024", "11"),
        (4, "17.01.2024", "12"),
        (5, "18.01.2024", "13"),
    ):
        cells.append(_c("L", f"A{row}", day))
        cells.append(_c("L", f"B{row}", amount))
    sheet = detect_layout(cells).sheets[0]
    assert [axis for axis in sheet.axes if axis.transposed] == []


def test_two_dates_do_not_start_a_vertical_axis() -> None:
    cells = [
        _c("L", "B1", "Сумма"),
        _c("L", "A2", "15.01.2024"),
        _c("L", "B2", "10"),
        _c("L", "A3", "16.01.2024"),
        _c("L", "B3", "11"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert sheet.axes == []


def test_year_caption_above_a_vertical_ruler_is_kept() -> None:
    cells = [
        _c("D", "A1", "Год"),
        _c("D", "B1", "Выручка"),
        _c("D", "C1", "EBITDA"),
    ]
    for row, year, revenue, ebitda in (
        (2, "2020", "10", "20"),
        (3, "2021", "11", "21"),
        (4, "2022", "12", "22"),
    ):
        cells.append(_c("D", f"A{row}", year))
        cells.append(_c("D", f"B{row}", revenue))
        cells.append(_c("D", f"C{row}", ebitda))
    sheet = detect_layout(cells).sheets[0]
    axis = sheet.axes[0]
    assert axis.transposed is True
    assert [period.period_key for period in axis.periods] == ["2020", "2021", "2022"]
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка", "EBITDA"]


def test_blank_row_between_names_and_vertical_years() -> None:
    cells = [_c("D", "B1", "Выручка"), _c("D", "C1", "EBITDA")]
    for row, year, revenue, ebitda in (
        (3, "2020", "10", "20"),
        (4, "2021", "11", "21"),
        (5, "2022", "12", "22"),
    ):
        cells.append(_c("D", f"A{row}", year))
        cells.append(_c("D", f"B{row}", revenue))
        cells.append(_c("D", f"C{row}", ebitda))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == ["2020", "2021", "2022"]
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка", "EBITDA"]


def test_two_empty_columns_end_the_vertical_series() -> None:
    cells = [
        _c("D", "B1", "Выручка"),
        _c("D", "C1", "EBITDA"),
        _c("D", "F1", "Долг"),
        _c("D", "H1", "Name"),
        _c("D", "I1", "Amount"),
    ]
    for row, year in ((2, "2020"), (3, "2021"), (4, "2022")):
        cells.append(_c("D", f"A{row}", year))
        cells.append(_c("D", f"B{row}", "1"))
        cells.append(_c("D", f"C{row}", "2"))
        cells.append(_c("D", f"F{row}", "3"))
        cells.append(_c("D", f"I{row}", "4"))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == ["2020", "2021", "2022"]
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка", "EBITDA"]


def test_one_empty_column_keeps_the_next_series() -> None:
    cells = [_c("D", "B1", "Выручка"), _c("D", "D1", "Долг")]
    for row, year in ((2, "2020"), (3, "2021"), (4, "2022")):
        cells.append(_c("D", f"A{row}", year))
        cells.append(_c("D", f"B{row}", "1"))
        cells.append(_c("D", f"D{row}", "3"))
    sheet = detect_layout(cells).sheets[0]
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка", "Долг"]


def test_side_by_side_rows_stay_with_the_columns_they_fill() -> None:
    cells = [
        _c("S", "A2", "Left"),
        _c("S", "A6", "Shared"),
        _c("S", "A7", "OnlyLeft"),
        _c("S", "A8", "OnlyRight"),
    ]
    for col, year in zip("BCDE", ["2020", "2021", "2022", "2023"], strict=True):
        cells.append(_c("S", f"{col}1", year))
        cells.append(_c("S", f"{col}2", "1"))
        cells.append(_c("S", f"{col}6", "9"))
        cells.append(_c("S", f"{col}7", "4"))
    cells.append(_c("S", "G4", "Right"))
    for col, year in zip("HIJ", ["2021", "2022", "2023"], strict=True):
        cells.append(_c("S", f"{col}3", year))
        cells.append(_c("S", f"{col}4", "5"))
        cells.append(_c("S", f"{col}6", "7"))
        cells.append(_c("S", f"{col}8", "6"))
    sheet = detect_layout(cells).sheets[0]
    by_axis = {block.axis_ids[0]: [row.label for row in block.rows] for block in sheet.blocks}
    wide = next(axis.id for axis in sheet.axes if len(axis.periods) == 4)
    narrow = next(axis.id for axis in sheet.axes if len(axis.periods) == 3)
    assert "OnlyLeft" in by_axis[wide]
    assert "OnlyLeft" not in by_axis[narrow]
    assert "OnlyRight" in by_axis[narrow]
    assert "OnlyRight" not in by_axis[wide]
    assert "Shared" in by_axis[wide]
    assert "Shared" not in by_axis[narrow]


def test_months_down_a_column_are_one_series() -> None:
    cells = [_c("M", "B1", "Выручка")]
    for row, month, value in (
        (2, "янв 2024", "1"),
        (3, "фев 2024", "2"),
        (4, "мар 2024", "3"),
    ):
        cells.append(_c("M", f"A{row}", month))
        cells.append(_c("M", f"B{row}", value))
    sheet = detect_layout(cells).sheets[0]
    assert sheet.axes[0].transposed is True
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2024-01",
        "2024-02",
        "2024-03",
    ]
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка"]


def test_one_year_among_other_numbers_does_not_paint_the_quarters() -> None:
    cells = [
        _c("P", "A1", "Код"),
        _c("P", "B1", "2024"),
        _c("P", "C1", "1850"),
        _c("P", "D1", "3100"),
        _c("P", "E1", "900"),
        _c("P", "A3", "Объём"),
    ]
    for col, quarter in zip("BCDE", ["1 кв.", "2 кв.", "3 кв.", "4 кв."], strict=True):
        cells.append(_c("P", f"{col}2", quarter))
        cells.append(_c("P", f"{col}3", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert [axis for axis in sheet.axes if axis.grain == "quarter"] == []


def test_years_above_a_forecast_word_stay_a_year_axis() -> None:
    cells = [_c("P", "A1", "Прогноз"), _c("P", "A4", "Затраты")]
    for col, year in zip("BCDE", ["2020", "2021", "2022", "2023"], strict=True):
        cells.append(_c("P", f"{col}1", year))
    for col, quarter in zip("BCDE", ["1 кв.", "2 кв.", "3 кв.", "4 кв."], strict=True):
        cells.append(_c("P", f"{col}3", quarter))
        cells.append(_c("P", f"{col}4", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert [axis.grain for axis in sheet.axes] == ["year"]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2020",
        "2021",
        "2022",
        "2023",
    ]
    assert [row.label for row in sheet.blocks[0].rows] == ["Затраты"]


def test_a_year_below_quarter_tokens_still_fills_them() -> None:
    cells = [_c("Q", "A4", "Выручка")]
    for col, quarter in zip("BCDE", ["1 кв.", "2 кв.", "3 кв.", "4 кв."], strict=True):
        cells.append(_c("Q", f"{col}1", quarter))
        cells.append(_c("Q", f"{col}3", "2024"))
        cells.append(_c("Q", f"{col}4", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
    ]


def test_month_ends_down_a_column_stay_a_month_axis() -> None:
    cells = [_c("D", "B1", "Выручка")]
    for row, text in ((2, "31.01.2024"), (3, "29.02.2024"), (4, "31.03.2024")):
        cells.append(_c("D", f"A{row}", text))
        cells.append(_c("D", f"B{row}", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert sheet.axes[0].transposed is True
    assert sheet.axes[0].grain == "month"
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2024-01",
        "2024-02",
        "2024-03",
    ]
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка"]
    assert [block.kind for block in sheet.blocks] == ["timeline"]


def test_year_ends_down_a_column_stay_a_year_axis() -> None:
    cells = [_c("D", "B1", "Выручка"), _c("D", "C1", "EBITDA")]
    for row, year in ((2, "31.12.2021"), (3, "31.12.2022"), (4, "31.12.2023"), (5, "31.12.2024")):
        cells.append(_c("D", f"A{row}", year))
        cells.append(_c("D", f"B{row}", "10"))
        cells.append(_c("D", f"C{row}", "20"))
    sheet = detect_layout(cells).sheets[0]
    assert sheet.axes[0].transposed is True
    assert sheet.axes[0].grain == "year"
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2021",
        "2022",
        "2023",
        "2024",
    ]
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка", "EBITDA"]
    assert [block.kind for block in sheet.blocks] == ["timeline"]


def test_repeated_year_under_a_year_ruler_stays_a_line() -> None:
    cells = [_c("P", "A2", "Выручка"), _c("P", "A3", "Год ввода")]
    for col, year, value in zip(
        "BCDE", ["2020", "2021", "2022", "2023"], ["10", "11", "12", "13"], strict=True
    ):
        cells.append(_c("P", f"{col}1", year))
        cells.append(_c("P", f"{col}2", value))
        cells.append(_c("P", f"{col}3", "2024"))
    sheet = detect_layout(cells).sheets[0]
    assert [axis.grain for axis in sheet.axes] == ["year"]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2020",
        "2021",
        "2022",
        "2023",
    ]
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка", "Год ввода"]


def test_one_year_value_under_filled_quarters_stays_a_line() -> None:
    cells = [
        _c("P", "A1", "Выручка"),
        _c("P", "B1", "2024"),
        _c("P", "A3", "Год ввода"),
        _c("P", "B3", "2024"),
        _c("P", "A4", "Себестоимость"),
    ]
    for col, quarter, value in zip(
        "BCDE", ["1 кв.", "2 кв.", "3 кв.", "4 кв."], ["10", "20", "30", "40"], strict=True
    ):
        cells.append(_c("P", f"{col}2", quarter))
        cells.append(_c("P", f"{col}4", value))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
    ]
    assert [row.label for row in sheet.blocks[0].rows] == ["Год ввода", "Себестоимость"]
    assert [row.section_path for row in sheet.blocks[0].rows] == [["Выручка"], ["Выручка"]]


def test_borrowed_year_row_is_the_section_not_a_line() -> None:
    cells = [
        _c("P", "A2", "Выручка"),
        _c("P", "B2", "2024", merge_end_col=5),
        _c("P", "A3", "Себестоимость"),
    ]
    for col, quarter, value in zip(
        "BCDE", ["1 кв.", "2 кв.", "3 кв.", "4 кв."], ["10", "20", "30", "40"], strict=True
    ):
        cells.append(_c("P", f"{col}1", quarter))
        cells.append(_c("P", f"{col}3", value))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
    ]
    assert [row.label for row in sheet.blocks[0].rows] == ["Себестоимость"]
    assert sheet.blocks[0].rows[0].section_path == ["Выручка"]


def test_vertical_captions_are_not_a_closed_list() -> None:
    for caption in ("Годы", "г.", "Дата"):
        cells = [_c("D", "A1", caption), _c("D", "B1", "Выручка")]
        for row, year in ((2, "2020"), (3, "2021"), (4, "2022")):
            cells.append(_c("D", f"A{row}", year))
            cells.append(_c("D", f"B{row}", "10"))
        sheet = detect_layout(cells).sheets[0]
        assert sheet.axes and sheet.axes[0].transposed is True, caption
        assert [row.label for row in sheet.blocks[0].rows] == ["Выручка"]


def test_a_repeated_scenario_beside_years_stays_an_axis() -> None:
    cells = [_c("D", "A1", "Год"), _c("D", "C1", "Выручка"), _c("D", "D1", "EBITDA")]
    for row, year in ((2, "2020"), (3, "2021"), (4, "2022")):
        cells.append(_c("D", f"A{row}", "База"))
        cells.append(_c("D", f"B{row}", year))
        cells.append(_c("D", f"C{row}", "10"))
        cells.append(_c("D", f"D{row}", "20"))
    sheet = detect_layout(cells).sheets[0]
    assert sheet.axes[0].transposed is True
    assert [row.label for row in sheet.blocks[0].rows] == ["Выручка", "EBITDA"]


def test_four_adjacent_days_stay_a_list() -> None:
    cells = [_c("L", "B1", "Amount")]
    for row, day in ((2, "15.01.2024"), (3, "16.01.2024"), (4, "17.01.2024"), (5, "18.01.2024")):
        cells.append(_c("L", f"A{row}", day))
        cells.append(_c("L", f"B{row}", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert [axis for axis in sheet.axes if axis.transposed] == []
    assert sheet.blocks


def test_two_quarter_tokens_under_one_merged_year_are_not_an_axis() -> None:
    cells = [
        _c("IT", "B1", "2024", merge_end_col=3),
        _c("IT", "B2", "1 кв."),
        _c("IT", "C2", "2 кв."),
        _c("IT", "A3", "Выручка"),
        _c("IT", "B3", "10"),
        _c("IT", "C3", "11"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert sheet.axes == []


def test_two_full_quarter_labels_stay_an_axis() -> None:
    cells = [_c("IT", "A2", "Выручка")]
    for col, label in zip("BC", ["1 кв. 2024", "2 кв. 2024"], strict=True):
        cells.append(_c("IT", f"{col}1", label))
        cells.append(_c("IT", f"{col}2", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == ["2024Q1", "2024Q2"]


def test_a_note_between_the_name_and_the_quarters_is_not_the_section() -> None:
    cells = [
        _c("P", "A1", "Выручка"),
        _c("P", "B1", "2024"),
        _c("P", "A2", "комментарий"),
        _c("P", "A4", "Себестоимость"),
    ]
    for col, quarter in zip("BCDE", ["1 кв.", "2 кв.", "3 кв.", "4 кв."], strict=True):
        cells.append(_c("P", f"{col}3", quarter))
        cells.append(_c("P", f"{col}4", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
    ]
    assert sheet.blocks[0].rows[0].section_path == ["Выручка"]


def test_a_year_beside_a_label_does_not_section_a_year_ruler() -> None:
    cells = [_c("P", "A1", "Остаток"), _c("P", "B1", "2024"), _c("P", "A3", "Деньги")]
    for col, year in zip("BCDE", ["2020", "2021", "2022", "2023"], strict=True):
        cells.append(_c("P", f"{col}2", year))
        cells.append(_c("P", f"{col}3", "10"))
    sheet = detect_layout(cells).sheets[0]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2020",
        "2021",
        "2022",
        "2023",
    ]
    assert sheet.blocks[0].rows[0].label == "Деньги"
    assert sheet.blocks[0].rows[0].section_path == []


def test_transposed_lines_do_not_take_the_label_row() -> None:
    cells = [
        _c("Debt", "B1", "Выручка"),
        _c("Debt", "C1", "EBITDA"),
        _c("Debt", "A2", "2020"),
        _c("Debt", "B2", "10"),
        _c("Debt", "C2", "20"),
        _c("Debt", "A3", "2021"),
        _c("Debt", "B3", "11"),
        _c("Debt", "C3", "21"),
        _c("Debt", "A4", "2022"),
        _c("Debt", "B4", "12"),
        _c("Debt", "C4", "22"),
    ]
    layout = detect_layout(cells)
    sheet = layout.sheets[0]
    assert [row.value_col for row in sheet.blocks[0].rows] == [2, 3]
    index = _CellIndex(layout)
    assert ("Debt", 1) not in index.row_index
    assert formula_relations(layout, cells) == []
    mixed = Layout(
        sheets=[
            SheetLayout(
                name="Debt",
                blocks=[
                    *sheet.blocks,
                    Block(
                        block_id="Debt!r10",
                        label_col=1,
                        rows=[LayoutRow(row=1, label="Горизонтальная")],
                    ),
                ],
                axes=sheet.axes,
            )
        ]
    )
    kept = _CellIndex(mixed).row_index[("Debt", 1)]
    assert kept[1].label == "Горизонтальная"
