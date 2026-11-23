"""A bounds word in the line name must not swallow the first value row.

``начало периода`` / ``конец периода`` make ``_row_layers`` return ``bounds``,
so ``_is_data_row`` is false. The band used to take that row. Numeric formula
caches in the axis columns are values. A formula cache of quarter text is
still a header.
"""

from __future__ import annotations

import pytest
from tests.helpers.published import published_context as build_context

from finance_context.excel.a1 import format_addr, parse_addr
from finance_context.layout.detect import detect_layout
from finance_context.layout.periods import LAYER_BOUNDS
from finance_context.layout.rows import _is_data_row, _row_layers


def _c(
    sheet: str,
    addr: str,
    value: str | None,
    *,
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
        "hidden": False,
        "formula_raw": formula,
        "formula_template": None,
        "unparsed": False,
        "number_format": number_format,
        "comment": None,
    }


def _quarter_cells(label: str) -> list[dict]:
    """Formula quarters, then a bounds-named row of numeric formula caches.

    Column B is an empty position under a real quarter. Column E is empty too.
    """
    sheet = "CF"
    quarters = [
        ("B1", "4 кв. 2015"),
        ("C1", "1 кв. 2016"),
        ("D1", "2 кв. 2016"),
        ("E1", "3 кв. 2016"),
    ]
    cells = [_c(sheet, "A1", "ОСНОВНЫЕ СРЕДСТВА")]
    for addr, text in quarters:
        cells.append(_c(sheet, addr, text, formula=f"={addr[0]}$20"))
    cells.append(_c(sheet, "A2", label))
    cells.append(_c(sheet, "C2", "41043021", formula="=C40"))
    cells.append(_c(sheet, "D2", "43663021", formula="=D40"))
    cells.append(_c(sheet, "A3", "Приобретение основных средств"))
    for col, value in zip("BCDE", ["1", "2", "3", "4"], strict=True):
        cells.append(_c(sheet, f"{col}3", value, formula=f"={col}41"))
    return cells


@pytest.mark.parametrize(
    "label",
    [
        "Остаток на начало периода",
        "Все займы и кредиты на конец периода",
    ],
)
def test_numeric_formula_row_under_formula_quarters_is_published(label: str) -> None:
    cells = _quarter_cells(label)
    opening = [cell for cell in cells if cell["row"] == 2]
    assert _row_layers(opening, False) == {LAYER_BOUNDS}
    assert _is_data_row(opening, False) is False

    sheet = detect_layout(cells).sheets[0]
    assert [axis.header_row for axis in sheet.axes] == [1]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        "2015Q4",
        "2016Q1",
        "2016Q2",
        "2016Q3",
    ]
    timeline = next(block for block in sheet.blocks if block.kind == "timeline")
    assert timeline.axis is not None and timeline.axis.row == 1
    assert [(row.row, row.label, row.kind) for row in timeline.rows] == [
        (2, label, "fact"),
        (3, "Приобретение основных средств", "fact"),
    ]

    doc = build_context(
        job_id="quarters",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=cells,
        layout=detect_layout(cells),
    )
    axis = doc.axes[0]
    row = next(item for block in doc.blocks for item in block.rows if item.row == 2)
    assert axis.header_row == 1
    assert [
        (format_addr(period.col, row.row), period.period_key, value, status)
        for period, value, status in zip(
            axis.periods, row.values, row.value_statuses, strict=True
        )
    ] == [
        ("B2", "2015Q4", None, "empty"),
        ("C2", "2016Q1", "41043021", "cached"),
        ("D2", "2016Q2", "43663021", "cached"),
        ("E2", "2016Q3", None, "empty"),
    ]


