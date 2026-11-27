from __future__ import annotations

import pytest
from tests.helpers.published import published_context
from tests.layout.test_detect_rows import _c

from finance_context.excel.a1 import index_to_col
from finance_context.graph.stage import _local_value_cells, _side_cells
from finance_context.layout.detect import detect_layout


def _publish(cells):
    layout = detect_layout(cells)
    context = published_context(
        job_id="side-tables",
        workbook_meta={"sheets": [{"name": "Model"}]},
        cells=cells,
        layout=layout,
    )
    return layout, context


def _observations(context, row):
    axes = {axis.id: axis for axis in context.axes}
    return [
        (f"{index_to_col(period.col)}{row.row}", period.period_key, value)
        for series in row.series
        for period, value in zip(axes[series.axis_id].periods, series.values, strict=True)
        if value is not None
    ]


@pytest.mark.parametrize("right_header", [1, 3])
def test_side_tables_keep_their_labels_values_and_sections(right_header):
    cells = [
        _c("Model", "A1", "Item"),
        _c("Model", "A2", "Left section"),
        _c("Model", f"F{right_header}", "Name"),
        _c("Model", "F4" if right_header == 1 else "G2", "Right section"),
        _c("Model", "A5", "Left amount"),
        _c("Model", "F5", "Right amount"),
        _c("Model", "A6", "Left only"),
        _c("Model", "F7", "Right only"),
        _c("Model", "F11", "Later right amount"),
    ]
    for offset, col in enumerate("BCDE"):
        cells.append(_c("Model", f"{col}1", str(2020 + offset)))
        if right_header == 3:
            cells.append(_c("Model", f"{col}3", str(10 + offset)))
        cells.append(_c("Model", f"{col}5", str(20 + offset)))
        cells.append(_c("Model", f"{col}6", str(30 + offset)))
    if right_header == 3:
        cells.append(_c("Model", "A3", "Left during right header"))
    for offset, col in enumerate("GHI"):
        cells.append(_c("Model", f"{col}{right_header}", f"{offset + 1} кв. 2025"))
        cells.append(_c(
            "Model", f"{col}5", str(40 + offset),
            formula=f"={'BCD'[offset]}5*2", formula_template="=RC[-5]*2",
        ))
        cells.append(_c("Model", f"{col}7", str(50 + offset)))
        cells.append(_c("Model", f"{col}10", f"{offset + 1} кв. 2027"))
        cells.append(_c("Model", f"{col}11", str(60 + offset)))

    layout, context = _publish(cells)
    sheet = layout.sheets[0]
    layout_rows = {
        (row.row, row.label_col): (row.label, row.parent_row)
        for block in sheet.blocks
        if block.kind == "timeline"
        for row in block.rows
    }
    assert layout_rows[5, 1] == ("Left amount", 2)
    assert layout_rows[5, 6] == ("Right amount", 4 if right_header == 1 else 2)
    assert {col for row, col in layout_rows if row == 6} == {1}
    assert {col for row, col in layout_rows if row == 7} == {6}
    rows = {
        row.label: row
        for block in context.blocks
        if block.kind == "timeline"
        for row in block.rows
    }
    for label, section, columns, periods, values in (
        ("Left amount", "Left section", "BCDE", ["2020", "2021", "2022", "2023"], range(20, 24)),
        ("Right amount", "Right section", "GHI", ["2025Q1", "2025Q2", "2025Q3"], range(40, 43)),
    ):
        row = rows[label]
        assert row.label_path == [section]
        assert _observations(context, row) == [
            (f"{col}5", period, str(value))
            for col, period, value in zip(columns, periods, values, strict=True)
        ]
    assert rows["Right amount"].series[0].formula == "=RC[-5]*2"
    owners = _side_cells(layout)
    assert owners["Model", 5, 2] == rows["Left amount"].row_key
    assert owners["Model", 5, 7] == rows["Right amount"].row_key
    periods = _local_value_cells(layout)
    assert periods["Model", 5, 7] == (rows["Right amount"].row_key, "2025Q1")
    assert periods["Model", 7, 7] == (rows["Right only"].row_key, "2025Q1")
    if right_header == 3:
        row = rows["Left during right header"]
        assert _observations(context, row)[0] == ("B3", "2020", "10")


@pytest.mark.parametrize("value", [None, "29", "RUR"])
def test_formula_name_does_not_extend_nested_axis_coverage(value):
    cells = [_c("Model", "A2", "Wide before"), _c("Model", "A6", "Wide after")]
    for offset, col in enumerate("BCDEFGH"):
        cells.extend([
            _c("Model", f"{col}1", str(2020 + offset)),
            _c("Model", f"{col}2", "10"),
            _c("Model", f"{col}6", "20"),
        ])
    for offset, col in enumerate("EFG"):
        cells.append(_c("Model", f"{col}3", f"{offset + 1} кв. 2024"))
        cells.append(_c("Model", f"{col}4", str(30 + offset)))
    if value is not None:
        formula = '=D2+19' if value == "29" else '="RUR"'
        cells.extend([_c("Model", "A4", "Wide amount"), _c("Model", "D4", value, formula=formula)])
    else:
        cells.append(_c("Model", "D4", "Quarter amount", formula='"Quarter amount"'))

    layout, context = _publish(cells)
    layout_rows = [row for block in layout.sheets[0].blocks for row in block.rows if row.row == 4]
    assert [(row.label_col, row.label) for row in layout_rows] == [
        (1, "Wide amount") if value is not None else (4, "Quarter amount")
    ]
    [row] = [row for block in context.blocks for row in block.rows if row.row == 4]
    expected = (
        [("D4", "2022", value), ("E4", "2023", "30"), ("F4", "2024", "31"), ("G4", "2025", "32")]
        if value is not None
        else [("E4", "2024Q1", "30"), ("F4", "2024Q2", "31"), ("G4", "2024Q3", "32")]
    )
    assert _observations(context, row) == expected
