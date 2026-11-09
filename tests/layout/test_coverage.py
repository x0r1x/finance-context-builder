"""Rows outside a period axis become a list or a matrix, not another timeline."""

from __future__ import annotations

from finance_context.excel.a1 import parse_addr
from finance_context.layout.detect import detect_layout


def _c(sheet: str, addr: str, value: str | None, *, hidden: bool = False) -> dict:
    col, row = parse_addr(addr)
    return {
        "sheet": sheet,
        "row": row,
        "col": col,
        "addr": addr,
        "cached_value": value,
        "hidden": hidden,
        "formula_raw": None,
        "number_format": None,
    }


def test_narrow_list_without_an_axis_is_key_value() -> None:
    cells = [
        _c("List", "A1", "Name"),
        _c("List", "B1", "Amount"),
        _c("List", "A2", "Rent"),
        _c("List", "B2", "10"),
        _c("List", "A3", "Tax"),
        _c("List", "B3", "2"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert [block.kind for block in sheet.blocks] == ["key_value"]
    block = sheet.blocks[0]
    assert [row.label for row in block.rows] == ["Rent", "Tax"]
    assert [header.period_key for header in block.axis.headers] == ["c2"]
    assert block.axis.headers[0].text == "Amount"
    assert any("key_value" in note for note in sheet.notes)


def test_one_labelled_number_is_not_a_table() -> None:
    cells = [_c("Meta", "A1", "As of"), _c("Meta", "B1", "5")]
    sheet = detect_layout(cells).sheets[0]
    assert sheet.blocks == []
    assert sheet.notes == []


def test_wide_grid_without_an_axis_is_a_matrix() -> None:
    # Two data rows stay under the params minimum, so this is the fallback.
    cells = [
        _c("Grid", "A1", "Case"),
        _c("Grid", "B1", "One"),
        _c("Grid", "C1", "Two"),
        _c("Grid", "D1", "Three"),
        _c("Grid", "E1", "Four"),
        _c("Grid", "A2", "Base"),
        _c("Grid", "B2", "10"),
        _c("Grid", "C2", "11"),
        _c("Grid", "D2", "12"),
        _c("Grid", "E2", "13"),
        _c("Grid", "A3", "High"),
        _c("Grid", "B3", "20"),
        _c("Grid", "C3", "21"),
        _c("Grid", "D3", "22"),
        _c("Grid", "E3", "23"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert [block.kind for block in sheet.blocks] == ["matrix"]
    assert [row.label for row in sheet.blocks[0].rows] == ["Base", "High"]
    assert [header.text for header in sheet.blocks[0].axis.headers] == [
        "One",
        "Two",
        "Three",
        "Four",
    ]
    assert any("matrix" in note for note in sheet.notes)


def test_rows_above_a_timeline_keep_their_own_block() -> None:
    cells = [
        _c("Model", "A1", "Case"),
        _c("Model", "B1", "10"),
        _c("Model", "C1", "20"),
        _c("Model", "D1", "30"),
        _c("Model", "E1", "40"),
        _c("Model", "A5", "Item"),
        _c("Model", "B5", "2020"),
        _c("Model", "C5", "2021"),
        _c("Model", "D5", "2022"),
        _c("Model", "A6", "Revenue"),
        _c("Model", "B6", "1"),
        _c("Model", "C6", "2"),
        _c("Model", "D6", "3"),
    ]
    sheet = detect_layout(cells).sheets[0]
    kinds = [block.kind for block in sheet.blocks]
    assert "timeline" in kinds
    assert "matrix" in kinds
    matrix = next(block for block in sheet.blocks if block.kind == "matrix")
    timeline = next(block for block in sheet.blocks if block.kind == "timeline")
    assert [row.label for row in matrix.rows] == ["Case"]
    assert [row.label for row in timeline.rows] == ["Revenue"]
    assert [header.period_key for header in timeline.axis.headers] == ["2020", "2021", "2022"]


def test_a_timeline_does_not_gain_a_coverage_block() -> None:
    cells = [
        _c("P&L", "A1", "Item"),
        _c("P&L", "B1", "2023"),
        _c("P&L", "C1", "2024"),
        _c("P&L", "A2", "Revenue"),
        _c("P&L", "B2", "100"),
        _c("P&L", "C2", "110"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert [block.kind for block in sheet.blocks] == ["timeline"]
    assert sheet.notes == []


def test_hidden_row_is_included() -> None:
    cells = [
        _c("List", "A1", "Rent"),
        _c("List", "B1", "10"),
        _c("List", "A2", "Tax", hidden=True),
        _c("List", "B2", "2", hidden=True),
    ]
    block = detect_layout(cells).sheets[0].blocks[0]
    assert block.kind == "key_value"
    assert [row.label for row in block.rows] == ["Rent", "Tax"]


def test_chart_sheet_is_marked_and_still_read() -> None:
    cells = [
        _c("Charts", "A1", "Rent"),
        _c("Charts", "B1", "10"),
        _c("Charts", "A2", "Tax"),
        _c("Charts", "B2", "2"),
    ]
    sheet = detect_layout(cells).sheets[0]
    assert sheet.blocks[0].kind == "key_value"
    assert any(note == "Sheet Charts is a chart copy, not a calculation" for note in sheet.notes)


def test_payment_schedule_is_not_a_chart_sheet() -> None:
    cells = [
        _c("График платежей", "A1", "Rent"),
        _c("График платежей", "B1", "10"),
        _c("График платежей", "A2", "Tax"),
        _c("График платежей", "B2", "2"),
    ]
    notes = detect_layout(cells).sheets[0].notes
    assert not any("chart copy" in note for note in notes)
