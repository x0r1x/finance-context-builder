"""File-path layout cells are slotted records, and build stays under a dict list."""

from __future__ import annotations

import gc
import sys
import tracemalloc
from pathlib import Path

from finance_context.context.build import (
    _BuildCell,
    _layout_rows,
    _layout_value_cells,
    build_context,
)
from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.store.fs import read_parquet_dicts, write_parquet

_COLUMNS = (
    ("sheet", "VARCHAR"),
    ("row", "INTEGER"),
    ("col", "INTEGER"),
    ("addr", "VARCHAR"),
    ("formula_raw", "VARCHAR"),
    ("formula_template", "VARCHAR"),
    ("cached_value", "VARCHAR"),
    ("hidden", "BOOLEAN"),
    ("number_format", "VARCHAR"),
    ("unparsed", "BOOLEAN"),
)
_BUILD_COLUMNS = [name for name, _kind in _COLUMNS]


def _layout(rows: int = 2) -> Layout:
    return Layout(
        sheets=[
            SheetLayout(
                name="S",
                blocks=[
                    Block(
                        block_id="S!r1",
                        label_col=1,
                        axis=Axis(
                            id="S!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="2024", kind="period", period_key="2024")
                            ],
                        ),
                        rows=[
                            LayoutRow(row=row, label=f"L{row}", kind="fact")
                            for row in range(2, rows + 2)
                        ],
                    )
                ],
            )
        ]
    )


def _rows(count: int) -> list[tuple]:
    rows: list[tuple] = [
        ("S", 1, 1, "A1", None, None, "Item", False, None, False),
        ("S", 1, 2, "B1", None, None, "2024", False, None, False),
    ]
    for row in range(2, count + 2):
        rows.append(("S", row, 1, f"A{row}", None, None, f"L{row}", False, None, False))
        rows.append(
            ("S", row, 2, f"B{row}", "=1+1", "=1+1", "2", False, "0", False)
        )
    return rows


def _write(path: Path, count: int) -> None:
    write_parquet(path, _COLUMNS, _rows(count), row_group_rows=0)


def test_build_cell_is_smaller_than_the_cell_dict() -> None:
    raw = {
        "sheet": "S",
        "row": 300,
        "col": 2,
        "addr": "B300",
        "formula_raw": "=1+1",
        "formula_template": "=1+1",
        "cached_value": "2",
        "hidden": False,
        "number_format": "0.00",
        "unparsed": False,
    }
    assert sys.getsizeof(_BuildCell(**raw)) < sys.getsizeof(raw)
    assert _BuildCell(**raw).get("missing", "nope") == "nope"


def test_layout_value_cells_match_dicts(tmp_path: Path) -> None:
    path = tmp_path / "cells.parquet"
    _write(path, 4)
    layout = _layout(4)
    records = _layout_value_cells(path, layout)
    assert records
    assert all(isinstance(cell, _BuildCell) for cell in records)
    dicts = []
    for sheet, rows in _layout_rows(layout).items():
        dicts.extend(
            read_parquet_dicts(
                path,
                _BUILD_COLUMNS,
                where="sheet = ? AND row IN (SELECT unnest(?::BIGINT[]))",
                params=[sheet, [int(row) for row in rows]],
            )
        )

    def identity(cell):
        return tuple(cell.get(name) for name in _BUILD_COLUMNS)

    assert [identity(cell) for cell in records] == [identity(cell) for cell in dicts]
    meta = {"sheets": [{"name": "S"}]}
    from_records = build_context(
        job_id="same",
        workbook_meta=meta,
        cells=records,
        layout=layout,
    )
    from_dicts = build_context(
        job_id="same",
        workbook_meta=meta,
        cells=dicts,
        layout=layout,
    )
    assert from_records.model_dump(mode="json") == from_dicts.model_dump(mode="json")


def test_layout_value_cells_peak_below_dicts(tmp_path: Path) -> None:
    """Slotted build cells stay under the dict list this read used to allocate.

    tracemalloc, same process, same file, same query. The slotted read traced
    about 0.8 of the dict read here; a dict per cell does not.
    """
    count = 6_000
    path = tmp_path / "cells.parquet"
    _write(path, count)
    layout = _layout(count)
    where = "sheet = ? AND row IN (SELECT unnest(?::BIGINT[]))"
    gc.collect()
    tracemalloc.start()
    try:
        records = _layout_value_cells(path, layout)
        assert len(records) == count * 2 + 2
        _current, slot_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del records
    gc.collect()
    tracemalloc.start()
    try:
        dicts: list = []
        for sheet, rows in _layout_rows(layout).items():
            dicts.extend(
                read_parquet_dicts(
                    path,
                    _BUILD_COLUMNS,
                    where=where,
                    params=[sheet, [int(row) for row in rows]],
                )
            )
        assert len(dicts) == count * 2 + 2
        _current, dict_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert slot_peak * 10 < dict_peak * 9
