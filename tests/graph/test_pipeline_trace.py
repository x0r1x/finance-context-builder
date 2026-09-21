from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
from finance_context.graph.trace import trace_graph
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet


def _finance_book(path: Path) -> Path:
    cells: list[CellSpec] = [
        CellSpec(addr="A1", value="Item", type="s"),
        CellSpec(addr="B1", value="2023", type="s"),
        CellSpec(addr="C1", value="2024", type="s"),
        CellSpec(addr="D1", value="2025", type="s"),
        CellSpec(addr="A9", value="Gross revenues", type="s"),
        CellSpec(addr="B9", value="10", formula="=Operation!C14"),
        CellSpec(addr="C9", value="20", formula="=Operation!D14"),
        CellSpec(addr="D9", value="30", formula="=Operation!E14"),
        CellSpec(addr="A10", value="Opex", type="s"),
        CellSpec(addr="B10", value="1"),
        CellSpec(addr="C10", value="1"),
        CellSpec(addr="D10", value="1"),
        CellSpec(addr="A11", value="Other", type="s"),
        CellSpec(addr="B11", value="1"),
        CellSpec(addr="C11", value="1"),
        CellSpec(addr="D11", value="1"),
        CellSpec(addr="A12", value="Misc", type="s"),
        CellSpec(addr="B12", value="1"),
        CellSpec(addr="C12", value="1"),
        CellSpec(addr="D12", value="1"),
        CellSpec(addr="A13", value="EBITDA", type="s"),
        CellSpec(addr="B13", value="13", formula="=SUM(B9:B12)"),
        CellSpec(addr="C13", value="23", formula="=SUM(C9:C12)"),
        CellSpec(addr="D13", value="33", formula="=SUM(D9:D12)"),
    ]
    operation = [
        CellSpec(addr="A1", value="Item", type="s"),
        CellSpec(addr="B1", value="2023", type="s"),
        CellSpec(addr="C1", value="2024", type="s"),
        CellSpec(addr="D1", value="2025", type="s"),
        CellSpec(addr="E1", value="2026", type="s"),
        CellSpec(addr="A14", value="Total Revenue (nominal)", type="s"),
        CellSpec(addr="B14", value="5"),
        CellSpec(addr="C14", value="10"),
        CellSpec(addr="D14", value="20"),
        CellSpec(addr="E14", value="30"),
        CellSpec(addr="A20", value="Inflation", type="s"),
        CellSpec(addr="B20", value="0.02"),
    ]
    return build_xlsx(
        path,
        sheets=[
            SheetSpec(name="P&L", cells=cells),
            SheetSpec(name="Operation", cells=operation),
        ],
        shared_strings=[
            "Item",
            "2023",
            "2024",
            "2025",
            "2026",
            "Gross revenues",
            "Opex",
            "Other",
            "Misc",
            "EBITDA",
            "Total Revenue (nominal)",
            "Inflation",
        ],
    )


