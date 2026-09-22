from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-graph.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _ok_graph(**overrides: object) -> dict:
    body: dict = {
        "schema_version": "1.7.0",
        "job_id": "job-1",
        "nodes": 8,
        "edges": 20,
        "iterate": False,
        "links": [
            {
                "cell": "P&L!C13",
                "formula": "=SUM(C9:C12)",
                "formula_class": "aggregation",
                "refs": ["P&L!C9:C12"],
                "row_key": "P&L|13|P&L!r2",
                "period_id": "2024",
            },
        ],
        "artifacts": {
            "cells": "ir/cells.parquet",
            "edges": "ir/edges.parquet",
            "cell_edges": "ir/cell_edges.parquet",
            "index": "ir/graph_index.parquet",
        },
    }
    body.update(overrides)
    return body


def _ok_context(**overrides: object) -> dict:
    body: dict = {
        "schema_version": "1.11.0",
        "graph": {"artifact": "graph.json", "nodes": 8, "edges": 20, "iterate": False},
        "blocks": [
            {
                "block_id": "P&L!r2",
                "rows": [
                    {
                        "row_key": "P&L|13|P&L!r2",
                        "label": "EBITDA",
                        "concept_id": "pnl.ebitda",
                        "formula": "=SUM(RC[-4]:RC[-1])",
                        "values": ["13", "23"],
                        "value_statuses": ["cached", "cached"],
                        "normalized_values": ["13", "23"],
                        "scale_factor": 1,
                        "period_position": None,
                        "aggregation": None,
                    }
                ],
            }
        ],
    }
    body.update(overrides)
    return body


def test_accepts_flat_values_and_prints_formula_cell(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(tmp_path / "graph.json", _ok_graph())
    result = _run(str(context), str(graph), "--print-origin")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "P&L!C13"


def test_rejects_second_catalog_and_cell_objects(tmp_path: Path) -> None:
    context = _write(
        tmp_path / "context.json",
        _ok_context(
            blocks=[
                {
                    "block_id": "P&L!r2",
                    "metrics": [],
                    "rows": [
                        {
                            "label": "EBITDA",
                            "values": [
                                {
                                    "cached_value": "1",
                                    "source": {"sheet": "P&L", "addr": "C13"},
                                }
                            ],
                        }
                    ],
                }
            ]
        ),
    )
    graph = _write(tmp_path / "graph.json", _ok_graph())
    result = _run(str(context), str(graph))
    assert result.returncode == 1
    assert "metrics" in result.stderr or "flat list" in result.stderr


def test_rejects_old_graph_schema_and_sidecar_artifacts(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(
        tmp_path / "graph.json",
        _ok_graph(
            schema_version="1.4.0",
            artifacts={
                "cells": "ir/cells.parquet",
                "edges": "ir/edges.parquet",
                "cell_edges": "ir/cell_edges.parquet",
                "index": "ir/graph_index.parquet",
                "edges_json": "graph-edges.json",
            },
        ),
    )
    result = _run(str(context), str(graph))
    assert result.returncode == 1
    assert "1.7" in result.stderr
    assert "edges_json" in result.stderr


def test_markdown_must_repeat_blocks_and_links(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(tmp_path / "graph.json", _ok_graph())
    context_md = tmp_path / "context.md"
    graph_md = tmp_path / "graph.md"
    context_md.write_text(
        "\n".join(
            [
                "# Financial context",
                "",
                "EBITDA",
                "P&L\\|13\\|P&L!r2",
                "pnl.ebitda",
                "P&L!r2",
                "=SUM(RC[-4]:RC[-1])",
                "",
            ]
        ),
        encoding="utf-8",
    )
    graph_md.write_text(
        "\n".join(
            [
                "# Formula graph",
                "Nodes: 8",
                "Edges: 20",
                "P&L!C13",
                "P&L\\|13\\|P&L!r2",
                "2024",
                "aggregation",
                "=SUM(C9:C12)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    result = _run(
        str(context),
        str(graph),
        "--context-md",
        str(context_md),
        "--graph-md",
        str(graph_md),
    )
    assert result.returncode == 0, result.stderr


def test_link_row_key_must_exist_in_context(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(
        tmp_path / "graph.json",
        _ok_graph(
            links=[
                {
                    "cell": "P&L!C13",
                    "formula": "=SUM(C9:C12)",
                    "formula_class": "aggregation",
                    "refs": ["P&L!C9:C12"],
                    "row_key": "P&L|99|P&L!r2",
                    "period_id": "2024",
                }
            ]
        ),
    )
    result = _run(str(context), str(graph))
    assert result.returncode == 1
    assert "row_key" in result.stderr


def test_rejects_merged_cell_contract(tmp_path: Path) -> None:
    context = _write(
        tmp_path / "context.json",
        _ok_context(formulas=[], series=[], audit_trail={}),
    )
    graph = _write(tmp_path / "graph.json", _ok_graph())
    result = _run(str(context), str(graph))
    assert result.returncode == 1
    assert "merged cell contract" in result.stderr


def test_markdown_must_show_status_scale_and_normalized_value(tmp_path: Path) -> None:
    payload = _ok_context()
    row = payload["blocks"][0]["rows"][0]
    row["values"] = [None, "1.5"]
    row["value_statuses"] = ["empty", "cached"]
    row["normalized_values"] = [None, "1500"]
    row["scale_factor"] = 1000
    row["period_position"] = "during_period"
    row["aggregation"] = "sum"
    context = _write(tmp_path / "context.json", payload)
    graph = _write(tmp_path / "graph.json", _ok_graph())
    context_md = tmp_path / "context.md"
    context_md.write_text(
        "EBITDA\nP&L\\|13\\|P&L!r2\npnl.ebitda\nP&L!r2\n=SUM(RC[-4]:RC[-1])\n",
        encoding="utf-8",
    )
    graph_md = tmp_path / "graph.md"
    graph_md.write_text(
        "Nodes: 8\nEdges: 20\nP&L!C13\nP&L\\|13\\|P&L!r2\n2024\naggregation\n=SUM(C9:C12)\n",
        encoding="utf-8",
    )
    result = _run(
        str(context),
        str(graph),
        "--context-md",
        str(context_md),
        "--graph-md",
        str(graph_md),
    )
    assert result.returncode == 1
    assert "empty value status" in result.stderr


def test_trace_rejects_ast(tmp_path: Path) -> None:
    trace = _write(
        tmp_path / "trace.json",
        {
            "origin": "P&L!C13",
            "nodes": [{"node_id": "P&L!C13", "formula_ast": {"op": "func"}}],
            "edges": [],
        },
    )
    result = _run("--trace", str(trace), "--origin", "P&L!C13")
    assert result.returncode == 1
    assert "formula AST" in result.stderr
