from __future__ import annotations

from finance_context.formulas.csr import expand_cell_edges
from finance_context.formulas.models import Edge


def test_expand_range_members_and_dangling() -> None:
    edges = [
        Edge(kind="range", source="P&L!J13", target="P&L!J9:J12"),
        Edge(kind="ref", source="P&L!J13", target="Missing!A1"),
    ]
    known = {"P&L!J13", "P&L!J9", "P&L!J10", "P&L!J11", "P&L!J12"}
    rows = expand_cell_edges(edges, known)
    targets = {row[1] for row in rows}
    assert {"P&L!J9", "P&L!J10", "P&L!J11", "P&L!J12", "Missing!A1"} <= targets
    dangling = {row[1] for row in rows if row[5]}
    assert "Missing!A1" in dangling
    assert "P&L!J9" not in dangling
