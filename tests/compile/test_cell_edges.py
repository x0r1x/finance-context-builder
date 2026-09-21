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


def test_single_blank_ref_is_empty_ref() -> None:
    edges = [Edge(kind="cross_sheet", source="P&L!A1", target="Operation!C14")]
    rows = expand_cell_edges(edges, {"P&L!A1"}, known_sheets={"P&L", "Operation"})
    assert rows[0][5] is False
    assert rows[0][6] == "empty_ref"
    assert rows[0].status == "empty"
    assert rows[0].reason == "actual_blank_cell"
    assert rows[0].evidence == "omitted_by_excel"
    assert rows[0].range_ref == "Operation!C14"


def test_styled_blank_and_populated_mismatch() -> None:
    edges = [
        Edge(kind="range", source="P&L!A1", target="P&L!B1:B2"),
    ]
    rows = expand_cell_edges(
        edges,
        {"P&L!A1"},
        known_sheets={"P&L"},
        presence={"P&L!B1": "styled_blank", "P&L!B2": "populated"},
    )
    by_target = {row.target: row for row in rows}
    assert by_target["P&L!B1"].evidence == "styled_blank"
    assert by_target["P&L!B1"].status == "empty"
    assert by_target["P&L!B1"].dangling is False
    assert by_target["P&L!B2"].dangling_reason == "parser_resolution_failure"
    assert by_target["P&L!B2"].status == "unresolved"
    assert by_target["P&L!B2"].evidence == "populated_missing_from_index"
    assert by_target["P&L!B2"].dangling is True


def test_absolute_and_range_anchors_are_kept() -> None:
    from finance_context.formulas.engine import FormulaEngine

    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=$C$9+Construction!E19", sheet="Amortization", addr="E8")
    known = {"Amortization!E8", "Amortization!C9", "Construction!E19"}
    rows = expand_cell_edges(
        parsed.edges, known, known_sheets={"Amortization", "Construction"}
    )
    by_target = {row.target: row for row in rows}
    absolute = by_target["Amortization!C9"]
    assert absolute.abs_col is True
    assert absolute.abs_row is True
    relative = by_target["Construction!E19"]
    assert relative.kind == "cross_sheet"
    assert relative.abs_col is False
    assert relative.abs_row is False

    ranged = engine.parse("=SUM($B$9:B12)", sheet="P&L", addr="B13")
    members = expand_cell_edges(
        ranged.edges,
        {"P&L!B13", "P&L!B9", "P&L!B10", "P&L!B11", "P&L!B12"},
        known_sheets={"P&L"},
    )
    assert members
    assert all(row.abs_col is True and row.abs_row is True for row in members)
    assert all(row.abs_col_end is False and row.abs_row_end is False for row in members)


def test_ppmt_and_if_refs_expand_to_cell_edges() -> None:
    from finance_context.formulas.engine import FormulaEngine

    engine = FormulaEngine(locale_hint="en")
    parsed = engine.parse("=PPMT(B1,IF(C1>0,C1,D1),E1,F1)", sheet="Debt", addr="G1")
    known = {"Debt!G1", "Debt!B1", "Debt!C1", "Debt!D1", "Debt!E1", "Debt!F1"}
    rows = expand_cell_edges(parsed.edges, known, known_sheets={"Debt"})
    targets = {row[1] for row in rows if not row[5]}
    assert {"Debt!B1", "Debt!C1", "Debt!D1", "Debt!E1", "Debt!F1"} <= targets
