"""Whole-column and oversized ranges intersect the sheet's present cells."""

from __future__ import annotations

from pathlib import Path

from tests.helpers.parquet import load_parquet
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel import parse_workbook
from finance_context.formulas import compile_workbook
from finance_context.formulas.csr import (
    RANGE_EXPAND_CAP,
    expand_cell_edges,
    expand_range,
    expansion_count,
    sheet_usage,
)
from finance_context.formulas.models import Edge
from finance_context.layout.adjacency import row_adjacency
from finance_context.memory import assess_raw


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
    assert oversized_truncated is False
    assert oversized == [f"Data!C{row}" for row in range(1, 31)]
    assert "Data!C2500" not in oversized

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
    # Two whole columns, only the populated rows. The old cap would count 2000 each.
    assert estimate.expanded_cells == used_rows * 2 + 1
    assert estimate.expanded_cells < RANGE_EXPAND_CAP
    compile_workbook(dest)
    cell_edges = [
        edge
        for edge in load_parquet(dest / "ir" / "cell_edges.parquet")
        if edge["source"] == "Report!C1"
    ]
    assert len(cell_edges) == used_rows * 2 + 1
    assert all(edge["truncated"] is False for edge in cell_edges)
    targets = {edge["target"] for edge in cell_edges}
    assert "Data!A1" in targets
    assert "Data!A40" in targets
    assert "Data!C40" in targets
    assert "Data!A41" not in targets
    assert "Data!C2000" not in targets

    formula_edges = [
        edge
        for edge in load_parquet(dest / "ir" / "edges.parquet")
        if edge["source"] == "Report!C1" and edge["kind"] == "range"
    ]
    precedents, _dependents = row_adjacency(formula_edges, limit=0, usage=_used(used_rows))
    assert len(precedents[("Report", 1)]) == used_rows
