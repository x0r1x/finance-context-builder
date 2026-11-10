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
