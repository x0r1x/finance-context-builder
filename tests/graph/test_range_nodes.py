"""Whole columns and large ranges go through one range node per book."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
from finance_context.formulas import csr
from finance_context.formulas.csr import (
    RANGE_MEMBER_KIND,
    expand_cell_edges,
    expanded_edge_count,
    is_range_node,
    range_node_for,
    sheet_usage,
)
from finance_context.formulas.models import Edge
from finance_context.graph.cycles import classify_cycles
from finance_context.graph.trace import trace_graph
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet

_DATA_ROWS = 30
_REPORT_ROWS = 12
_YEARS = ("2024", "2025", "2026")
_COLS = ("B", "C", "D")


def _book(path: Path) -> Path:
    data = [CellSpec(addr="A1", value="Item", type="s")]
    data += [
        CellSpec(addr=f"{col}1", value=year, type="s")
        for col, year in zip(_COLS, _YEARS, strict=True)
    ]
    for row in range(2, _DATA_ROWS + 2):
        data.append(CellSpec(addr=f"A{row}", value=f"k{row % 4}", type="s"))
        data += [CellSpec(addr=f"{col}{row}", value=str(row)) for col in _COLS]
    report = [CellSpec(addr="A1", value="Item", type="s")]
    report += [
        CellSpec(addr=f"{col}1", value=year, type="s")
        for col, year in zip(_COLS, _YEARS, strict=True)
    ]
    for row in range(2, _REPORT_ROWS + 2):
        report.append(CellSpec(addr=f"A{row}", value=f"k{row % 4}", type="s"))
        for col in _COLS:
            report.append(
                CellSpec(
                    addr=f"{col}{row}",
                    value="1",
                    formula=f"SUMIF(Data!$A:$A,$A{row},Data!{col}:{col})",
                )
            )
    total = _REPORT_ROWS + 2
    report.append(CellSpec(addr=f"A{total}", value="Total", type="s"))
    report += [
        CellSpec(addr=f"{col}{total}", value="1", formula=f"SUM({col}2:{col}{total - 1})")
        for col in _COLS
    ]
    labels = ["Item", *_YEARS, *(f"k{n}" for n in range(4)), "Total"]
    return build_xlsx(
        path,
        sheets=[SheetSpec(name="Data", cells=data), SheetSpec(name="Report", cells=report)],
        shared_strings=labels,
    )


def _run(tmp_path: Path, name: str) -> Path:
    source = _book(tmp_path / f"{name}.xlsx")
    dest = tmp_path / name
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    pipeline = Pipeline(Settings(data_dir=tmp_path / f"data-{name}", _env_file=None))
    pipeline.run(dest, job_id=f"{name}-job", source_filename=f"{name}.xlsx")
    return dest


def _without_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(csr, "range_node_for", lambda target, usage: None)


def _trace(dest: Path, origin: str, direction: str) -> tuple[list, list]:
    doc = trace_graph(dest, origin=origin, direction=direction, depth=4)
    nodes = sorted((node.node_id, node.depth) for node in doc.nodes)
    edges = sorted((edge.source, edge.target, edge.kind) for edge in doc.edges)
    return nodes, edges


def test_copied_down_whole_column_lists_members_once(tmp_path: Path) -> None:
    dest = _run(tmp_path, "nodes")
    edges = read_parquet(dest / "ir" / "cell_edges.parquet")
    members = [edge for edge in edges if edge["kind"] == RANGE_MEMBER_KIND]
    by_node: dict[str, set[str]] = {}
    for edge in members:
        by_node.setdefault(edge["source"], set()).add(edge["target"])
    # Data!A:A plus one column node per period. Each lists its cells once.
    assert set(by_node) == {"Data!A:A", "Data!B:B", "Data!C:C", "Data!D:D"}
    assert len(members) == sum(len(cells) for cells in by_node.values())
    assert by_node["Data!B:B"] == {f"Data!B{row}" for row in range(1, _DATA_ROWS + 2)}
    into = [edge for edge in edges if edge["kind"] == "range" and is_range_node(edge["target"])]
    # Two node edges per SUMIF cell. The 12-row SUM stays an ordinary range.
    assert len(into) == _REPORT_ROWS * len(_COLS) * 2
    # range_ref keeps the formula's own target, which the parser already stripped of $.
    assert {edge["range_ref"] for edge in into if edge["target"] == "Data!A:A"} == {"Data!A:A"}
    assert all(edge["truncated"] is False for edge in into + members)
    graph = json.loads((dest / "graph.json").read_text(encoding="utf-8"))
    assert graph["kinds"][RANGE_MEMBER_KIND] == len(members)


def test_context_trace_and_classes_match_per_formula_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_nodes = _run(tmp_path, "nodes")
    with monkeypatch.context() as patch:
        _without_nodes(patch)
        expanded = _run(tmp_path, "expanded")

    def context(dest: Path) -> list:
        payload = json.loads((dest / "context.json").read_text(encoding="utf-8"))
        return payload["blocks"]

    assert context(with_nodes) == context(expanded)

    def links(dest: Path) -> dict:
        graph = json.loads((dest / "graph.json").read_text(encoding="utf-8"))
        return {link["cell"]: (link["formula_class"], link["refs"]) for link in graph["links"]}

    assert links(with_nodes) == links(expanded)

    for origin, direction in (
        ("Report!C3", "precedents"),
        ("Report!C14", "precedents"),
        ("Data!C5", "dependents"),
        ("Data!A7", "dependents"),
    ):
        assert _trace(with_nodes, origin, direction) == _trace(expanded, origin, direction)
    nodes, edges = _trace(with_nodes, "Data!C5", "dependents")
    assert ("Report!C2", 1) in nodes
    assert ("Report!C14", 2) in nodes
    assert ("Report!C2", "Data!C:C", "range") in edges
    assert not any(is_range_node(node) for node, _depth in nodes)


def test_estimate_counts_a_shared_node_once() -> None:
    usage = sheet_usage([f"Data!A{row}" for row in range(1, 101)])
    seen: set[str] = set()
    per_formula = [
        expanded_edge_count([Edge(kind="range", source=f"R!B{n}", target="Data!A:A")], usage, seen)
        for n in range(1, 51)
    ]
    assert per_formula[0] == 1 + 100
    assert per_formula[1:] == [1] * 49


def test_small_range_still_expands_per_formula() -> None:
    usage = sheet_usage([f"Data!A{row}" for row in range(1, 101)])
    assert range_node_for("Data!A1:A64", usage) is None
    assert range_node_for("Data!A1:A65", usage) is not None
    assert range_node_for("Data!A1:A65", None) is None
    assert range_node_for("data!$A:$A", usage).node_id == "Data!A:A"
    assert range_node_for("Data!3:5", usage).node_id == "Data!3:5"


def test_cycle_through_a_node_keeps_only_cells() -> None:
    usage = sheet_usage([f"Data!A{row}" for row in range(1, 101)])
    edges = [Edge(kind="range", source="Data!A100", target="Data!A:A")]
    known = {f"Data!A{row}" for row in range(1, 101)}
    rows = expand_cell_edges(edges, known, known_sheets={"Data"}, usage=usage)
    cycles = classify_cycles([row._replace(period_lag="same") for row in rows])
    assert [cycle.members for cycle in cycles] == [["Data!A100"]]
    assert cycles[0].class_ == "unexpected"


def _adjacency_reference(edges: list[Edge], limit: int, usage) -> tuple[dict, dict]:
    """One pass per edge, then cut: the behaviour before range rows were cached."""
    from collections import defaultdict

    from finance_context.layout.adjacency import _targets, cell_ref_row, row_key_ref

    precedents: dict = defaultdict(dict)
    dependents: dict = defaultdict(dict)
    for edge in edges:
        source = cell_ref_row(edge.source)
        for ref in _targets(edge, usage):
            target = cell_ref_row(ref)
            if target is None or source == target:
                continue
            precedents[source].setdefault(row_key_ref(*target), None)
            dependents[target].setdefault(row_key_ref(*source), None)
    cut = limit if limit > 0 else None
    return (
        {key: list(values)[:cut] for key, values in precedents.items()},
        {key: list(values)[:cut] for key, values in dependents.items()},
    )


@pytest.mark.parametrize("limit", [0, 1, 3, 7, 50])
def test_row_adjacency_limit_keeps_the_first_entries_in_order(limit: int) -> None:
    import random

    from finance_context.layout.adjacency import row_adjacency

    rng = random.Random(limit)
    nodes = [f"Data!{col}{row}" for col in "ABC" for row in range(1, 41)]
    nodes += [f"Calc!{col}{row}" for col in "AB" for row in range(1, 31)]
    usage = sheet_usage(nodes)
    targets = ["Data!A:A", "Data!B1:B20", "Data!A5:C9", "Calc!A1:A30", "Data!C7"]
    edges = [
        Edge(
            kind="ref" if ":" not in target else "range",
            source=f"Calc!{rng.choice('AB')}{rng.randint(1, 30)}",
            target=target,
        )
        for target in (rng.choice(targets) for _ in range(300))
    ]
    assert row_adjacency(edges, limit=limit, usage=usage) == _adjacency_reference(
        edges, limit, usage
    )
