from __future__ import annotations

from finance_context.mapping.graph import row_adjacency


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