def _year_cells() -> list[dict]:
    """Years 2018–2026, a bounds-named first value row, and a ХХХХ stub."""
    sheet = "Debt"
    years = [str(year) for year in range(2018, 2027)]
    values = ["61000", "59000", "52000", "45000", "38000", "30000", "22000", "15000", "10055"]
    cells = [_c(sheet, "A1", "ВЭБ"), _c(sheet, "K1", "ХХХХ")]
    for index, year in enumerate(years):
        col = chr(ord("B") + index)
        cells.append(_c(sheet, f"{col}1", year))
    cells.append(
        _c(sheet, "A2", "Остаток задолженности по основному кредиту на начало периода")
    )
    for index, value in enumerate(values):
        col = chr(ord("B") + index)
        formula = None if index == 0 else f"={col}40"
        cells.append(_c(sheet, f"{col}2", value, formula=formula))
    cells.append(_c(sheet, "K2", "999", formula="=K40"))
    cells.append(_c(sheet, "A3", "Привлечение"))
    for index in range(9):
        col = chr(ord("B") + index)
        cells.append(_c(sheet, f"{col}3", "0"))
    return cells


def test_first_debt_row_keeps_the_year_axis_and_drops_the_stub() -> None:
    cells = _year_cells()
    opening = [cell for cell in cells if cell["row"] == 2]
    assert _row_layers(opening, False) == {LAYER_BOUNDS}
    assert _is_data_row(opening, False) is False

    sheet = detect_layout(cells).sheets[0]
    assert [axis.header_row for axis in sheet.axes] == [1]
    assert [period.period_key for period in sheet.axes[0].periods] == [
        str(year) for year in range(2018, 2027)
    ]
    assert all(period.col != parse_addr("K1")[0] for period in sheet.axes[0].periods)
    timeline = next(block for block in sheet.blocks if block.kind == "timeline")
    assert [(row.row, row.kind) for row in timeline.rows] == [(2, "fact"), (3, "fact")]

    doc = build_context(
        job_id="years",
        workbook_meta={"sheets": [{"name": "Debt"}]},
        cells=cells,
        layout=detect_layout(cells),
    )
    axis = doc.axes[0]
    row = next(item for block in doc.blocks for item in block.rows if item.row == 2)
    assert axis.header_row == 1
    published = [
        (format_addr(period.col, row.row), period.period_key, value, status)
        for period, value, status in zip(
            axis.periods, row.values, row.value_statuses, strict=True
        )
    ]
    assert published == [
        ("B2", "2018", "61000", "cached"),
        ("C2", "2019", "59000", "cached"),
        ("D2", "2020", "52000", "cached"),
        ("E2", "2021", "45000", "cached"),
        ("F2", "2022", "38000", "cached"),
        ("G2", "2023", "30000", "cached"),
        ("H2", "2024", "22000", "cached"),
        ("I2", "2025", "15000", "cached"),
        ("J2", "2026", "10055", "cached"),
    ]
    assert "999" not in row.values
    assert all(period.text != "ХХХХ" for period in axis.periods)


_QUARTER_KEYS = ["2024Q1", "2024Q2", "2025Q3", "2025Q4"]
_FULL_QUARTERS = ["1 кв. 2024", "2 кв. 2024", "3 кв. 2025", "4 кв. 2025"]


def _labeled_year_sheet(label: str, quarters: list[str]) -> list[dict]:
    """Quarters, a labelled year row, then revenue 10, 20, 30, 40."""
    sheet = "CF"
    cells = [_c(sheet, "A1", "Статья")]
    for col, quarter in zip("BCDE", quarters, strict=True):
        cells.append(_c(sheet, f"{col}1", quarter))
    cells.append(_c(sheet, "A2", label))
    for col, year in zip("BCDE", ["2024", "2024", "2025", "2025"], strict=True):
        cells.append(_c(sheet, f"{col}2", year))
    cells.append(_c(sheet, "A3", "Выручка"))
    for col, value in zip("BCDE", ["10", "20", "30", "40"], strict=True):
        cells.append(_c(sheet, f"{col}3", value))
    return cells


