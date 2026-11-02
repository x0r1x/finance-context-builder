"""Inline cell-edge dedup matches the DuckDB window, including its row groups."""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq
import pytest

from finance_context.formulas.engine import FormulaEngine
from finance_context.formulas.models import Edge
from finance_context.formulas.stage import (
    _edge_sources_contiguous,
    _keep_first_cell_edges,
    _write_cell_edge_batches,
    _write_cell_edges_via_window,
)
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


def _write(dest: Path, edges: list[Edge], row_group_rows: int, writer) -> Path:
    dest.mkdir()
    writer(
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
def test_inline_cell_edges_match_window_bytes(tmp_path: Path, row_group_rows: int) -> None:
    edges = _edges()
    assert _edge_sources_contiguous(edges)
    inline = _write(tmp_path / "inline", edges, row_group_rows, _write_cell_edge_batches)
    window = _write(tmp_path / "window", edges, row_group_rows, _write_cell_edges_via_window)
    assert inline.read_bytes() == window.read_bytes()
    assert not (tmp_path / "inline" / "ir" / "cell_edges.parquet.dup").exists()
    groups = pq.ParquetFile(inline).num_row_groups
    if row_group_rows <= 0 or row_group_rows >= 100:
        assert groups == 1
    else:
        assert groups > 1
    rows = read_parquet(inline)
    repeated = [row for row in rows if row["source"] == "S!B1"]
    assert len(repeated) == 1
    assert repeated[0]["target"] == "S!A1"
    covered = [row["target"] for row in rows if row["source"] == "S!C1"]
    assert covered == [f"S!A{row}" for row in range(1, 8)]
    assert len(rows) < len(edges)


def test_inline_path_does_not_use_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one-group write does not open the dedup connection.

    A multi-group file still reads its parts through ``limited_connection`` inside
    ``RowGroupWriter``. That is the parquet stitch, not the ``row_number`` window.
    """
    calls: list[int] = []

    def wrapped(*args, **kwargs):
        calls.append(1)
        return _keep_first_cell_edges(*args, **kwargs)

    monkeypatch.setattr("finance_context.formulas.stage._keep_first_cell_edges", wrapped)

    def boom(*_args, **_kwargs):
        raise AssertionError("limited_connection")

    monkeypatch.setattr("finance_context.store.duckdb_io.limited_connection", boom)
    _write(tmp_path / "one", _edges(), 0, _write_cell_edge_batches)
    assert calls == []


def test_repeated_source_falls_back_to_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edges = [
        Edge(kind="ref", source="S!B1", target="S!A1"),
        Edge(kind="ref", source="S!B1", target="S!A1"),
        Edge(kind="ref", source="S!B2", target="S!A2"),
        Edge(kind="ref", source="S!B1", target="S!A3"),
        Edge(kind="ref", source="S!B1", target="S!A1"),
    ]
    assert _edge_sources_contiguous(edges) is False
    monkeypatch.setattr(
        "finance_context.formulas.stage._edge_sources_contiguous", lambda _edges: True
    )
    calls: list[int] = []
    real = _write_cell_edges_via_window

    def wrapped(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr("finance_context.formulas.stage._write_cell_edges_via_window", wrapped)
    path = _write(tmp_path / "fallback", edges, 2, _write_cell_edge_batches)
    direct = _write(tmp_path / "window", edges, 2, real)
    assert calls == [1]
    assert path.read_bytes() == direct.read_bytes()
    assert not (tmp_path / "fallback" / "ir" / "cell_edges.parquet.dup").exists()
    kept = [(row["source"], row["target"], row["kind"]) for row in read_parquet(path)]
    assert kept == [
        ("S!B1", "S!A1", "ref"),
        ("S!B2", "S!A2", "ref"),
        ("S!B1", "S!A3", "ref"),
    ]


def test_empty_cell_edges_match_and_leave_no_dup(tmp_path: Path) -> None:
    inline = _write(tmp_path / "inline", [], 8, _write_cell_edge_batches)
    window = _write(tmp_path / "window", [], 8, _write_cell_edges_via_window)
    assert inline.read_bytes() == window.read_bytes()
    assert read_parquet(inline) == []
    assert not (tmp_path / "inline" / "ir" / "cell_edges.parquet.dup").exists()
