from __future__ import annotations

from finance_context.formulas.csr import expand_cell_edges
from finance_context.formulas.models import Edge
from finance_context.settings import DEFAULT_RANGE_EXPAND_CAP as RANGE_EXPAND_CAP


def test_range_expansion_caps_at_2000_and_sets_truncated() -> None:
    edge = Edge(
        kind="range",
        source="Sheet1!A1",
        target="Sheet1!B1:B2500",
        unresolved=False,
    )
    rows = expand_cell_edges([edge], {"Sheet1!A1"})
    assert RANGE_EXPAND_CAP == 2000
    assert len(rows) == 2000
    assert edge.truncated is True
    targets = {row.target for row in rows}
    assert "Sheet1!B2000" in targets
    assert "Sheet1!B2500" not in targets
    assert all(row.truncated is True for row in rows)


def test_named_range_edge_is_not_expanded() -> None:
    edge = Edge(
        kind="range",
        source="Cover!C20",
        target="DS_Drawn_C:DS_Drawn_N",
        unresolved=True,
    )
    rows = expand_cell_edges([edge], {"Cover!C20"})
    assert [row.target for row in rows] == ["DS_Drawn_C:DS_Drawn_N"]
    assert edge.truncated is False


def test_small_range_is_not_truncated() -> None:
    edge = Edge(
        kind="range",
        source="Sheet1!C1",
        target="Sheet1!A1:B2",
        unresolved=False,
    )
    rows = expand_cell_edges([edge], {"Sheet1!C1"})
    assert edge.truncated is False
    assert len(rows) == 4
    assert {row.target for row in rows} == {
        "Sheet1!A1",
        "Sheet1!B1",
        "Sheet1!A2",
        "Sheet1!B2",
    }
