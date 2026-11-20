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
        "number_format": None,
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
