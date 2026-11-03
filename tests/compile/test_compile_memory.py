"""Compile memory stays linear in cells and below a per-id string set."""

from __future__ import annotations

import gc
import tracemalloc
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel.stage import parse_workbook
from finance_context.formulas.known import KnownNodes
from finance_context.formulas.stage import canonical_node_id, compile_workbook
from finance_context.settings import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", _env_file=None)


def _book(path: Path, values: int, formulas: int) -> None:
    cells = [CellSpec(addr=f"A{row}", value="1") for row in range(1, values + 1)]
    cells.extend(
        CellSpec(addr=f"B{row}", value="2", formula=f"=A{row}+A{row}")
        for row in range(1, formulas + 1)
    )
    build_xlsx(path, sheets=[SheetSpec(name="Data", cells=cells)])


def _compile_peak(tmp_path: Path, values: int, formulas: int) -> int:
    dest = tmp_path / f"v{values}-f{formulas}"
    dest.mkdir()
    _book(dest / "book.xlsx", values, formulas)
    settings = _settings(tmp_path)
    parse_workbook(dest / "book.xlsx", dest, settings=settings)
    gc.collect()
    tracemalloc.start()
    try:
        compile_workbook(dest, settings=settings)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak


def test_known_nodes_peak_is_below_the_id_set() -> None:
    count = 20_000
    gc.collect()
    tracemalloc.start()
    try:
        index = KnownNodes()
        for row in range(1, count + 1):
            index.add("Sheet", f"A{row}")
        assert len(index) == count
        assert f"Sheet!A{count}" in index
        _current, index_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    del index
    gc.collect()
    tracemalloc.start()
    try:
        old = {canonical_node_id("Sheet", f"A{row}") for row in range(1, count + 1)}
        assert len(old) == count
        _current, set_peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert index_peak < set_peak


def test_compile_tracemalloc_scales_with_cells_and_formulas(tmp_path: Path) -> None:
    small = _compile_peak(tmp_path, 1_500, 400)
    large = _compile_peak(tmp_path, 6_000, 1_600)
    # 4× cells and 4× formulas. Main (d12fba5) traced 4_411_834 bytes on the
    # larger book; this compile has to stay at or under that peak.
    assert large < small * 5
    assert large <= 4_411_834
