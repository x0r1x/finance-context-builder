"""A bounds word in the line name must not swallow the first value row.

``начало периода`` / ``конец периода`` make ``_row_layers`` return ``bounds``,
so ``_is_data_row`` is false. The band used to take that row. Numeric formula
caches in the axis columns are values. A formula cache of quarter text is
still a header.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from tests.helpers.published import published_context as build_context
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel import parse_workbook
from finance_context.excel.a1 import format_addr, index_to_col, parse_addr
from finance_context.formulas import compile_workbook
from finance_context.layout.detect import detect_layout
from finance_context.layout.periods import LAYER_BOUNDS, classify_atom
from finance_context.layout.rows import _is_data_row, _row_layers
from finance_context.layout.stage import _load_layout_sheet_cells


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


def test_underscore_serial_with_date_format_does_not_move_the_axis() -> None:
    cells = _year_axis_with_tail("4_2025", number_format="DD.MM.YYYY")
    doc, rows = _published_rows(cells, "underscore-serial")
    assert len(doc.axes) == 1
    assert [period.period_key for period in doc.axes[0].periods] == [
        str(year) for year in range(2018, 2027)
    ]
    assert rows[2].values[-1] == "4_2025"
    assert format_addr(doc.axes[0].periods[-1].col, 2) == "J2"
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


def _year_row_with_tails(tails: list[tuple[str, str | None]]) -> list[dict]:
    """Years 2018–2026, seven amounts, then calendar tails, then another fact."""
    sheet = "Debt"
    cells = [_c(sheet, "A1", "Показатель")]
    for index, year in enumerate(range(2018, 2027)):
        cells.append(_c(sheet, f"{chr(ord('B') + index)}1", str(year)))
    cells.append(_c(sheet, "A2", "Остаток на начало периода"))
    for index, value in enumerate(["10", "20", "30", "40", "50", "60", "70"]):
        cells.append(_c(sheet, f"{chr(ord('B') + index)}2", value))
    for offset, (text, number_format) in enumerate(tails):
        cells.append(
            _c(
                sheet,
                f"{chr(ord('I') + offset)}2",
                text,
                number_format=number_format,
            )
        )
    cells.append(_c(sheet, "A3", "Следующая"))
    for index, value in enumerate(
        ["110", "120", "130", "140", "150", "160", "170", "180", "190"]
    ):
        cells.append(_c(sheet, f"{chr(ord('B') + index)}3", value))
    return cells


@pytest.mark.parametrize(
    ("tails", "published"),
    [
        ([("31.03.2026", None), ("4 кв. 2026", None)], ["31.03.2026", "4 кв. 2026"]),
        ([("46112", "DD.MM.YYYY"), ("4 кв. 2026", None)], ["31.03.2026", "4 кв. 2026"]),
    ],
)
def test_calendar_cells_among_sums_keep_the_year_axis(
    tails: list[tuple[str, str | None]], published: list[str]
) -> None:
    cells = _year_row_with_tails(tails)
    doc, rows = _published_rows(cells, "two-calendar")
    assert len(doc.axes) == 1
    axis = doc.axes[0]
    assert [period.period_key for period in axis.periods] == [
        str(year) for year in range(2018, 2027)
    ]
    assert rows[2].kind == "fact"
    assert list(rows[2].values[:7]) == ["10", "20", "30", "40", "50", "60", "70"]
    assert list(rows[2].values[-2:]) == published
    assert [format_addr(period.col, 3) for period in axis.periods[-2:]] == ["I3", "J3"]
    assert [period.period_key for period in axis.periods[-2:]] == ["2025", "2026"]
    assert list(rows[3].values) == [
        "110",
        "120",
        "130",
        "140",
        "150",
        "160",
        "170",
        "180",
        "190",
    ]


def test_two_calendar_cells_keep_the_quarter_axis() -> None:
    sheet = "CF"
    labels = ["4 кв. 2015", "1 кв. 2016", "2 кв. 2016", "3 кв. 2016", "4 кв. 2016", "1 кв. 2017"]
    cells = [_c(sheet, "A1", "Статья")]
    for col, label in zip("BCDEFG", labels, strict=True):
        cells.append(_c(sheet, f"{col}1", label))
    cells.append(_c(sheet, "A2", "Остаток на начало периода"))
    for col, value in zip("BCDE", ["10", "20", "30", "40"], strict=True):
        cells.append(_c(sheet, f"{col}2", value))
    cells.append(_c(sheet, "F2", "31.03.2026"))
    cells.append(_c(sheet, "G2", "4 кв. 2026"))
    cells.append(_c(sheet, "A3", "Следующая"))
    for col, value in zip("BCDEFG", ["15", "25", "35", "45", "55", "65"], strict=True):
        cells.append(_c(sheet, f"{col}3", value))

    doc, rows = _published_rows(cells, "two-quarter")
    assert len(doc.axes) == 1
    axis = doc.axes[0]
    assert [period.period_key for period in axis.periods] == [
        "2015Q4",
        "2016Q1",
        "2016Q2",
        "2016Q3",
        "2016Q4",
        "2017Q1",
    ]
    assert rows[2].kind == "fact"
    assert list(rows[2].values) == ["10", "20", "30", "40", "31.03.2026", "4 кв. 2026"]
    assert list(rows[3].values) == ["15", "25", "35", "45", "55", "65"]
    assert [period.period_key for period in axis.periods[-2:]] == ["2016Q4", "2017Q1"]


def test_explicit_zero_stays_zero() -> None:
    sheet = "Debt"
    cells = [_c(sheet, "A1", "Показатель")]
    for col, year in zip("BCDE", ["2024", "2025", "2026", "2027"], strict=True):
        cells.append(_c(sheet, f"{col}1", year))
    cells.append(_c(sheet, "A2", "Остаток на конец периода"))
    for col, value in zip("BCDE", ["0", "1", "2", "3"], strict=True):
        cells.append(_c(sheet, f"{col}2", value))
    doc, rows = _published_rows(cells, "zero")
    assert [period.period_key for period in doc.axes[0].periods] == [
        "2024",
        "2025",
        "2026",
        "2027",
    ]
    assert rows[2].value_statuses[0] == "zero_explicit"
    assert rows[2].values[0] == "0"


def test_formula_quarter_cache_stays_in_the_header() -> None:
    sheet = "CF"
    cells = [_c(sheet, "A1", "Статья")]
    for col, year in zip("BCDE", ["2024", "2024", "2025", "2025"], strict=True):
        cells.append(_c(sheet, f"{col}1", year))
    cells.append(_c(sheet, "A2", "Квартал"))
    for col, text in zip("BCDE", ["1 кв.", "2 кв.", "3 кв.", "4 кв."], strict=True):
        cells.append(_c(sheet, f"{col}2", text, formula=f"={col}20"))
    cells.append(_c(sheet, "A3", "Выручка"))
    for col, value in zip("BCDE", ["10", "20", "30", "40"], strict=True):
        cells.append(_c(sheet, f"{col}3", value))
    doc, rows = _published_rows(cells, "formula-quarters")
    assert [period.period_key for period in doc.axes[0].periods] == _QUARTER_KEYS
    assert 2 not in rows
    assert list(rows[3].values) == ["10", "20", "30", "40"]


def test_formula_outside_the_axis_does_not_publish_a_value_row() -> None:
    sheet = "Debt"
    cells = [_c(sheet, "A1", "Показатель")]
    for col, year in zip("BCDE", ["2024", "2025", "2026", "2027"], strict=True):
        cells.append(_c(sheet, f"{col}1", year))
    cells.append(_c(sheet, "A2", "Раздел"))
    cells.append(_c(sheet, "F2", None, formula="=F10"))
    cells.append(_c(sheet, "A3", "Выручка"))
    for col, value in zip("BCDE", ["10", "20", "30", "40"], strict=True):
        cells.append(_c(sheet, f"{col}3", value))
    doc, rows = _published_rows(cells, "note-formula")
    assert [period.period_key for period in doc.axes[0].periods] == [
        "2024",
        "2025",
        "2026",
        "2027",
    ]
    assert list(rows[3].values) == ["10", "20", "30", "40"]
    if 2 in rows:
        assert all(value in (None, "") for value in rows[2].values)


def _sheet_xml(path: Path) -> str:
    with zipfile.ZipFile(path) as book:
        return book.read("xl/worksheets/sheet1.xml").decode("utf-8")


def _published_xlsx(tmp_path: Path, source: Path, sheet: str):
    dest = tmp_path / "job"
    dest.mkdir()
    parse_workbook(source, dest)
    compile_workbook(dest)
    _names, groups = _load_layout_sheet_cells(dest)
    cells = list(groups[sheet])
    doc = build_context(
        job_id="book",
        workbook_meta={"sheets": [{"name": sheet}]},
        cells=cells,
        layout=detect_layout(cells),
    )
    rows = {row.row: row for block in doc.blocks for row in block.rows}
    return doc, rows, cells


@pytest.mark.parametrize(
    ("mode", "snippet"),
    [
        ("absent", '<c r="B2"><f>=B10</f></c>'),
        ("empty", '<c r="B2"><f>=B10</f><v></v></c>'),
        ("str", '<c r="B2" t="str"><f>=B10</f><v></v></c>'),
    ],
)
def test_blank_formula_cache_is_published(tmp_path: Path, mode: str, snippet: str) -> None:
    strings = ["Показатель", "Остаток на конец периода", "Выручка"]
    formula_cells = []
    for col in "BCDE":
        kwargs: dict[str, object] = {"formula": f"={col}10"}
        if mode == "empty":
            kwargs["empty_cache"] = True
        elif mode == "str":
            kwargs["empty_cache"] = True
            kwargs["type"] = "str"
        formula_cells.append(CellSpec(addr=f"{col}2", **kwargs))
    source = build_xlsx(
        tmp_path / "cache.xlsx",
        sheets=[
            SheetSpec(
                name="Debt",
                cells=[
                    CellSpec(addr="A1", value="Показатель"),
                    *[
                        CellSpec(addr=f"{col}1", value=year)
                        for col, year in zip("BCDE", ["2024", "2025", "2026", "2027"], strict=True)
                    ],
                    CellSpec(addr="A2", value="Остаток на конец периода"),
                    *formula_cells,
                    CellSpec(addr="A3", value="Выручка"),
                    *[
                        CellSpec(addr=f"{col}3", value=value)
                        for col, value in zip("BCDE", ["10", "20", "30", "40"], strict=True)
                    ],
                ],
            )
        ],
        shared_strings=strings,
    )
    xml = _sheet_xml(source)
    assert snippet in xml
    if mode == "absent":
        start = xml.index('<c r="B2">')
        cell_xml = xml[start : xml.index("</c>", start) + 4]
        assert "<v>" not in cell_xml

    doc, rows, cells = _published_xlsx(tmp_path, source, "Debt")
    by_addr = {cell["addr"]: cell for cell in cells}
    assert by_addr["B2"]["formula_raw"] == "=B10"
    assert by_addr["E2"]["formula_raw"] == "=E10"
    assert [period.period_key for period in doc.axes[0].periods] == [
        "2024",
        "2025",
        "2026",
        "2027",
    ]
    opening = rows[2]
    assert opening.kind == "fact"
    assert list(opening.value_statuses) == ["empty", "empty", "empty", "empty"]
    assert opening.formula
    assert list(rows[3].values) == ["10", "20", "30", "40"]


def test_merged_year_layer_builds_one_quarter_axis(tmp_path: Path) -> None:
    strings = ["Статья", "1 кв.", "2 кв.", "3 кв.", "4 кв.", "Год", "Выручка", "2024", "2025"]
    source = build_xlsx(
        tmp_path / "merged.xlsx",
        sheets=[
            SheetSpec(
                name="CF",
                merges=["B2:C2", "D2:E2"],
                cells=[
                    CellSpec(addr="A1", value="Статья"),
                    CellSpec(addr="B1", value="1 кв."),
                    CellSpec(addr="C1", value="2 кв."),
                    CellSpec(addr="D1", value="3 кв."),
                    CellSpec(addr="E1", value="4 кв."),
                    CellSpec(addr="A2", value="Год"),
                    CellSpec(addr="B2", value="2024"),
                    CellSpec(addr="D2", value="2025"),
                    CellSpec(addr="A3", value="Выручка"),
                    CellSpec(addr="B3", value="10"),
                    CellSpec(addr="C3", value="20"),
                    CellSpec(addr="D3", value="30"),
                    CellSpec(addr="E3", value="40"),
                ],
            )
        ],
        shared_strings=strings,
    )
    assert 'mergeCell ref="B2:C2"' in _sheet_xml(source)
    doc, rows, _cells = _published_xlsx(tmp_path, source, "CF")
    assert len(doc.axes) == 1
    assert [period.period_key for period in doc.axes[0].periods] == _QUARTER_KEYS
    assert 2 not in rows
    assert list(rows[3].values) == ["10", "20", "30", "40"]
    assert [format_addr(period.col, 3) for period in doc.axes[0].periods] == [
        "B3",
        "C3",
        "D3",
        "E3",
    ]


def test_years_above_and_below_eight_quarters_stay_one_axis() -> None:
    sheet = "CF"
    quarters = ["1 кв.", "2 кв.", "3 кв.", "4 кв.", "1 кв.", "2 кв.", "3 кв.", "4 кв."]
    years = ["2024", "2024", "2024", "2024", "2025", "2025", "2025", "2025"]
    cells = []
    for col, year in zip("BCDEFGHI", years, strict=True):
        cells.append(_c(sheet, f"{col}1", year))
    for col, quarter in zip("BCDEFGHI", quarters, strict=True):
        cells.append(_c(sheet, f"{col}2", quarter))
    for col, year in zip("BCDEFGHI", years, strict=True):
        cells.append(_c(sheet, f"{col}3", year))
    cells.append(_c(sheet, "A4", "Выручка"))
    for col, value in zip("BCDEFGHI", ["1", "2", "3", "4", "5", "6", "7", "8"], strict=True):
        cells.append(_c(sheet, f"{col}4", value))
    doc, rows = _published_rows(cells, "eight")
    assert len(doc.axes) == 1
    assert [period.period_key for period in doc.axes[0].periods] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
        "2025Q1",
        "2025Q2",
        "2025Q3",
        "2025Q4",
    ]
    assert list(rows[4].values) == ["1", "2", "3", "4", "5", "6", "7", "8"]


def test_explicit_quarter_keeps_its_year_and_the_layer_fills_the_rest() -> None:
    sheet = "CF"
    cells = [
        _c(sheet, "A1", "Статья"),
        _c(sheet, "B1", "1 кв. 2024"),
        _c(sheet, "C1", "2 кв."),
        _c(sheet, "D1", "3 кв."),
        _c(sheet, "E1", "4 кв."),
        _c(sheet, "A2", "Год"),
        _c(sheet, "B2", "2024"),
        _c(sheet, "C2", "2024"),
        _c(sheet, "D2", "2025"),
        _c(sheet, "E2", "2025"),
        _c(sheet, "A3", "Выручка"),
        _c(sheet, "B3", "10"),
        _c(sheet, "C3", "20"),
        _c(sheet, "D3", "30"),
        _c(sheet, "E3", "40"),
    ]
    doc, rows = _published_rows(cells, "explicit")
    assert len(doc.axes) == 1
    assert [period.period_key for period in doc.axes[0].periods] == _QUARTER_KEYS
    assert 2 not in rows
    assert list(rows[3].values) == ["10", "20", "30", "40"]


def test_conflicting_year_does_not_replace_an_explicit_quarter() -> None:
    sheet = "CF"
    cells = [
        _c(sheet, "B1", "1 кв. 2024"),
        _c(sheet, "C1", "2 кв."),
        _c(sheet, "D1", "3 кв."),
        _c(sheet, "E1", "4 кв."),
        _c(sheet, "B2", "2025"),
        _c(sheet, "C2", "2025"),
        _c(sheet, "D2", "2025"),
        _c(sheet, "E2", "2025"),
        _c(sheet, "A3", "Выручка"),
        _c(sheet, "B3", "10"),
        _c(sheet, "C3", "20"),
        _c(sheet, "D3", "30"),
        _c(sheet, "E3", "40"),
    ]
    doc, _rows = _published_rows(cells, "conflict")
    assert [period.period_key for period in doc.axes[0].periods] == [
        "2024Q1",
        "2025Q2",
        "2025Q3",
        "2025Q4",
    ]


def test_year_caption_left_of_quarters_is_not_a_period() -> None:
    sheet = "CF"
    cells = [
        _c(sheet, "A1", "Отчет 2024"),
        _c(sheet, "B1", "1 кв."),
        _c(sheet, "C1", "2 кв."),
        _c(sheet, "D1", "3 кв."),
        _c(sheet, "E1", "4 кв."),
        _c(sheet, "A2", "Год"),
        _c(sheet, "B2", "2024"),
        _c(sheet, "C2", "2024"),
        _c(sheet, "D2", "2025"),
        _c(sheet, "E2", "2025"),
        _c(sheet, "A3", "Выручка"),
        _c(sheet, "B3", "10"),
        _c(sheet, "C3", "20"),
        _c(sheet, "D3", "30"),
        _c(sheet, "E3", "40"),
    ]
    doc, rows = _published_rows(cells, "caption")
    assert len(doc.axes) == 1
    assert [period.period_key for period in doc.axes[0].periods] == _QUARTER_KEYS
    assert [format_addr(period.col, 3) for period in doc.axes[0].periods] == [
        "B3",
        "C3",
        "D3",
        "E3",
    ]
    assert list(rows[3].values) == ["10", "20", "30", "40"]


def test_gapped_year_layer_keeps_every_amount() -> None:
    sheet = "CF"
    cells = [
        _c(sheet, "A1", "Статья"),
        _c(sheet, "B1", "1 кв."),
        _c(sheet, "C1", "2 кв."),
        _c(sheet, "D1", "3 кв."),
        _c(sheet, "E1", "4 кв."),
        _c(sheet, "A2", "Год"),
        _c(sheet, "B2", "2024"),
        _c(sheet, "C2", "2024"),
        _c(sheet, "E2", "2025"),
        _c(sheet, "A3", "Выручка"),
        _c(sheet, "B3", "10"),
        _c(sheet, "C3", "20"),
        _c(sheet, "D3", "30"),
        _c(sheet, "E3", "40"),
    ]
    doc, rows = _published_rows(cells, "gap")
    assert all("@" not in period.period_key for axis in doc.axes for period in axis.periods)
    revenue = rows[3]
    assert list(revenue.values) == ["10", "20", "30", "40"]
    sheet_layout = detect_layout(cells).sheets[0]
    block = next(item for item in sheet_layout.blocks if any(row.row == 3 for row in item.rows))
    assert [header.col for header in block.axis.headers] == [2, 3, 4, 5]


def test_incomplete_date_layer_keeps_the_open_year() -> None:
    sheet = "CF"
    cells = [_c(sheet, "A1", "Показатель")]
    for col, year in zip("BCDE", ["2024", "2025", "2026", "2027"], strict=True):
        cells.append(_c(sheet, f"{col}1", year))
    cells.append(_c(sheet, "A2", "Начало периода"))
    for col, text in zip("BCD", ["01.01.2024", "01.01.2025", "01.01.2026"], strict=True):
        cells.append(_c(sheet, f"{col}2", text))
    cells.append(_c(sheet, "E2", "4_2025", number_format="DD.MM.YYYY"))
    cells.append(_c(sheet, "A3", "Выручка"))
    for col, value in zip("BCDE", ["10", "20", "30", "40"], strict=True):
        cells.append(_c(sheet, f"{col}3", value))
    doc, rows = _published_rows(cells, "gpb-dates")
    assert 2 not in rows
    axis = doc.axes[0]
    assert [period.period_key for period in axis.periods] == ["2024", "2025", "2026", "2027"]
    assert [format_addr(period.col, 3) for period in axis.periods] == ["B3", "C3", "D3", "E3"]
    assert list(rows[3].values) == ["10", "20", "30", "40"]
    assert rows[3].values[-1] == "40"


def test_eight_quarters_with_one_explicit_quarter_and_merged_years(tmp_path: Path) -> None:
    quarters = ["1 кв. 2024", "2 кв.", "3 кв.", "4 кв.", "1 кв.", "2 кв.", "3 кв.", "4 кв."]
    strings = ["Статья", "Год", "Выручка", "2024", "2025", *quarters]
    source = build_xlsx(
        tmp_path / "eight.xlsx",
        sheets=[
            SheetSpec(
                name="CF",
                merges=["B2:E2", "F2:I2"],
                cells=[
                    CellSpec(addr="A1", value="Статья"),
                    *[
                        CellSpec(addr=f"{col}1", value=text)
                        for col, text in zip("BCDEFGHI", quarters, strict=True)
                    ],
                    CellSpec(addr="A2", value="Год"),
                    CellSpec(addr="B2", value="2024"),
                    CellSpec(addr="F2", value="2025"),
                    CellSpec(addr="A3", value="Выручка"),
                    *[
                        CellSpec(addr=f"{col}3", value=str(index))
                        for index, col in enumerate("BCDEFGHI", start=1)
                    ],
                ],
            )
        ],
        shared_strings=strings,
    )
    doc, rows, _cells = _published_xlsx(tmp_path, source, "CF")
    assert len(doc.axes) == 1
    assert [period.period_key for period in doc.axes[0].periods] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
        "2025Q1",
        "2025Q2",
        "2025Q3",
        "2025Q4",
    ]
    assert 2 not in rows
    assert list(rows[3].values) == ["1", "2", "3", "4", "5", "6", "7", "8"]


def _axis_and_row(doc, row_n: int):
    block = next(item for item in doc.blocks if any(row.row == row_n for row in item.rows))
    axis = next(item for item in doc.axes if item.id == block.axis_ids[0])
    row = next(item for item in block.rows if item.row == row_n)
    return axis, row


def test_later_year_axis_survives_partial_column_overlap() -> None:
    """Full 2024 quarters do not own an independent 2030–2032 ruler below them."""
    sheet = "CF"
    cells = [_c(sheet, "A1", "Статья")]
    quarters = ["1 кв. 2024", "2 кв. 2024", "3 кв. 2024", "4 кв. 2024"]
    for col, text in zip("BCDE", quarters, strict=True):
        cells.append(_c(sheet, f"{col}1", text))
    cells.append(_c(sheet, "A2", "Факт"))
    for col, value in zip("BCDE", ["10", "20", "30", "40"], strict=True):
        cells.append(_c(sheet, f"{col}2", value))
    cells.append(_c(sheet, "A12", "Горизонт"))
    for col, year in zip("CDE", ["2030", "2031", "2032"], strict=True):
        cells.append(_c(sheet, f"{col}12", year))
    cells.append(_c(sheet, "A13", "Ввод"))
    for col, value in zip("CDE", ["7", "8", "9"], strict=True):
        cells.append(_c(sheet, f"{col}13", value))

    doc, _rows = _published_rows(cells, "later-years")
    assert len(doc.axes) == 2
    upper, upper_row = _axis_and_row(doc, 2)
    lower, lower_row = _axis_and_row(doc, 13)
    assert [period.period_key for period in upper.periods] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
    ]
    assert list(upper_row.values) == ["10", "20", "30", "40"]
    assert [period.period_key for period in lower.periods] == ["2030", "2031", "2032"]
    assert [format_addr(period.col, 13) for period in lower.periods] == ["C13", "D13", "E13"]
    assert list(lower_row.values) == ["7", "8", "9"]


def test_later_year_axis_survives_when_upper_quarters_still_need_a_year() -> None:
    """A lower ruler is not the year layer of bare quarters that sit far above it."""
    sheet = "CF"
    cells = [_c(sheet, "A1", "Статья")]
    for col, text in zip("BCDE", ["1 кв.", "2 кв.", "3 кв.", "4 кв."], strict=True):
        cells.append(_c(sheet, f"{col}1", text))
    cells.append(_c(sheet, "A2", "Факт"))
    for col, value in zip("BCDE", ["10", "20", "30", "40"], strict=True):
        cells.append(_c(sheet, f"{col}2", value))
    for col, year in zip("CDE", ["2030", "2031", "2032"], strict=True):
        cells.append(_c(sheet, f"{col}12", year))
    cells.append(_c(sheet, "A13", "Ввод"))
    for col, value in zip("CDE", ["7", "8", "9"], strict=True):
        cells.append(_c(sheet, f"{col}13", value))

    doc, _rows = _published_rows(cells, "later-bare")
    lower_axis, lower_row = _axis_and_row(doc, 13)
    assert [period.period_key for period in lower_axis.periods] == ["2030", "2031", "2032"]
    assert list(lower_row.values) == ["7", "8", "9"]


def test_right_header_on_a_left_fact_row_stays_an_axis() -> None:
    """Numbers in the left table do not erase a quarter header on the same row."""
    sheet = "CF"
    cells = [_c(sheet, "A1", "Показатель")]
    for col, year in zip("BCDE", ["2020", "2021", "2022", "2023"], strict=True):
        cells.append(_c(sheet, f"{col}1", year))
    cells.append(_c(sheet, "A2", "Выручка"))
    for col, value in zip("BCDE", ["10", "20", "30", "40"], strict=True):
        cells.append(_c(sheet, f"{col}2", value))
    cells.append(_c(sheet, "A3", "Себестоимость"))
    for col, value in zip("BCDE", ["1", "2", "3", "4"], strict=True):
        cells.append(_c(sheet, f"{col}3", value))
    for col, text in zip(
        "GHIJ", ["1 кв. 2024", "2 кв. 2024", "3 кв. 2024", "4 кв. 2024"], strict=True
    ):
        cells.append(_c(sheet, f"{col}3", text))
    cells.append(_c(sheet, "F4", "Правый показатель"))
    for col, value in zip("GHIJ", ["100", "200", "300", "400"], strict=True):
        cells.append(_c(sheet, f"{col}4", value))

    doc, _rows = _published_rows(cells, "side-header")
    assert len(doc.axes) == 2
    left, left_row = _axis_and_row(doc, 3)
    right, right_row = _axis_and_row(doc, 4)
    assert [period.period_key for period in left.periods] == ["2020", "2021", "2022", "2023"]
    assert [format_addr(period.col, 3) for period in left.periods] == ["B3", "C3", "D3", "E3"]
    assert list(left_row.values) == ["1", "2", "3", "4"]
    opening, _opening_row = _axis_and_row(doc, 2)
    assert opening.id == left.id
    assert list(_opening_row.values) == ["10", "20", "30", "40"]
    assert [period.period_key for period in right.periods] == [
        "2024Q1",
        "2024Q2",
        "2024Q3",
        "2024Q4",
    ]
    assert [format_addr(period.col, 4) for period in right.periods] == ["G4", "H4", "I4", "J4"]
    assert list(right_row.values) == ["100", "200", "300", "400"]


def test_wide_quarter_header_classifies_about_linearly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Doubling a quarter header doubles classification, it does not square it."""
    calls = 0
    real = classify_atom

    def counting(text: str | None, *args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return real(text, *args, **kwargs)

    monkeypatch.setattr("finance_context.layout.axes.classify_atom", counting)

    def measured(width: int) -> int:
        nonlocal calls
        calls = 0
        cells = [_c("CF", "A1", "Отчет 2024")]
        for index in range(width):
            col = index_to_col(2 + index)
            quarter = (index % 4) + 1
            year = 2024 + index // 4
            cells.append(_c("CF", f"{col}1", f"{quarter} кв. {year}"))
        cells.append(_c("CF", "A2", "Выручка"))
        for index in range(width):
            cells.append(_c("CF", f"{index_to_col(2 + index)}2", str(index + 1)))
        layout = detect_layout(cells).sheets[0]
        axis = layout.axes[0]
        # ``Отчет 2024`` stays a caption. Years stop at 2099, so a 320-column
        # ruler keeps every in-range quarter and drops the rest.
        assert axis.periods[0].col == 2
        assert axis.periods[0].period_key == "2024Q1"
        kept = sum(1 for index in range(width) if 2024 + index // 4 <= 2099)
        assert len(axis.periods) == kept
        assert axis.periods[-1].col == 1 + kept
        return calls

    small = measured(80)
    middle = measured(160)
    large = measured(320)
    assert middle < small * 3, (small, middle, large)
    assert large < middle * 3, (small, middle, large)
