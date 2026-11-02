"""Per-source cell-edge dedup. One path, DuckDB parquet write."""

from __future__ import annotations

import sys
from itertools import islice
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from finance_context.formulas.csr import (
    SheetUsage,
    expand_cell_edges_column_batches,
    expanded_edge_count,
)
from finance_context.formulas.engine import FormulaEngine
from finance_context.formulas.models import Edge
from finance_context.formulas.stage import _KeptEdgeWriter, _write_cell_edge_batches
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


def test_packed_keys_match_source_target_kind(tmp_path: Path) -> None:
    """First ``(source, target, kind)`` wins. Kind and target text both count.

    ``S!$A$1`` is not the canonical id ``S!A1``, so both rows stay.
    """
    usage = SheetUsage(rows_by_col={"S": {}}, canonical={"s": "S"})
    edges = [
        Edge(kind="ref", source="S!B1", target="S!A1"),
        Edge(kind="ref", source="S!B1", target="S!A1"),
        Edge(kind="ref", source="S!B1", target="S!$A$1"),
        Edge(kind="cross_sheet", source="S!B1", target="S!A1"),
        Edge(kind="range", source="S!B1", target="S!A1"),
        Edge(kind="ref", source="S!B1", target="Named"),
        Edge(kind="ref", source="S!B1", target="Named"),
        Edge(kind="range", source="S!B1", target="S!C1:C80"),
    ]
    expected: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    dest = tmp_path / "kinds"
    dest.mkdir()
    path = dest / "ir" / "cell_edges.parquet"
    sink = _KeptEdgeWriter(path, 8)

    def consume(columns: dict[str, list]) -> None:
        for index in range(len(columns["source"])):
            key = (columns["source"][index], columns["target"][index], columns["kind"][index])
            if key in seen:
                continue
            seen.add(key)
            expected.append(key)
        sink.consume(columns)

    expand_cell_edges_column_batches(
        edges,
        {"S!A1", "S!C1"},
        known_sheets={"S"},
        presence={},
        usage=usage,
        pool=None,
        consume=consume,
        batch_size=8,
    )
    assert all(isinstance(key, int) for key in sink._keys)
    sink.finish()
    kept = [(row["source"], row["target"], row["kind"]) for row in read_parquet(path)]
    assert kept == expected
    assert ("S!B1", "S!A1", "ref") in kept
    assert ("S!B1", "S!$A$1", "ref") in kept
    assert ("S!B1", "S!A1", "cross_sheet") in kept
    assert ("S!B1", "S!A1", "range") in kept
    assert ("S!B1", "Named", "ref") in kept
    assert sum(1 for row in kept if row == ("S!B1", "S!A1", "ref")) == 1


def test_huge_rectangle_is_one_range_node() -> None:
    usage = SheetUsage(rows_by_col={"S": {}}, canonical={"s": "S"})
    edge = Edge(kind="range", source="S!B1", target="S!A1:Z40000")
    assert expanded_edge_count([edge], usage) == 1


def test_million_edges_of_one_formula_bound_the_key_set(tmp_path: Path) -> None:
    """One formula of 15,625 ranges of 64 cells: 1,000,000 expanded edges.

    A rectangle bigger than ``RANGE_NODE_MIN_CELLS`` (64) is one range node, and
    ``RANGE_EXPAND_CAP`` (2000) cuts a range that still expands. 64 is the
    largest block main still expands on a sheet it knows. The next formula
    replaces the set.
    """
    usage = SheetUsage(rows_by_col={"S": {}}, canonical={"s": "S"})
    span = 64
    count = 1_000_000 // span
    edges = [
        Edge(kind="range", source="S!B1", target=f"S!A{start}:A{start + span - 1}")
        for start in range(1, count * span, span)
    ]
    assert expanded_edge_count(edges, usage) == 1_000_000
    dest = tmp_path / "million"
    dest.mkdir()
    sink = _KeptEdgeWriter(dest / "ir" / "cell_edges.parquet", 65536)
    expand_cell_edges_column_batches(
        edges,
        set(),
        known_sheets={"S"},
        presence={},
        usage=usage,
        pool={},
        consume=sink.consume,
        batch_size=65536,
    )
    assert len(sink._keys) == 1_000_000
    assert sink._other_ids == {}
    assert all(isinstance(key, int) and key > 0 for key in islice(sink._keys, 8))
    sample = next(iter(sink._keys))
    set_bytes = sys.getsizeof(sink._keys) + len(sink._keys) * sys.getsizeof(sample)
    assert set_bytes < 80 * 1024 * 1024
    expand_cell_edges_column_batches(
        [Edge(kind="ref", source="S!B2", target="S!A1")],
        set(),
        known_sheets={"S"},
        presence={},
        usage=usage,
        pool={},
        consume=sink.consume,
        batch_size=65536,
    )
    assert len(sink._keys) == 1
    assert sink._key_high == 1_000_000
    sink.abort()
