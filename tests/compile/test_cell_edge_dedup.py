"""Per-source cell-edge dedup. One path, DuckDB parquet write."""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from finance_context.formulas.engine import FormulaEngine
from finance_context.formulas.models import Edge
from finance_context.formulas.stage import _write_cell_edge_batches
from finance_context.store.fs import read_parquet


def _edges() -> list[Edge]:
    engine = FormulaEngine(locale_hint="en")
    edges: list[Edge] = []
    for row in range(1, 30):
        parsed = engine.parse("=A1+A1", sheet="S", addr=f"B{row}")
        edges.extend(parsed.edges)
    overlapped = engine.parse("=SUM(A1:A5)+SUM(A3:A7)", sheet="S", addr="C1")
    edges.extend(overlapped.edges)
    return edges


def _known() -> set[str]:
    nodes = {f"S!A{row}" for row in range(1, 8)}
    nodes.update(f"S!B{row}" for row in range(1, 30))
    nodes.add("S!C1")
    return nodes


def _write(dest: Path, edges: list[Edge], row_group_rows: int) -> Path:
    dest.mkdir()
    _write_cell_edge_batches(
        dest,
        edges,
        _known(),
        sheets={"S"},
        presence={},
        usage=None,
        pool=None,
        row_group_rows=row_group_rows,
    )
    return dest / "ir" / "cell_edges.parquet"


@pytest.mark.parametrize("row_group_rows", [0, 1, 4, 10, 100, 65536])
def test_duplicate_refs_collapse_inside_one_source(tmp_path: Path, row_group_rows: int) -> None:
    path = _write(tmp_path / "edges", _edges(), row_group_rows)
    assert not (tmp_path / "edges" / "ir" / "cell_edges.parquet.dup").exists()
    groups = pq.ParquetFile(path).num_row_groups
    if row_group_rows <= 0 or row_group_rows >= 100:
        assert groups == 1
    else:
        assert groups > 1
    rows = read_parquet(path)
    repeated = [row for row in rows if row["source"] == "S!B1"]
    assert len(repeated) == 1
    assert repeated[0]["target"] == "S!A1"
    covered = [row["target"] for row in rows if row["source"] == "S!C1"]
    assert covered == [f"S!A{row}" for row in range(1, 8)]
    assert len(rows) < len(_edges())


def test_one_group_write_does_not_open_the_scan_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single row group is one DuckDB ``COPY`` via ``write_parquet``.

    Several groups still stitch through ``limited_connection`` inside
    ``RowGroupWriter``. That connection is the parquet stitch.
    """

    def boom(*_args, **_kwargs):
        raise AssertionError("limited_connection")

    monkeypatch.setattr("finance_context.store.duckdb_io.limited_connection", boom)
    _write(tmp_path / "one", _edges(), 0)


def test_repeated_formula_address_keeps_a_key_main_would_drop(tmp_path: Path) -> None:
    """The same formula address after another formula is a new run.

    Main's window kept one ``(S!B1, S!A1, ref)``. This writer keeps the copy
    that comes back with ``S!B1`` after ``S!B2``.
    """
    edges = [
        Edge(kind="ref", source="S!B1", target="S!A1"),
        Edge(kind="ref", source="S!B1", target="S!A1"),
        Edge(kind="ref", source="S!B2", target="S!A2"),
        Edge(kind="ref", source="S!B1", target="S!A3"),
        Edge(kind="ref", source="S!B1", target="S!A1"),
    ]
    path = _write(tmp_path / "repeat", edges, 2)
    assert not (tmp_path / "repeat" / "ir" / "cell_edges.parquet.dup").exists()
    kept = [(row["source"], row["target"], row["kind"]) for row in read_parquet(path)]
    assert kept == [
        ("S!B1", "S!A1", "ref"),
        ("S!B2", "S!A2", "ref"),
        ("S!B1", "S!A3", "ref"),
        ("S!B1", "S!A1", "ref"),
    ]


def test_empty_cell_edges_leave_no_dup(tmp_path: Path) -> None:
    path = _write(tmp_path / "empty", [], 8)
    assert read_parquet(path) == []
    assert not (tmp_path / "empty" / "ir" / "cell_edges.parquet.dup").exists()
