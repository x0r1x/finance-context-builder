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
    by_target = {row[1]: row for row in rows}
    assert by_target["Missing!A1"][6] == "missing_cell"


def test_index_range_holes_are_empty_range_members() -> None:
    edges = [
        Edge(kind="range", source="Input Assumptions!C8", target="Input Assumptions!J8:O8"),
    ]
    known = {"Input Assumptions!C8", "Input Assumptions!J8"}
    sheets = {"Input Assumptions"}
    rows = expand_cell_edges(edges, known, known_sheets=sheets)
    by_target = {row[1]: row for row in rows}
    assert by_target["Input Assumptions!J8"][5] is False
    assert by_target["Input Assumptions!J8"][6] is None
    for addr in ("K8", "L8", "M8", "N8", "O8"):
        row = by_target[f"Input Assumptions!{addr}"]
        assert row[5] is False
        assert row[6] == "empty_range_member"


def test_unknown_sheet_is_missing_sheet() -> None:
    edges = [Edge(kind="ref", source="P&L!A1", target="Ghost!Z9")]
    rows = expand_cell_edges(edges, {"P&L!A1"}, known_sheets={"P&L"})
    assert rows[0][5] is True
    assert rows[0][6] == "missing_sheet"


def test_single_missing_ref_is_missing_cell() -> None:
    edges = [Edge(kind="cross_sheet", source="P&L!A1", target="Operation!C14")]
    rows = expand_cell_edges(edges, {"P&L!A1"}, known_sheets={"P&L", "Operation"})
    assert rows[0][5] is True
    assert rows[0][6] == "missing_cell"
