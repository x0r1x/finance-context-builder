from __future__ import annotations

from finance_context.formulas.csr import RANGE_EXPAND_CAP, build_csr
from finance_context.formulas.models import Edge


def test_range_expansion_caps_at_2000_and_sets_truncated() -> None:
    edge = Edge(
        kind="range",
        source="Sheet1!A1",
        target="Sheet1!B1:B2500",
        unresolved=False,
    )
    graph = build_csr(edges=[edge], extra_nodes=["Sheet1!A1"])
    assert RANGE_EXPAND_CAP == 2000
    start = graph.node_index["Sheet1!A1"]
    degree = int(graph.matrix.getrow(start).nnz)
    assert degree == 2000
    assert "Sheet1!A1" in graph.truncated_sources
    assert "Sheet1!B2000" in graph.node_index
    assert "Sheet1!B2500" not in graph.node_index


def test_small_range_is_not_truncated() -> None:
    edge = Edge(
        kind="range",
        source="Sheet1!C1",
        target="Sheet1!A1:B2",
        unresolved=False,
    )
    graph = build_csr(edges=[edge], extra_nodes=["Sheet1!C1"])
    assert "Sheet1!C1" not in graph.truncated_sources
    start = graph.node_index["Sheet1!C1"]
    assert int(graph.matrix.getrow(start).nnz) == 4
