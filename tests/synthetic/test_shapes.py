"""The generator writes the benchmark shapes, and the same bytes twice."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from tests.fixtures.synthetic import dense, hops, hops_tl, outline, rel, series, sumif, text

from finance_context.excel.stage import parse_workbook
from finance_context.formulas.stage import compile_workbook
from finance_context.layout.cells import parse_number
from finance_context.layout.stage import layout_workbook
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet_dicts

_CELL_COLUMNS = (
    "sheet",
    "row",
    "col",
    "addr",
    "formula_raw",
    "cached_value",
    "style_indent",
    "outline_level",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _layout(path: Path, tmp_path: Path):
    dest = tmp_path / path.stem
    dest.mkdir(parents=True)
    settings = Settings(data_dir=tmp_path / "data", _env_file=None)
    parse_workbook(path, dest, settings=settings)
    compiled = compile_workbook(dest, settings=settings)
    layout = layout_workbook(dest, settings=settings)
    cells = read_parquet_dicts(dest / "raw" / "cells.parquet", _CELL_COLUMNS)
    return layout, compiled, cells


def test_each_shape_is_byte_stable(tmp_path: Path) -> None:
    builds = (
        ("rel", lambda path: rel(path, 6, periods=4)),
        ("series", lambda path: series(path, 8, periods=4)),
        ("hops", lambda path: hops(path, 6)),
        ("hops_tl", lambda path: hops_tl(path, 6)),
        ("dense", lambda path: dense(path, 3, columns=4)),
        ("outline", lambda path: outline(path)),
        ("sumif", lambda path: sumif(path, 6)),
        ("text", lambda path: text(path, 4, strlen=24)),
    )
    for name, build in builds:
        first = tmp_path / f"{name}-a.xlsx"
        second = tmp_path / f"{name}-b.xlsx"
        build(first)
        build(second)
        assert _digest(first) == _digest(second), name


def test_rel_is_cached_one_plus_one(tmp_path: Path) -> None:
    path = tmp_path / "rel.xlsx"
    rel(path, 4, periods=60)
    _layout_doc, _compiled, cells = _layout(path, tmp_path)
    formulas = [
        cell
        for cell in cells
        if cell["formula_raw"] == "=1+1" and cell["cached_value"] == "2"
    ]
    assert len(formulas) == 4 * 60
    block = _layout_doc.sheets[0].blocks[0]
    assert block.kind == "timeline"
    assert len(block.rows) == 4


def test_hops_is_a_params_block_and_the_timeline_has_two_chains(tmp_path: Path) -> None:
    rows = 20
    params = tmp_path / "hops.xlsx"
    timeline = tmp_path / "hops-tl.xlsx"
    hops(params, rows)
    hops_tl(timeline, rows)

    params_layout, params_compiled, _cells = _layout(params, tmp_path / "params")
    assert [block.kind for sheet in params_layout.sheets for block in sheet.blocks] == ["params"]
    assert len(params_layout.sheets[0].blocks[0].rows) == rows
    # One chain: every row after the seed references the cell above.
    assert len(params_compiled.cell_edges) == rows - 1

    timeline_layout, timeline_compiled, _cells = _layout(timeline, tmp_path / "timeline")
    block = timeline_layout.sheets[0].blocks[0]
    assert block.kind == "timeline"
    assert len(block.rows) == rows
    # Two chains, each seeded by a value. 100,000 rows are 199,998 edges.
    assert len(timeline_compiled.cell_edges) == 2 * (rows - 1)


def test_series_uses_the_relative_formula_grid(tmp_path: Path) -> None:
    path = tmp_path / "series.xlsx"
    series(path, 8, periods=4)
    _layout_doc, _compiled, cells = _layout(path, tmp_path)
    by_addr = {cell["addr"]: cell for cell in cells}
    # Excel row 8 is 8 % 4 == 0 and past the value rows, so it sums the four above.
    assert by_addr["B8"]["formula_raw"] == "=SUM(B4:B7)"
    assert by_addr["B2"]["formula_raw"] is None
    # Row 7 is the fourth formula. It negates the cell three rows up.
    assert by_addr["B7"]["formula_raw"] == "=-B4"
    assert parse_number(by_addr["B7"]["cached_value"]) == -106
    block = _layout_doc.sheets[0].blocks[0]
    assert block.kind == "timeline"
    assert len(block.rows) == 8


def test_series_caches_stay_inside_the_float_range(tmp_path: Path) -> None:
    """The old column-B SUM left the float range at row 4076."""
    path = tmp_path / "series.xlsx"
    series(path, 4_200, periods=4)
    dest = tmp_path / "parsed"
    dest.mkdir()
    parse_workbook(path, dest, settings=Settings(data_dir=tmp_path / "data", _env_file=None))
    cells = read_parquet_dicts(
        dest / "raw" / "cells.parquet",
        columns=("addr", "row", "col", "cached_value", "formula_raw"),
    )
    values = [
        cell["cached_value"] for cell in cells if cell["col"] > 1 and cell["row"] > 1
    ]
    assert values
    assert all(parse_number(value) is not None for value in values)
    by_addr = {cell["addr"]: cell for cell in cells}
    assert by_addr["B4076"]["formula_raw"] == "=SUM(B4072:B4075)"
    assert parse_number(by_addr["B4076"]["cached_value"]) is not None


def test_dense_is_a_value_grid(tmp_path: Path) -> None:
    path = tmp_path / "dense.xlsx"
    dense(path, 3, columns=5)
    layout, _compiled, cells = _layout(path, tmp_path)
    body = [cell for cell in cells if cell["row"] > 1 and cell["col"] > 1]
    assert len(body) == 3 * 5
    assert all(cell["formula_raw"] is None and cell["cached_value"] == "1" for cell in body)
    block = layout.sheets[0].blocks[0]
    assert block.kind == "timeline"
    assert len(block.rows) == 3


def test_outline_has_unit_text_and_outline_levels(tmp_path: Path) -> None:
    path = tmp_path / "outline.xlsx"
    outline(path)
    layout, _compiled, cells = _layout(path, tmp_path)
    by_label = {cell["cached_value"]: cell for cell in cells if cell["col"] == 1}
    assert by_label["k£"]["outline_level"] == 2
    assert by_label["Cash"]["style_indent"] == 2
    assert by_label["RURm"]["outline_level"] == 1
    labels = {row.label: row.parent_row for block in layout.sheets[0].blocks for row in block.rows}
    assert labels["k£"] is None
    assert labels["Cash"] is not None


def test_sumif_copies_the_whole_column_and_the_running_total(tmp_path: Path) -> None:
    path = tmp_path / "sumif.xlsx"
    sumif(path, 5)
    _layout_doc, compiled, cells = _layout(path, tmp_path)
    by_addr = {cell["addr"]: cell["formula_raw"] for cell in cells}
    assert by_addr["C2"] == "=SUMIF(A:A,A2,B:B)"
    assert by_addr["D2"] == "=SUM($B$2:B2)"
    assert by_addr["D6"] == "=SUM($B$2:B6)"
    assert len(compiled.cell_edges) > 0


def test_text_shared_strings_are_unique(tmp_path: Path) -> None:
    rows = 5
    path = tmp_path / "text.xlsx"
    text(path, rows, strlen=32)
    _layout_doc, _compiled, cells = _layout(path, tmp_path)
    labels = [cell["cached_value"] for cell in cells if cell["col"] in {1, 2}]
    assert len(labels) == 2 + 2 * rows
    assert len(set(labels)) == len(labels)
    block = _layout_doc.sheets[0].blocks[0]
    assert block.kind == "timeline"
    assert len(block.rows) == rows


def test_column_past_the_old_letter_table(tmp_path: Path) -> None:
    """Column 302 is ``KP``. A 302-entry letter table raised ``IndexError``."""
    path = tmp_path / "wide.xlsx"
    rel(path, 1, periods=301)
    _layout_doc, _compiled, cells = _layout(path, tmp_path)
    by_addr = {cell["addr"]: cell for cell in cells}
    assert by_addr["KP1"]["cached_value"] == "FY2300"
    assert by_addr["KP2"]["formula_raw"] == "=1+1"
    assert by_addr["KP2"]["cached_value"] == "2"


def test_cli_writes_a_workbook(tmp_path: Path) -> None:
    target = tmp_path / "rel.xlsx"
    script = Path(__file__).resolve().parents[2] / "scripts" / "gen_synthetic.py"
    completed = subprocess.run(
        [sys.executable, str(script), "rel", "3", str(target), "--periods", "4"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert target.is_file()
    assert _digest(target) == _digest(rel(tmp_path / "direct.xlsx", 3, periods=4))