def test_labeled_year_under_bare_quarters_completes_one_axis() -> None:
    cells = _labeled_year_sheet("Год", ["1 кв.", "2 кв.", "3 кв.", "4 кв."])
    sheet = detect_layout(cells).sheets[0]
    assert len(sheet.axes) == 1
    assert sheet.axes[0].header_row == 2
    assert [period.period_key for period in sheet.axes[0].periods] == _QUARTER_KEYS
    assert [row.row for row in sheet.blocks[0].rows] == [3]

    doc = build_context(
        job_id="god",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=cells,
        layout=detect_layout(cells),
    )
    assert len(doc.axes) == 1
    axis = doc.axes[0]
    assert [period.period_key for period in axis.periods] == _QUARTER_KEYS
    assert all(row.row != 2 for block in doc.blocks for row in block.rows)
    revenue = next(row for block in doc.blocks for row in block.rows if row.row == 3)
    assert list(revenue.values) == ["10", "20", "30", "40"]
    assert [
        format_addr(period.col, revenue.row) for period in axis.periods
    ] == ["B3", "C3", "D3", "E3"]


def test_year_entry_under_complete_quarters_stays_one_fact() -> None:
    cells = _labeled_year_sheet("Год ввода", _FULL_QUARTERS)
    sheet = detect_layout(cells).sheets[0]
    assert len(sheet.axes) == 1
    assert sheet.axes[0].header_row == 1
    assert [period.period_key for period in sheet.axes[0].periods] == _QUARTER_KEYS
    assert [period.text for period in sheet.axes[0].periods] == _FULL_QUARTERS
    assert [(row.row, row.kind) for row in sheet.blocks[0].rows] == [
        (2, "fact"),
        (3, "fact"),
    ]

    doc = build_context(
        job_id="vvod",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=cells,
        layout=detect_layout(cells),
    )
    assert len(doc.axes) == 1
    axis = doc.axes[0]
    assert axis.header_row == 1
    assert [period.period_key for period in axis.periods] == _QUARTER_KEYS
    assert [period.text for period in axis.periods] == _FULL_QUARTERS
    by_row = {row.row: row for block in doc.blocks for row in block.rows}
    assert by_row[2].kind == "fact"
    assert list(by_row[2].values) == ["2024", "2024", "2025", "2025"]
    assert list(by_row[3].values) == ["10", "20", "30", "40"]
    assert [
        format_addr(period.col, 3) for period in axis.periods
    ] == ["B3", "C3", "D3", "E3"]


def test_unlabeled_year_layer_above_quarters_stays_one_axis() -> None:
    sheet = "CF"
    cells = []
    for col, year in zip("BCDE", ["2024", "2024", "2025", "2025"], strict=True):
        cells.append(_c(sheet, f"{col}1", year))
    for col, quarter in zip("BCDE", ["1 кв.", "2 кв.", "3 кв.", "4 кв."], strict=True):
        cells.append(_c(sheet, f"{col}2", quarter))
    cells.append(_c(sheet, "A3", "Выручка"))
    for col, value in zip("BCDE", ["10", "20", "30", "40"], strict=True):
        cells.append(_c(sheet, f"{col}3", value))

    layout = detect_layout(cells).sheets[0]
    assert len(layout.axes) == 1
    assert [period.period_key for period in layout.axes[0].periods] == _QUARTER_KEYS
    assert [row.row for row in layout.blocks[0].rows] == [3]


def _year_axis_with_tail(tail: str, *, number_format: str | None = None) -> list[dict]:
    """Years 2018–2026, a bounds row whose last cell is unusual, then another fact."""
    sheet = "Debt"
    cells = [_c(sheet, "A1", "Показатель")]
    for index, year in enumerate(range(2018, 2027)):
        cells.append(_c(sheet, f"{chr(ord('B') + index)}1", str(year)))
    cells.append(_c(sheet, "A2", "Остаток на начало периода"))
    for index, value in enumerate(["10", "20", "30", "40", "50", "60", "70", "80"]):
        cells.append(_c(sheet, f"{chr(ord('B') + index)}2", value))
    cells.append(_c(sheet, "J2", tail, number_format=number_format))
    cells.append(_c(sheet, "A3", "Следующая"))
    for index, value in enumerate(["110", "120", "130", "140", "150", "160", "170", "180", "190"]):
        cells.append(_c(sheet, f"{chr(ord('B') + index)}3", value))
    return cells


def _published_rows(cells: list[dict], job_id: str) -> tuple[object, dict[int, object]]:
    doc = build_context(
        job_id=job_id,
        workbook_meta={"sheets": [{"name": cells[0]["sheet"]}]},
        cells=cells,
        layout=detect_layout(cells),
    )
    rows = {row.row: row for block in doc.blocks for row in block.rows}
    return doc, rows


