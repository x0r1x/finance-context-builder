"""Model-year headers. Three columns is the labeled-relative minimum.

A copied formula has to sit under the header. A gap, a repeat, constants, or a
copy somewhere else on the sheet leave the columns in the local block.
"""

from __future__ import annotations

from tests.helpers.published import published_context
from tests.layout.test_detect_axes import _c

from finance_context.layout.axes import _LABELED_RELATIVE_MIN
from finance_context.layout.detect import detect_layout


def _year_row(headers: list[str], *, formulas: bool, gap_before_copy: bool = False) -> list[dict]:
    cells = [_c("Forecast", "A1", "Metric")]
    for index, text in enumerate(headers):
        cells.append(_c("Forecast", f"{chr(66 + index)}1", text))
    start = 2
    for row in range(start, start + 4):
        cells.append(_c("Forecast", f"A{row}", f"Line {row}"))
        for index in range(len(headers)):
            col = chr(66 + index)
            if formulas and not gap_before_copy:
                previous = chr(65 + index)
                cells.append(
                    _c(
                        "Forecast",
                        f"{col}{row}",
                        str(10 + row + index),
                        formula=f"={previous}{row}*1.03",
                    )
                )
            else:
                cells.append(_c("Forecast", f"{col}{row}", str(10 * row + index)))
    if gap_before_copy:
        for row in range(20, 23):
            cells.append(_c("Forecast", f"A{row}", f"Later {row}"))
            for index in range(len(headers)):
                col = chr(66 + index)
                previous = chr(65 + index)
                formula = "=Assumptions!B1" if index == 0 else f"={previous}{row}*1.03"
                cells.append(_c("Forecast", f"{col}{row}", "1", formula=formula))
    return cells


def _keys(cells: list[dict]) -> list[str]:
    layout = detect_layout(cells)
    axes = [axis for axis in layout.sheets[0].axes if axis.grain == "model_year"]
    if not axes:
        return []
    return [period.period_key for period in axes[0].periods]


def test_labeled_relative_minimum_is_three_columns() -> None:
    assert _LABELED_RELATIVE_MIN == 3
    headers = [f"Year {number}" for number in range(1, _LABELED_RELATIVE_MIN + 1)]
    assert _keys(_year_row(headers, formulas=True)) == ["Y1", "Y2", "Y3"]
    assert _keys(_year_row(headers[:2], formulas=True)) == []


def test_first_year_formula_can_differ() -> None:
    headers = [f"Year {number}" for number in range(1, 6)]
    cells = [_c("Forecast", "A6", "Metric")]
    for index, text in enumerate(headers):
        cells.append(_c("Forecast", f"{chr(66 + index)}6", text))
    cells.append(_c("Forecast", "A7", "Base rent ($/sf)"))
    cells.append(_c("Forecast", "B7", "24", formula="=Assumptions!D11"))
    for index in range(1, 5):
        previous = chr(65 + index)
        cells.append(
            _c(
                "Forecast",
                f"{chr(66 + index)}7",
                "25",
                formula=f"={previous}7*(1+Assumptions!$D$15)",
            )
        )
    layout = detect_layout(cells)
    axis = layout.sheets[0].axes[0]
    assert axis.grain == "model_year"
    assert [period.period_key for period in axis.periods] == ["Y1", "Y2", "Y3", "Y4", "Y5"]
    assert [period.text for period in axis.periods] == headers
    assert [period.col for period in axis.periods] == [2, 3, 4, 5, 6]
    rent = next(row for block in layout.sheets[0].blocks for row in block.rows if row.row == 7)
    assert rent.label == "Base rent ($/sf)"
    assert rent.kind == "fact"


def test_constants_and_a_distant_formula_copy_do_not_form_an_axis() -> None:
    headers = [f"Year {number}" for number in range(1, 6)]
    assert _keys(_year_row(headers, formulas=False)) == []
    assert _keys(_year_row(headers, formulas=False, gap_before_copy=True)) == []


def test_a_skip_or_a_repeat_keeps_every_column_and_names_the_header() -> None:
    for headers, span in (
        (["Year 1", "Year 2", "Year 4"], "Forecast!B1:D1"),
        (["Year 1", "Year 2", "Year 2"], "Forecast!B1:D1"),
        (["Год 1", "Год 2", "Год 3"], "Forecast!B1:D1"),
    ):
        cells = _year_row(headers, formulas=False)
        doc = published_context(
            job_id="years",
            workbook_meta={"sheets": [{"name": "Forecast"}]},
            cells=cells,
            layout=detect_layout(cells),
        )
        assert doc.axes == [] or all(axis.grain != "model_year" for axis in doc.axes)
        warning = next(item for item in doc.warnings if "No time axis" in item)
        assert span in warning
        assert all(f"«{text}»" in warning for text in headers)
        block = doc.blocks[0]
        assert block.kind != "timeline"
        values = block.rows[0].series[0].values
        assert len(values) == len(headers)
        assert all(value is not None for value in values)


def test_model_year_axis_leaves_the_neighbor_table_alone() -> None:
    cells = [
        _c("Model", "B6", "Metric"),
        _c("Model", "C6", "Year 1"),
        _c("Model", "D6", "Year 2"),
        _c("Model", "E6", "Year 3"),
        _c("Model", "G6", "Inputs"),
        _c("Model", "H6", "Amount"),
    ]
    for row, label, left, right in (
        (7, "Revenue", "10", "0.2"),
        (8, "Expense", "11", "0.3"),
        (9, "Tax", "12", "0.4"),
    ):
        cells.extend(
            [
                _c("Model", f"B{row}", label),
                _c("Model", f"C{row}", left, formula=f"=B{row}*1.1"),
                _c("Model", f"D{row}", left, formula=f"=C{row}*1.1"),
                _c("Model", f"E{row}", left, formula=f"=D{row}*1.1"),
                _c("Model", f"G{row}", f"{label} input"),
                _c("Model", f"H{row}", right),
            ]
        )
    doc = published_context(
        job_id="neighbor",
        workbook_meta={"sheets": [{"name": "Model"}]},
        cells=cells,
        layout=detect_layout(cells),
    )
    timeline = next(block for block in doc.blocks if block.kind == "timeline")
    revenue = next(row for row in timeline.rows if row.label == "Revenue")
    assert [period.period_key for period in doc.axes[0].periods] == ["Y1", "Y2", "Y3"]
    assert len(revenue.values) == 3
    local = next(
        block
        for block in doc.blocks
        if any(row.label == "Revenue input" for row in block.rows)
    )
    assert local.kind != "timeline"
    assert local.block_id != timeline.block_id
    entered = next(row for row in local.rows if row.label == "Revenue input")
    assert entered.values == ["0.2"]
