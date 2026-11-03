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
from finance_context.formulas.stage import (
    IR_CELL_EDGE_COLUMNS,
    _KeptEdgeWriter,
    _write_cell_edge_batches,
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


def test_one_group_write_uses_the_capped_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A single row group is one DuckDB COPY through ``limited_connection``."""
    from contextlib import contextmanager

    from finance_context.store import duckdb_io

    calls = 0
    real = duckdb_io.limited_connection

    @contextmanager
    def _count():
        nonlocal calls
        calls += 1
        with real() as con:
            yield con

    monkeypatch.setattr(duckdb_io, "limited_connection", _count)
    path = _write(tmp_path / "one", _edges(), 0)
    assert calls == 1
    assert path.is_file()


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
    ints = [key for key in sink._keys if isinstance(key, int)]
    pairs = [key for key in sink._keys if isinstance(key, tuple)]
    assert ints and all(key > 0 for key in ints)
    assert pairs and all(len(key) == 2 and key[0] > 0 and key[1] > 0 for key in pairs)
    sink.finish()
    kept = [(row["source"], row["target"], row["kind"]) for row in read_parquet(path)]
    assert kept == expected
    assert ("S!B1", "S!A1", "ref") in kept
    assert ("S!B1", "S!$A$1", "ref") in kept
    assert ("S!B1", "S!A1", "cross_sheet") in kept
    assert ("S!B1", "S!A1", "range") in kept
    assert ("S!B1", "Named", "ref") in kept
    assert sum(1 for row in kept if row == ("S!B1", "S!A1", "ref")) == 1


def test_many_kinds_on_one_source_keep_every_non_packable_edge(tmp_path: Path) -> None:
    """More than 256 kinds cannot alias. A pair key has no bit field to overflow.

    A canonical cell with a kind code of 16 or more uses that same pair, so it
    stays distinct from the packed ``ref`` to the same address.
    """
    kinds = [f"k{index:03d}" for index in range(300)]
    # k000 is the first extra kind (code 6). k010 is the 11th (code 16).
    unique = [("S!B1", "NameA", kind) for kind in kinds]
    unique.extend(("S!B1", "NameB", kind) for kind in kinds)
    unique.append(("S!B1", "S!A1", "ref"))
    unique.append(("S!B1", "S!A1", "k010"))
    rows = [*unique, *unique]
    columns = {name: [] for name, _dtype in IR_CELL_EDGE_COLUMNS}
    for source, target, kind in rows:
        columns["source"].append(source)
        columns["target"].append(target)
        columns["kind"].append(kind)
        for name, dtype in IR_CELL_EDGE_COLUMNS:
            if name in {"source", "target", "kind"}:
                continue
            columns[name].append(False if dtype == "BOOLEAN" else None)
    dest = tmp_path / "kinds"
    dest.mkdir()
    path = dest / "ir" / "cell_edges.parquet"
    sink = _KeptEdgeWriter(path, 0)
    sink.consume(columns)
    assert len(sink._keys) == len(unique)
    packed = [key for key in sink._keys if isinstance(key, int)]
    pairs = [key for key in sink._keys if isinstance(key, tuple)]
    assert len(packed) == 1 and packed[0] > 0
    assert len(pairs) == len(unique) - 1
    assert sink._key("S!A1", "ref") == packed[0]
    wide = sink._key("S!A1", "k010")
    assert isinstance(wide, tuple) and wide in sink._keys
    assert sink._key("NameA", "k000") in sink._keys
    assert sink._key("NameB", "k299") in sink._keys
    sink.finish()
    kept = [(row["source"], row["target"], row["kind"]) for row in read_parquet(path)]
    assert kept == unique


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
