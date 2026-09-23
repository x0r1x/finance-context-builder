from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
from finance_context.formulas.stage import compile_schema_id
from finance_context.graph.stage import build_formula_graph
from finance_context.graph.trace import trace_graph
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet, write_json


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
    assert graph["schema_version"] == "1.7.0"
    assert graph["iterate"] is False
    assert "edges_json" not in graph["artifacts"]
    assert "formula_ast" not in json.dumps(graph)
    assert not (dest / "graph-edges.json").exists()
    assert not (dest / "formulas.json").exists()
    assert (dest / "graph.md").is_file()
    ebitda = next(link for link in graph["links"] if link["cell"] == "P&L!C13")
    assert ebitda["formula"] == "=SUM(C9:C12)"
    assert ebitda["formula_class"] == "aggregation"
    assert ebitda["row_key"] == "P&L|13|P&L!r1"
    assert ebitda["period_id"] == "2024"
    assert any("C9:C12" in ref for ref in ebitda["refs"])
    assert not any(ref.endswith("!C9") for ref in ebitda["refs"])
    direct = next(link for link in graph["links"] if link["cell"] == "P&L!B9")
    assert direct["formula"] == "=Operation!C14"
    assert direct["formula_class"] == "cross_period"
    assert any(ref.endswith("Operation!C14") for ref in direct["refs"])
    assert "P&L!C13" in (dest / "graph.md").read_text(encoding="utf-8")

    context = json.loads((dest / "context.json").read_text(encoding="utf-8"))
    ebitda_row = next(
        row
        for block in context["blocks"]
        for row in block["rows"]
        if row.get("label") == "EBITDA"
    )
    assert ebitda_row["formula"]
    assert isinstance(ebitda_row["values"], list)
    assert all(not isinstance(value, dict) for value in ebitda_row["values"])

    edges = read_parquet(dest / "ir" / "cell_edges.parquet")
    ebitda = [e for e in edges if e["source"] == "P&L!C13"]
    targets = {e["target"] for e in ebitda}
    assert {"P&L!C9", "P&L!C10", "P&L!C11", "P&L!C12"} <= targets
    formula_lag = [
        e for e in edges if e["source"] == "P&L!B9" and e["target"] == "Operation!C14"
    ]
    assert formula_lag
    assert formula_lag[0]["period_lag"] is None

    graph_edges = read_parquet(dest / "ir" / "graph_edges.parquet")
    lag_edges = [
        e for e in graph_edges if e["source"] == "P&L!B9" and e["target"] == "Operation!C14"
    ]
    assert lag_edges
    assert lag_edges[0]["period_lag"] not in {None, "same"}
    assert graph["artifacts"]["graph_edges"] == "ir/graph_edges.parquet"

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
    assert all("empty range" not in warning for warning in doc.warnings)
    graph = json.loads((dest / "graph.json").read_text(encoding="utf-8"))
    assert graph["dangling"]["count"] == 0
    assert graph["dangling_classes"].get("empty_range_member", 0) >= len(holes)
    link = next(item for item in graph["links"] if item["cell"] == "Input Assumptions!C8")
    assert any("J8:O8" in ref for ref in link["refs"])
    assert not any(ref.endswith("K8") for ref in link["refs"])
    assert doc.graph.dangling == 0
    assert doc.graph.empty_range_members >= len(holes)
    traced = trace_graph(dest, origin="Input Assumptions!C8", direction="precedents", depth=2)
    assert "Input Assumptions!K8" not in {n.node_id for n in traced.nodes}
    assert any("J8:O8" in edge.target for edge in traced.edges)
    assert "formula_ast" not in traced.model_dump_json()


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
    edges = read_parquet(dest / "ir" / "cell_edges.parquet")
    by_target = {str(edge["target"]): edge for edge in edges}
    assert by_target["CF!B4"]["dangling_reason"] == "empty_ref"
    assert by_target["CF!B4"]["status"] == "empty"
    assert by_target["CF!B4"]["evidence"] == "omitted_by_excel"
    assert by_target["CF!C3"]["dangling_reason"] == "empty_range_member"
    assert by_target["CF!C3"]["evidence"] == "styled_blank"
    assert by_target["CF!C4"]["evidence"] == "omitted_by_excel"
    assert by_target["Ghost!A1"]["status"] == "unresolved"
    assert by_target["Ghost!A1"]["reason"] == "missing_sheet"
    assert doc.graph.dangling == 1
    graph = json.loads((dest / "graph.json").read_text(encoding="utf-8"))
    assert graph["dangling_classes"].get("empty_ref", 0) >= 1
    assert "Ghost!A1" not in {link["cell"] for link in graph["links"]}
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
    assert graph["schema_version"] == "1.7.0"
    assert graph["iterate"] is True
    assert graph["cycles"]
    members = {m for cycle in graph["cycles"] for m in cycle["members"]}
    assert {"CF!B5", "CF!B6"} <= members or {"CF!C5", "CF!C6"} <= members
    breakers = {b for cycle in graph["cycles"] for b in cycle.get("breakers") or []}
    assert "CF!B6" in breakers or "CF!C6" in breakers
    assert all(cycle["class"] == "unexpected" for cycle in graph["cycles"])


def test_graph_does_not_rewrite_cell_edges(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, bytes] = {}

    def wrapped(dest_dir: Path, **kwargs: object):
        seen["before"] = (dest_dir / "ir" / "cell_edges.parquet").read_bytes()
        result = build_formula_graph(dest_dir, **kwargs)  # type: ignore[arg-type]
        assert (dest_dir / "ir" / "cell_edges.parquet").read_bytes() == seen["before"]
        return result

    monkeypatch.setattr("finance_context.app.pipeline.build_formula_graph", wrapped)
    source = _finance_book(tmp_path / "model.xlsx")
    dest = tmp_path / "job"
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    Pipeline(Settings(data_dir=tmp_path / "data"), embed=None, chat=None).run(
        dest, job_id="bytes-job", source_filename="model.xlsx"
    )
    assert seen["before"]


def test_bad_schema_id_recompiles(tmp_path: Path) -> None:
    source = _finance_book(tmp_path / "model.xlsx")
    dest = tmp_path / "job"
    dest.mkdir()
    (dest / "source.xlsx").write_bytes(source.read_bytes())
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data"), embed=None, chat=None)
    pipeline.run(dest, job_id="stamp-job", source_filename="model.xlsx")
    (dest / "ir" / "cells.parquet").write_bytes(b"broken")
    write_json(
        dest / "ir" / "compile.json",
        {
            "schema_id": "bad",
            "files": ["cells.parquet", "edges.parquet", "cell_edges.parquet"],
        },
    )
    (dest / "context.json").unlink()
    (dest / "context.md").unlink()
    pipeline.run(dest, job_id="stamp-job", source_filename="model.xlsx")
    assert (dest / "ir" / "cells.parquet").read_bytes() != b"broken"
    stamp = json.loads((dest / "ir" / "compile.json").read_text(encoding="utf-8"))
    assert stamp["schema_id"] == compile_schema_id()
