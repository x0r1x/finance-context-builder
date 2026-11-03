from __future__ import annotations

from pathlib import Path

from finance_context.app.pipeline import _read_formula_edges
from finance_context.formulas.models import as_edge
from finance_context.layout.params import active_value_columns, active_value_columns_from_endpoints
from finance_context.store.duckdb_io import _iter_parquet_rows
from finance_context.store.fs import read_parquet, write_parquet

_EDGE_COLUMNS = (
    ("source", "VARCHAR"),
    ("kind", "VARCHAR"),
    ("target", "VARCHAR"),
    ("unresolved", "BOOLEAN"),
    ("truncated", "BOOLEAN"),
    ("named", "BOOLEAN"),
)


def test_cross_sheet_column_needs_two_hits_and_keeps_the_first_tie() -> None:
    edges = [
        {"source": "Other!A1", "target": "Input Assumptions!D8"},
        {"source": "P&L!C9", "target": "Input Assumptions!C9"},
        {"source": "CFS!C10", "target": "Input Assumptions!D10"},
        {"source": "Same!A1", "target": "Same!B2"},
        {"source": "Other!B1", "target": "Cover!E1"},
    ]
    # D is column 4 and was seen before C. Cover has a single hit.
    assert active_value_columns(edges) == {"input assumptions": 4}
    assert active_value_columns([]) == {}


def test_endpoint_scan_matches_the_edge_list(tmp_path: Path) -> None:
    rows = [
        ("Other!A1", "ref", "Sheet!C1", False, False, False),
        ("Other!A2", "ref", "Sheet!B1", False, False, False),
        ("Other!A3", "ref", "Sheet!C2", False, False, False),
        ("Other!A4", "ref", "SHEET!B2", False, False, False),
    ]
    path = tmp_path / "edges.parquet"
    write_parquet(path, _EDGE_COLUMNS, rows, row_group_rows=2)
    edges = read_parquet(path)
    pairs = []
    for names, batch in _iter_parquet_rows(path, ["source", "target"]):
        pos = {name: index for index, name in enumerate(names)}
        for row in batch:
            pairs.append((str(row[pos["source"]] or ""), str(row[pos["target"]] or "")))
    assert active_value_columns_from_endpoints(pairs) == active_value_columns(edges)
    # C is column 3, seen first, tied 2-2 with B.
    assert active_value_columns(edges) == {"sheet": 3}


def test_formula_edge_reload_matches_full_read(tmp_path: Path) -> None:
    rows = [
        ("S!A1", "ref", "T!B2", False, True, False),
        ("S!A2", "range", None, True, False, True),
    ]
    book = tmp_path / "book"
    path = book / "ir" / "edges.parquet"
    write_parquet(path, _EDGE_COLUMNS, rows, row_group_rows=0)
    reloaded = _read_formula_edges(book)
    direct = [as_edge(edge) for edge in read_parquet(path)]

    def fields(edge):
        return (edge.source, edge.kind, edge.target, edge.unresolved, edge.truncated, edge.named)

    assert [fields(edge) for edge in reloaded] == [fields(edge) for edge in direct]
