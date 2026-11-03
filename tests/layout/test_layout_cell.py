from __future__ import annotations

import sys
from pathlib import Path

from tests.helpers.fast_sheet import fast_cells
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel.stage import parse_workbook
from finance_context.formulas.stage import compile_workbook
from finance_context.layout.cells import LayoutCell
from finance_context.layout.detect import detect_layout
from finance_context.layout.stage import _overlay_merges, _read_layout_sheet
from finance_context.store.fs import read_parquet_dicts


def _record(cell: dict) -> LayoutCell:
    return LayoutCell(
        cell.get("sheet"),
        cell.get("row"),
        cell.get("col"),
        cell.get("addr"),
        cell.get("formula_raw"),
        cell.get("formula_template"),
        cell.get("cached_value"),
        cell.get("hidden"),
        cell.get("number_format"),
        cell.get("merge_end_col"),
        cell.get("style_indent"),
        cell.get("outline_level"),
        cell.get("col_outline_level"),
    )


def test_slot_record_is_smaller_than_the_cell_dict() -> None:
    raw = {
        "sheet": "S",
        "row": 300,
        "col": 1,
        "addr": "A300",
        "formula_raw": "=A1",
        "formula_template": "=A1",
        "cached_value": "1",
        "hidden": False,
        "number_format": "0.00",
    }
    assert sys.getsizeof(LayoutCell(**raw)) < sys.getsizeof(raw)


def test_detect_layout_matches_on_records_and_dicts() -> None:
    cells = fast_cells()
    records = [_record(cell) for cell in cells]
    assert detect_layout(records).model_dump(mode="json") == detect_layout(cells).model_dump(
        mode="json"
    )


def test_merge_overlay_keeps_the_record_and_copies_a_dict() -> None:
    record = LayoutCell("S", 2, 1, "A2", cached_value="Title")
    original = {"sheet": "S", "row": 2, "col": 1, "addr": "A2", "cached_value": "Title"}
    meta = {"sheets": [{"name": "S", "merges": ["A2:C2"]}]}
    overlaid = _overlay_merges([record, original], meta)
    assert overlaid[0] is record
    assert record["merge_end_col"] == 3
    assert overlaid[1] is not original
    assert "merge_end_col" not in original
    assert overlaid[1]["merge_end_col"] == 3


def test_sheet_read_returns_records_with_dict_values(tmp_path: Path) -> None:
    source = tmp_path / "model.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2024", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="10", formula="=1+9"),
                ],
            )
        ],
        shared_strings=["Item", "2024", "Revenue"],
    )
    dest = tmp_path / "book"
    parse_workbook(source, dest)
    compile_workbook(dest)
    records = _read_layout_sheet(dest, "P&L", None)
    assert records
    assert all(isinstance(cell, LayoutCell) for cell in records)
    dicts = read_parquet_dicts(
        dest / "ir" / "cells.parquet",
        ["sheet", "row", "col", "addr", "formula_raw", "cached_value"],
        where="sheet = ?",
        params=["P&L"],
    )

    def identity(cell):
        return (
            cell["sheet"],
            cell["row"],
            cell["col"],
            cell["addr"],
            cell.get("formula_raw"),
            cell.get("cached_value"),
        )

    assert [identity(cell) for cell in records] == [identity(cell) for cell in dicts]
