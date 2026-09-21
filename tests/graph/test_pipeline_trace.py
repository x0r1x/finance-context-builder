from __future__ import annotations

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
