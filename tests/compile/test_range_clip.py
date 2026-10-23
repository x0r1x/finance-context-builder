"""Whole-column and oversized ranges intersect the sheet's present cells."""

from __future__ import annotations

from pathlib import Path

from tests.helpers.parquet import load_parquet
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel import parse_workbook
from finance_context.formulas import compile_workbook
from finance_context.formulas.csr import (
    expand_cell_edges,
    expand_range,
    expansion_count,
    sheet_usage,
)
from finance_context.formulas.models import Edge
from finance_context.layout.adjacency import row_adjacency
from finance_context.memory import assess_raw
from finance_context.settings import DEFAULT_RANGE_EXPAND_CAP as RANGE_EXPAND_CAP


def _used(rows: int = 30) -> object:
    nodes = [f"Data!A{row}" for row in range(1, rows + 1)]
    nodes += [f"Data!C{row}" for row in range(1, rows + 1)]
    nodes.append("Report!A1")
    nodes.append("Report!C1")
    return sheet_usage(nodes)


def test_whole_column_and_oversized_ranges_keep_only_present_cells() -> None:
    usage = _used()
    column, truncated = expand_range("Data!$A:$A", usage=usage)
    assert truncated is False
    assert column == [f"Data!A{row}" for row in range(1, 31)]
    assert expansion_count("Data!$A:$A", usage=usage) == 30
    assert expansion_count("Data!$A:$A") == RANGE_EXPAND_CAP

    oversized, oversized_truncated = expand_range("Data!C1:C2500", usage=usage)
    assert oversized_truncated is True
    assert len(oversized) == RANGE_EXPAND_CAP
    assert oversized[0] == "Data!C1"
    assert "Data!C2500" not in oversized
    assert expansion_count("Data!C1:C2500", usage=usage) == RANGE_EXPAND_CAP

    whole_row, _row_truncated = expand_range("Data!1:1", usage=usage)
    assert whole_row == ["Data!A1", "Data!C1"]

    both, _both_truncated = expand_range("Data!A:C", usage=usage)
    assert both[0] == "Data!A1"
    assert both[1] == "Data!C1"
    assert len(both) == 60


def test_small_range_still_includes_holes_when_usage_is_known() -> None:
    usage = sheet_usage(["Input Assumptions!C8", "Input Assumptions!J8"])
    edges = [
        Edge(
            kind="range",
            source="Input Assumptions!C8",
            target="Input Assumptions!J8:O8",
        )
    ]
    rows = expand_cell_edges(
        edges,
        {"Input Assumptions!C8", "Input Assumptions!J8"},
        known_sheets={"Input Assumptions"},
        usage=usage,
    )
    by_target = {row.target: row for row in rows}
    assert by_target["Input Assumptions!J8"].dangling_reason is None
    for addr in ("K8", "L8", "M8", "N8", "O8"):
        assert by_target[f"Input Assumptions!{addr}"].dangling_reason == "empty_range_member"


def test_whole_column_sumif_edge_count_follows_used_rows(tmp_path: Path, dest: Path) -> None:
    used_rows = 40
    data_cells = []
    for row in range(1, used_rows + 1):
        data_cells.append(CellSpec(addr=f"A{row}", value=str(row % 7)))
        data_cells.append(CellSpec(addr=f"C{row}", value=str(row)))
    source = tmp_path / "sumif.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(name="Data", cells=data_cells),
            SheetSpec(
                name="Report",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(
                        addr="C1",
                        value="1",
                        formula="SUMIF(Data!A:A,A1,Data!C:C)",
                    ),
                ],
            ),
        ],
    )
    parse_workbook(source, dest)
    estimate = assess_raw(dest)
    # Two whole columns are two range nodes, one edge each whatever their size.
    # A1 is one more edge. The old cap would count 2000 each.
    assert estimate.expanded_cells == 3
    compile_workbook(dest)
    all_edges = load_parquet(dest / "ir" / "cell_edges.parquet")
    # The node edges are all there is: no row lists the column's cells.
    assert {(edge["source"], edge["target"]) for edge in all_edges} == {
        ("Report!C1", "Data!A:A"),
        ("Report!C1", "Report!A1"),
        ("Report!C1", "Data!C:C"),
    }
    assert all(edge["truncated"] is False for edge in all_edges)

    formula_edges = [
        edge
        for edge in load_parquet(dest / "ir" / "edges.parquet")
        if edge["source"] == "Report!C1" and edge["kind"] == "range"
    ]
    precedents, _dependents = row_adjacency(formula_edges, limit=0, usage=_used(used_rows))
    assert len(precedents[("Report", 1)]) == used_rows


def test_clipped_sheet_names_follow_the_workbook_spelling() -> None:
    usage = sheet_usage([f"Data!A{row}" for row in range(1, 6)])
    cells, truncated = expand_range("data!A:A", usage=usage)
    assert truncated is False
    assert cells == [f"Data!A{row}" for row in range(1, 6)]
    assert expansion_count("data!A:A", usage=usage) == 5


def test_bounded_range_over_the_cap_stays_truncated_and_keeps_holes() -> None:
    present = [f"Data!A{row}" for row in range(1, 2002) if row != 5]
    usage = sheet_usage(present)
    cells, truncated = expand_range("Data!A1:A2001", usage=usage)
    assert truncated is True
    assert len(cells) == RANGE_EXPAND_CAP
    assert "Data!A5" in cells
    assert "Data!A2001" not in cells
    assert expansion_count("Data!A1:A2001", usage=usage) == RANGE_EXPAND_CAP


def test_lowercase_sheet_sum_keeps_canonical_precedents(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "case.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(
                name="Data",
                cells=[CellSpec(addr=f"A{row}", value=str(row)) for row in range(1, 6)],
            ),
            SheetSpec(
                name="Report",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(addr="C1", value="1", formula="SUM(data!A:A)"),
                ],
            ),
        ],
    )
    parse_workbook(source, dest)
    compile_workbook(dest)
    edges = load_parquet(dest / "ir" / "cell_edges.parquet")
    targets = {edge["target"] for edge in edges if edge["source"] == "Report!C1"}
    # The lowercase reference lands on the workbook's spelling of the sheet.
    assert targets == {"Data!A:A"}


def test_row_adjacency_dedupes_without_repeating_a_target() -> None:
    rows = 40
    usage = sheet_usage([f"Data!A{row}" for row in range(1, rows + 1)] + ["Report!C1"])
    edge = Edge(kind="range", source="Report!C1", target="Data!A:A")
    precedents, _dependents = row_adjacency([edge, edge], limit=0, usage=usage)
    assert precedents[("Report", 1)] == [f"Data!{row}" for row in range(1, rows + 1)]
