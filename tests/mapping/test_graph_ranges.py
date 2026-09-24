from __future__ import annotations

from finance_context.mapping.graph import row_adjacency


def test_cell_edge_is_not_expanded_again() -> None:
    edges = [
        {
            "source": "Sheet1!C1",
            "target": "Sheet1!A2",
            "kind": "range",
            "unresolved": False,
            "range_ref": "Sheet1!A:A",
        }
    ]
    precedents, _dependents = row_adjacency(edges, limit=0)
    assert precedents[("Sheet1", 1)] == ["Sheet1!2"]


def test_row_adjacency_expands_sum_range() -> None:
    edges = [
        {
            "source": "P&L!J13",
            "target": "P&L!J9:J12",
            "kind": "range",
            "unresolved": False,
        }
    ]
    precedents, dependents = row_adjacency(edges, limit=0)
    assert set(precedents[("P&L", 13)]) == {"P&L!9", "P&L!10", "P&L!11", "P&L!12"}
    assert "P&L!13" in dependents[("P&L", 9)]
    assert "P&L!13" in dependents[("P&L", 12)]
