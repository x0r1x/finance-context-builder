"""File-path layout cells are slotted records, and build stays under a dict list."""

from __future__ import annotations

import gc
import sys
import tracemalloc
from pathlib import Path

from tests.helpers.published import published_context as build_context

from finance_context.context.build import (
    _BUILD_FIELDS,
    _BuildCell,
    _formula_stats,
    _layout_rows,
    _layout_value_cells,
)
from finance_context.context.timeline import build_axes
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
_BUILD_COLUMNS = list(_BUILD_FIELDS)


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
        "formula_template": "=1+1",
        "cached_value": "2",
        "hidden": False,
        "number_format": "0.00",
    }
    record = _BuildCell(**raw)
    assert sys.getsizeof(record) < sys.getsizeof(raw)
    assert "addr" not in _BuildCell.__slots__
    assert "formula_raw" not in _BuildCell.__slots__
    assert "unparsed" not in _BuildCell.__slots__
    assert record.get("addr") is None
    assert record.get("missing", "nope") == "nope"


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


def test_layout_value_cells_do_not_read_unused_columns(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "cells.parquet"
    _write(path, 2)
    seen: list[list[str]] = []
    import finance_context.store.duckdb_io as duckdb_io

    real = duckdb_io._iter_parquet_rows

    def spy(file_path, columns, **kwargs):
        seen.append(list(columns))
        return real(file_path, columns, **kwargs)

    monkeypatch.setattr(duckdb_io, "_iter_parquet_rows", spy)
    records = _layout_value_cells(path, _layout(2))
    assert records
    assert seen
    loaded = set(seen[0])
    assert "addr" not in loaded
    assert "formula_raw" not in loaded
    assert "unparsed" not in loaded


def test_file_build_matches_cells_when_addr_stays_on_disk(tmp_path: Path) -> None:
    """Period warnings and formula lines still come from the file, not the slots."""
    rows = [
        ("S", 1, 1, "A1", None, None, "Item", False, None, False),
        ("S", 1, 2, "B1", None, None, "2024", False, None, False),
        ("S", 1, 3, "C1", None, None, "Q5 2025", False, None, False),
        ("S", 2, 1, "A2", None, None, "Section", False, None, False),
        ("S", 2, 2, "B2", "=???", None, None, False, None, True),
        ("S", 3, 1, "A3", None, None, "Revenue", False, None, False),
        ("S", 3, 2, "B3", "=???", None, None, False, None, True),
        ("S", 4, 1, "A4", None, None, "Cost", False, None, False),
        ("S", 4, 2, "B4", "=1+1", "=1+1", None, False, None, False),
        ("S", 5, 1, "A5", None, None, "Other", False, None, False),
        ("S", 5, 2, "B5", "=1+1", "=1+1", "2", False, "0", False),
    ]
    book = tmp_path / "book"
    (book / "ir").mkdir(parents=True)
    path = book / "ir" / "cells.parquet"
    write_parquet(path, _COLUMNS, rows, row_group_rows=0)
    layout = Layout(
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
                            LayoutRow(row=2, label="Section", kind="abstract"),
                            LayoutRow(row=3, label="Revenue", kind="fact"),
                            LayoutRow(row=4, label="Cost", kind="fact"),
                            LayoutRow(row=5, label="Other", kind="fact"),
                        ],
                    )
                ],
            )
        ]
    )
    meta = {"sheets": [{"name": "S"}]}
    dicts = read_parquet_dicts(path, [name for name, _kind in _COLUMNS])
    from_file = build_context(
        job_id="same", workbook_meta=meta, layout=layout, book_dir=book
    )
    from_cells = build_context(
        job_id="same", workbook_meta=meta, layout=layout, cells=dicts
    )
    assert from_file.model_dump(mode="json") == from_cells.model_dump(mode="json")
    assert "Unparsed period header S!C1: «Q5 2025»" in from_file.warnings
    assert "2 unparsed formula(s) (e.g. S!B2, S!B3)" in from_file.warnings
    assert (
        "2 formula cell(s) missing cached values (e.g. S!B3, S!B4)" in from_file.warnings
    )
    assert from_file.workbook.missing_cached_values == 3
    assert from_file.workbook.formula_count == 4


def test_formula_stats_keeps_three_addresses(tmp_path: Path) -> None:
    rows = [
        ("S", index, 1, f"A{index}", "=1+1", None, None, False, None, True)
        for index in range(1, 101)
    ]
    path = tmp_path / "cells.parquet"
    write_parquet(path, _COLUMNS, rows, row_group_rows=0)
    excluded = {f"S!A{index}" for index in range(1, 6)}
    stats = _formula_stats(path, excluded)
    assert stats.formula_count == 100
    assert stats.missing_cached == 100
    assert stats.unparsed == 100
    assert stats.missing_values == 95
    assert stats.missing_addrs == ["S!A6", "S!A7", "S!A8"]
    assert stats.unparsed_addrs == ["S!A1", "S!A2", "S!A3"]


def test_formula_stats_peak_stays_under_the_address_list(tmp_path: Path) -> None:
    """Keeping every missing address traced more than the three-example scan.

    Same file and the same column scan. tracemalloc. The extra list is what
    build used to hold until the warning was formatted.
    """
    from finance_context.context.build import _iter_cell_columns

    count = 20_000
    rows = [
        ("S", index, 1, f"A{index}", "=1+1", None, None, False, None, False)
        for index in range(1, count + 1)
    ]
    path = tmp_path / "cells.parquet"
    write_parquet(path, _COLUMNS, rows, row_group_rows=0)
    gc.collect()
    tracemalloc.start()
    try:
        stats = _formula_stats(path, set())
        assert stats.missing_cached == count
        assert stats.missing_addrs == ["S!A1", "S!A2", "S!A3"]
        _current, stats_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del stats
    gc.collect()
    tracemalloc.start()
    try:
        kept: list[str] = []
        for sheets, addresses, formulas, cached, _flags in _iter_cell_columns(
            path, ["sheet", "addr", "formula_raw", "cached_value", "unparsed"]
        ):
            for index, formula in enumerate(formulas):
                if formula and cached[index] is None:
                    kept.append(f"{sheets[index]}!{addresses[index]}")
        assert len(kept) == count
        _current, list_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert stats_peak < list_peak


def test_build_axes_reuses_the_given_address_map() -> None:
    """A second (sheet, row, col) map was a transient peak inside build_axes."""
    cells = [
        {"sheet": "S", "row": row, "col": 1, "addr": f"A{row}", "cached_value": "1"}
        for row in range(1, 20_001)
    ]
    layout = Layout(sheets=[SheetLayout(name="S", blocks=[])])
    by_addr = {(cell["sheet"], int(cell["row"]), int(cell["col"])): cell for cell in cells}
    gc.collect()
    tracemalloc.start()
    try:
        build_axes(layout, cells, by_addr=by_addr, period_warnings=False)
        _current, with_map = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    gc.collect()
    tracemalloc.start()
    try:
        build_axes(layout, cells, period_warnings=False)
        _current, without = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert with_map * 4 < without