def test_pipeline_graph_trace_sum_and_period_lag(tmp_path: Path) -> None:
    source = _finance_book(tmp_path / "model.xlsx")
    dest = tmp_path / "job"
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data"), embed=None, chat=None)
    doc = pipeline.run(dest, job_id="graph-job", source_filename="model.xlsx")
    assert (dest / "graph.json").is_file()
    assert (dest / "ir" / "cell_edges.parquet").is_file()
    assert (dest / "ir" / "graph_index.parquet").is_file()
    assert "precedents_rows" not in (dest / "context.json").read_text(encoding="utf-8")
    assert doc.graph.edges > 0
    payload = (dest / "graph.json").read_text(encoding="utf-8")
    assert "formula_ast" not in payload
    assert "precedents_rows" not in payload
    graph = json.loads(payload)
    assert graph["schema_version"] == "1.4.0"
    assert graph["iterate"] is False
    assert graph["artifacts"]["edges_json"] == "graph-edges.json"
    assert graph["artifacts"]["dangling"] == "graph-dangling.json"
    assert graph["artifacts"]["formulas"] == "formulas.json"
    assert "formula_ast" not in json.dumps(graph)
    assert "formula" not in graph
    assert "formula" not in graph.get("contract", {})

    edge_dump = json.loads((dest / "graph-edges.json").read_text(encoding="utf-8"))
    assert edge_dump["direction"] == "formula_depends_on_precedent"
    ebitda_edge = next(
        e for e in edge_dump["edges"] if e["source"] == "P&L!C13" and e["target"] == "P&L!C9"
    )
    assert ebitda_edge["formula_cell"]["node_id"] == "P&L!C13"
    assert ebitda_edge["precedent"]["node_id"] == "P&L!C9"
    assert ebitda_edge["formula_cell"]["sheet"] == "P&L"
    assert ebitda_edge["formula_cell"]["address"] == "C13"
    assert ebitda_edge["precedent"]["address"] == "C9"
    assert ebitda_edge["formula_cell"]["period_id"]
    assert ebitda_edge["reference_kind"] == "range_member"
    assert ebitda_edge["relation_type"] == "formula_reference"
    assert ebitda_edge["anchors"]["abs_col"] is False
    assert ebitda_edge["anchors"]["abs_row"] is False
    assert ebitda_edge["formula"] == "=SUM(C9:C12)"
    assert ebitda_edge["resolution_status"] == "resolved"
    assert ebitda_edge["edge_id"]
    direct = next(
        e
        for e in edge_dump["edges"]
        if e["source"] == "P&L!B9" and e["target"] == "Operation!C14"
    )
    assert direct["reference_kind"] == "direct"
    assert direct["formula"] == "=Operation!C14"
    assert direct["resolution_status"] == "resolved"
    formulas = json.loads((dest / "formulas.json").read_text(encoding="utf-8"))
    by_id = {cell["node_id"]: cell for cell in formulas["cells"]}
    assert by_id["P&L!C13"]["formula"] == "=SUM(C9:C12)"
    dangling = json.loads((dest / "graph-dangling.json").read_text(encoding="utf-8"))
    assert dangling["count"] == len(dangling["ids"])

    context = json.loads((dest / "context.json").read_text(encoding="utf-8"))
    ebitda_values = [
        value
        for block in context["blocks"]
        for metric in block["metrics"]
        if metric.get("label") == "EBITDA"
        for value in metric["values"]
    ]
    assert any(value.get("formula") == "=SUM(C9:C12)" for value in ebitda_values)

    edges = read_parquet(dest / "ir" / "cell_edges.parquet")
    ebitda = [e for e in edges if e["source"] == "P&L!C13"]
    targets = {e["target"] for e in ebitda}
    assert {"P&L!C9", "P&L!C10", "P&L!C11", "P&L!C12"} <= targets

    lag_edges = [e for e in edges if e["source"] == "P&L!B9" and e["target"] == "Operation!C14"]
    assert lag_edges
    assert lag_edges[0]["period_lag"] not in {None, "same"}

    traced = trace_graph(dest, origin="P&L!C13", direction="precedents", depth=6)
    node_ids = {n.node_id for n in traced.nodes}
    assert "P&L!C9" in node_ids
    assert "P&L!C12" in node_ids
    assert any(n.node_id.startswith("Operation!") for n in traced.nodes)