@pytest.mark.parametrize(
    ("tail", "number_format", "published"),
    [
        ("31.03.2026", None, "31.03.2026"),
        ("45747", "DD.MM.YYYY", "31.03.2025"),
        ("4 кв. 2026", None, "4 кв. 2026"),
    ],
)
def test_one_calendar_cell_among_sums_keeps_the_year_axis(
    tail: str, number_format: str | None, published: str
) -> None:
    cells = _year_axis_with_tail(tail, number_format=number_format)
    doc, rows = _published_rows(cells, "mixed-year")
    assert len(doc.axes) == 1
    axis = doc.axes[0]
    assert [period.period_key for period in axis.periods] == [
        str(year) for year in range(2018, 2027)
    ]
    assert rows[2].kind == "fact"
    assert rows[2].values[-1] == published
    assert format_addr(axis.periods[-1].col, rows[2].row) == "J2"
    assert list(rows[3].values) == ["110", "120", "130", "140", "150", "160", "170", "180", "190"]


def test_one_quarter_token_among_sums_keeps_the_quarter_axis() -> None:
    sheet = "CF"
    labels = ["4 кв. 2015", "1 кв. 2016", "2 кв. 2016", "3 кв. 2016"]
    cells = [_c(sheet, "A1", "Статья")]
    for col, label in zip("BCDE", labels, strict=True):
        cells.append(_c(sheet, f"{col}1", label))
    cells.append(_c(sheet, "A2", "Остаток на начало периода"))
    for col, value in zip("BCD", ["10", "20", "30"], strict=True):
        cells.append(_c(sheet, f"{col}2", value))
    cells.append(_c(sheet, "E2", "4 кв. 2026"))
    cells.append(_c(sheet, "A3", "Следующая"))
    for col, value in zip("BCDE", ["15", "25", "35", "45"], strict=True):
        cells.append(_c(sheet, f"{col}3", value))

    doc, rows = _published_rows(cells, "mixed-quarter")
    assert len(doc.axes) == 1
    axis = doc.axes[0]
    assert [period.period_key for period in axis.periods] == [
        "2015Q4",
        "2016Q1",
        "2016Q2",
        "2016Q3",
    ]
    assert rows[2].kind == "fact"
    assert rows[2].values[-1] == "4 кв. 2026"
    assert format_addr(axis.periods[-1].col, 2) == "E2"
    assert list(rows[3].values) == ["15", "25", "35", "45"]


def test_date_bounds_stay_header_and_the_neighbor_table_keeps_its_years() -> None:
    sheet = "CF"
    cells = [_c(sheet, "A1", "Показатель")]
    for col, year in zip("BCD", ["2018", "2019", "2020"], strict=True):
        cells.append(_c(sheet, f"{col}1", year))
    cells.append(_c(sheet, "A2", "Начало периода"))
    for col, text in zip("BCD", ["01.01.2018", "01.01.2019", "01.01.2020"], strict=True):
        cells.append(_c(sheet, f"{col}2", text))
    cells.append(_c(sheet, "A3", "Остаток на начало периода"))
    for col, value in zip("BCD", ["10", "20", "30"], strict=True):
        cells.append(_c(sheet, f"{col}3", value))
    for col, year in zip("BCD", ["2021", "2022", "2023"], strict=True):
        cells.append(_c(sheet, f"{col}5", year))
    cells.append(_c(sheet, "A6", "Выручка"))
    for col, value in zip("BCD", ["7", "8", "9"], strict=True):
        cells.append(_c(sheet, f"{col}6", value))

    doc, rows = _published_rows(cells, "bounds-neighbor")
    assert 2 not in rows
    opening = rows[3]
    assert opening.kind == "fact"
    assert list(opening.values) == ["10", "20", "30"]
    keys = [[period.period_key for period in axis.periods] for axis in doc.axes]
    assert ["2018", "2019", "2020"] in keys
    assert ["2021", "2022", "2023"] in keys
    assert list(rows[6].values) == ["7", "8", "9"]
