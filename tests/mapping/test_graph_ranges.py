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


def test_named_edge_does_not_feed_mapping_adjacency() -> None:
    named = {
        "source": "PF Model!AA367",
        "target": "PF Model!H32",
        "kind": "ref",
        "unresolved": False,
        "named": True,
    }
    direct = {
        "source": "PF Model!AA367",
        "target": "PF Model!H32",
        "kind": "ref",
        "unresolved": False,
        "named": False,
    }
    _precedents, named_deps = row_adjacency([named], limit=0)
    assert named_deps == {}
    precedents, direct_deps = row_adjacency([direct], limit=0)
    assert "PF Model!367" in direct_deps[("PF Model", 32)]
    assert precedents[("PF Model", 367)] == ["PF Model!32"]


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