def test_pipeline_classifies_index_range_holes(tmp_path: Path) -> None:
    source = build_xlsx(
        tmp_path / "index.xlsx",
        sheets=[
            SheetSpec(
                name="Input Assumptions",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024", type="s"),
                    CellSpec(addr="A8", value="Inflation", type="s"),
                    CellSpec(addr="C8", value="0.02", formula="=INDEX(J8:O8,1)"),
                    CellSpec(addr="J8", value="0.02"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "2024", "Inflation"],
    )
    dest = tmp_path / "job"
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data"), embed=None, chat=None)
    doc = pipeline.run(dest, job_id="index-job", source_filename="index.xlsx")
    edges = read_parquet(dest / "ir" / "cell_edges.parquet")
    holes = [
        e
        for e in edges
        if e["source"] == "Input Assumptions!C8"
        and str(e["target"]).endswith(("K8", "L8", "M8", "N8", "O8"))
    ]
    assert holes
    assert all(e["dangling_reason"] == "empty_range_member" for e in holes)
    assert all(e["status"] == "empty" and e["reason"] == "actual_blank_cell" for e in holes)
    assert all(e["evidence"] == "omitted_by_excel" for e in holes)
    assert all(not e["dangling"] for e in holes)
    index = {row["node_id"]: row for row in read_parquet(dest / "ir" / "graph_index.parquet")}
    assert index["Input Assumptions!K8"]["node_type"] == "empty"
    dangling = json.loads((dest / "graph-dangling.json").read_text(encoding="utf-8"))
    hole_ids = [
        item["node_id"]
        for item in dangling["ids"]
        if item["class"] == "empty_range_member"
    ]
    assert "Input Assumptions!K8" in hole_ids
    hole = next(item for item in dangling["ids"] if item["node_id"] == "Input Assumptions!K8")
    assert hole["status"] == "empty"
    assert "period_id" in hole
    assert hole["included_in_formula_semantics"] is True
    edge_dump = json.loads((dest / "graph-edges.json").read_text(encoding="utf-8"))
    empty_edge = next(
        edge
        for edge in edge_dump["edges"]
        if edge["target"] == "Input Assumptions!K8"
    )
    assert empty_edge["resolution_status"] == "empty"
    assert empty_edge["formula_cell"]["node_id"] == empty_edge["source"]
    source = {"node_id": "Input Assumptions!C8", "range": "Input Assumptions!J8:O8"}
    assert source in hole["sources"]
    assert all("empty range" not in warning for warning in doc.warnings)
    assert dangling["count"] == len(dangling["ids"])
    assert dangling["count"] > 32 or dangling["count"] >= len(holes)
    graph = json.loads((dest / "graph.json").read_text(encoding="utf-8"))
    assert graph["dangling"]["count"] == 0
    assert graph["dangling_classes"].get("empty_range_member", 0) >= len(holes)
    assert doc.graph.dangling == 0
    assert doc.graph.empty_range_members >= len(holes)
    traced = trace_graph(dest, origin="Input Assumptions!C8", direction="precedents", depth=2)
    assert "Input Assumptions!K8" in {n.node_id for n in traced.nodes}


def _circular_book(path: Path) -> Path:
    cells = [
        CellSpec(addr="A1", value="Item", type="s"),
        CellSpec(addr="B1", value="2023", type="s"),
        CellSpec(addr="C1", value="2024", type="s"),
        CellSpec(addr="A5", value="Interest", type="s"),
        CellSpec(addr="B5", value="1", formula="=B6"),
        CellSpec(addr="C5", value="1", formula="=C6"),
        CellSpec(addr="A6", value="Uses of funds for circularity breakdown", type="s"),
        CellSpec(addr="B6", value="1", formula="=B5"),
        CellSpec(addr="C6", value="1", formula="=C5"),
    ]
    return build_xlsx(
        path,
        sheets=[SheetSpec(name="CF", cells=cells)],
        shared_strings=[
            "Item",
            "2023",
            "2024",
            "Interest",
            "Uses of funds for circularity breakdown",
        ],
        iterate=True,
    )


def test_pipeline_traces_blank_ref_and_styled_blank(tmp_path: Path) -> None:
    source = build_xlsx(
        tmp_path / "blanks.xlsx",
        sheets=[
            SheetSpec(
                name="CF",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024", type="s"),
                    CellSpec(addr="A5", value="Opening", type="s"),
                    CellSpec(addr="B5", value="1", formula="=A5+B4"),
                    CellSpec(addr="C5", value="1", formula="=SUM(C3:C4)"),
                    CellSpec(addr="C3", style=1),
                ],
            ),
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="A2", value="Link", type="s"),
                    CellSpec(addr="B2", value="1", formula="=Ghost!A1"),
                ],
            ),
        ],
        shared_strings=["Item", "2023", "2024", "Opening", "Link"],
    )
    dest = tmp_path / "job"
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data"), embed=None, chat=None)
    doc = pipeline.run(dest, job_id="blank-job", source_filename="blanks.xlsx")
    dangling = json.loads((dest / "graph-dangling.json").read_text(encoding="utf-8"))
    by_id = {item["node_id"]: item for item in dangling["ids"]}
    opening = by_id["CF!B4"]
    assert opening["class"] == "empty_ref"
    assert opening["status"] == "empty"
    assert opening["reason"] == "actual_blank_cell"
    assert opening["evidence"] == "omitted_by_excel"
    assert opening["sources"] == [{"node_id": "CF!B5", "range": "CF!B4"}]
    styled = by_id["CF!C3"]
    assert styled["class"] == "empty_range_member"
    assert styled["evidence"] == "styled_blank"
    assert styled["sources"] == [{"node_id": "CF!C5", "range": "CF!C3:C4"}]
    omitted = by_id["CF!C4"]
    assert omitted["evidence"] == "omitted_by_excel"
    ghost = by_id["Ghost!A1"]
    assert ghost["status"] == "unresolved"
    assert ghost["reason"] == "missing_sheet"
    assert ghost["included_in_formula_semantics"] == "unknown"
    assert doc.graph.dangling == 1
    assert any("unresolved formula targets" in warning for warning in doc.warnings)
    assert all("empty range" not in warning for warning in doc.warnings)


def test_pipeline_publishes_iterate_and_cycle_breakers(tmp_path: Path) -> None:
    source = _circular_book(tmp_path / "circular.xlsx")
    dest = tmp_path / "job"
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data"), embed=None, chat=None)
    doc = pipeline.run(dest, job_id="cycle-job", source_filename="circular.xlsx")
    assert doc.workbook.iterate is True
    assert doc.graph.iterate is True
    graph = json.loads((dest / "graph.json").read_text(encoding="utf-8"))
    assert graph["schema_version"] == "1.4.0"
    assert graph["iterate"] is True
    assert graph["cycles"]
    members = {m for cycle in graph["cycles"] for m in cycle["members"]}
    assert {"CF!B5", "CF!B6"} <= members or {"CF!C5", "CF!C6"} <= members
    breakers = {b for cycle in graph["cycles"] for b in cycle.get("breakers") or []}
    assert "CF!B6" in breakers or "CF!C6" in breakers
    assert all(cycle["class"] == "unexpected" for cycle in graph["cycles"])
