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
        "schema_version": "1.4.0",
        "job_id": "job-1",
        "nodes": 3,
        "edges": 2,
        "iterate": False,
        "artifacts": {
            "cells": "ir/cells.parquet",
            "edges": "ir/edges.parquet",
            "cell_edges": "ir/cell_edges.parquet",
            "index": "ir/graph_index.parquet",
            "edges_json": "graph-edges.json",
            "dangling": "graph-dangling.json",
            "formulas": "formulas.json",
        },
    }
    body.update(overrides)
    return body


def _ok_context(**overrides: object) -> dict:
    body: dict = {
        "schema_version": "1.4.0",
        "graph": {
            "artifact": "graph.json",
            "nodes": 3,
            "edges": 2,
        },
        "blocks": [
            {
                "metrics": [
                    {
                        "concept_id": "pnl.ebitda",
                        "row_key": "P&L|13",
                        "values": [
                            {
                                "has_formula": True,
                                "source": {"sheet": "P&L", "addr": "C13"},
                            }
                        ],
                    }
                ]
            }
        ],
        "inventory": [{"row_key": "P&L|13", "concept_id": "pnl.ebitda"}],
    }
    body.update(overrides)
    return body


def test_accepts_a1_formula_on_period_values(tmp_path: Path) -> None:
    context = _write(
        tmp_path / "context.json",
        _ok_context(
            blocks=[
                {
                    "metrics": [
                        {
                            "concept_id": "pnl.ebitda",
                            "values": [
                                {
                                    "has_formula": True,
                                    "formula": "=$C$20*0.35",
                                    "source": {"sheet": "P&L", "addr": "C13"},
                                }
                            ],
                        }
                    ]
                }
            ]
        ),
    )
    graph = _write(tmp_path / "graph.json", _ok_graph())

    result = _run(str(context), str(graph), "--print-origin")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "P&L!C13"


def test_rejects_formula_ast_in_context(tmp_path: Path) -> None:
    context = _write(
        tmp_path / "context.json",
        _ok_context(
            blocks=[
                {
                    "metrics": [
                        {
                            "values": [
                                {
                                    "has_formula": True,
                                    "formula": "=C2",
                                    "formula_ast": {"op": "ref"},
                                    "source": {"sheet": "P&L", "addr": "C13"},
                                }
                            ]
                        }
                    ]
                }
            ]
        ),
    )
    graph = _write(tmp_path / "graph.json", _ok_graph())

    result = _run(str(context), str(graph))

    assert result.returncode == 1
    assert "formula_ast" in result.stderr


def test_rejects_missing_edges_json_artifact(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(
        tmp_path / "graph.json",
        _ok_graph(
            artifacts={
                "cells": "ir/cells.parquet",
                "edges": "ir/edges.parquet",
                "cell_edges": "ir/cell_edges.parquet",
                "index": "ir/graph_index.parquet",
            }
        ),
    )

    result = _run(str(context), str(graph))

    assert result.returncode == 1
    assert "artifacts.edges_json" in result.stderr
    assert "artifacts.dangling" in result.stderr
    assert "artifacts.formulas" in result.stderr


def test_accepts_sidecar_and_prints_formula_cell_origin(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(tmp_path / "graph.json", _ok_graph())

    result = _run(str(context), str(graph), "--print-origin")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "P&L!C13"


def test_rejects_row_graph_fields_in_context(tmp_path: Path) -> None:
    context = _write(
        tmp_path / "context.json",
        _ok_context(
            inventory=[
                {
                    "row_key": "P&L|13",
                    "precedents_rows": ["P&L|9"],
                }
            ]
        ),
    )
    graph = _write(tmp_path / "graph.json", _ok_graph())

    result = _run(str(context), str(graph))

    assert result.returncode == 1
    assert "precedents_rows" in result.stderr


def test_rejects_count_mismatch_and_formula_in_graph(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(
        tmp_path / "graph.json",
        _ok_graph(nodes=99, formula_ast={"op": "+"}),
    )

    result = _run(str(context), str(graph))

    assert result.returncode == 1
    assert "nodes=" in result.stderr
    assert "formula_ast" in result.stderr


def test_validates_trace_origin(tmp_path: Path) -> None:
    trace = _write(
        tmp_path / "graph-trace.json",
        {"origin": "P&L!C13", "nodes": [], "edges": []},
    )

    ok = _run("--trace", str(trace), "--origin", "P&L!C13")
    bad = _run("--trace", str(trace), "--origin", "other")

    assert ok.returncode == 0, ok.stderr
    assert bad.returncode == 1
    assert "trace origin" in bad.stderr


def test_rejects_graph_schema_1_0(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(tmp_path / "graph.json", _ok_graph(schema_version="1.0.0"))

    result = _run(str(context), str(graph))

    assert result.returncode == 1
    assert "schema_version" in result.stderr


def test_rejects_graph_schema_1_1(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(tmp_path / "graph.json", _ok_graph(schema_version="1.1.0"))

    result = _run(str(context), str(graph))

    assert result.returncode == 1
    assert "schema_version" in result.stderr


def test_rejects_missing_iterate(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    payload = _ok_graph()
    del payload["iterate"]
    graph = _write(tmp_path / "graph.json", payload)

    result = _run(str(context), str(graph))

    assert result.returncode == 1
    assert "iterate" in result.stderr


def _ok_edge() -> dict:
    return {
        "edge_id": "edge001",
        "direction": "formula_depends_on_precedent",
        "formula_cell": {
            "sheet": "P&L",
            "address": "C13",
            "period_id": "2024",
            "node_id": "P&L!C13",
        },
        "precedent": {
            "sheet": "P&L",
            "address": "C9",
            "period_id": "2024",
            "node_id": "P&L!C9",
        },
        "source": "P&L!C13",
        "target": "P&L!C9",
        "relation_type": "formula_reference",
        "reference_kind": "range_member",
        "anchors": {"abs_col": False, "abs_row": False},
        "formula": "=SUM(C9:C12)",
        "resolution_status": "resolved",
        "kind": "range",
    }


def test_accepts_audit_sidecars(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(tmp_path / "graph.json", _ok_graph())
    edges = _write(
        tmp_path / "graph-edges.json",
        {
            "direction": "formula_depends_on_precedent",
            "edges": [_ok_edge()],
        },
    )
    dangling = _write(
        tmp_path / "graph-dangling.json",
        {
            "count": 2,
            "by_class": {"empty_range_member": 2},
            "ids": [
                {
                    "node_id": "P&L!K8",
                    "period_id": "2024",
                    "class": "empty_range_member",
                    "status": "empty",
                    "reason": "actual_blank_cell",
                    "evidence": "omitted_by_excel",
                    "included_in_formula_semantics": True,
                    "sources": [{"node_id": "P&L!C13", "range": "P&L!K8:L8"}],
                },
                {
                    "node_id": "P&L!L8",
                    "period_id": None,
                    "class": "empty_range_member",
                    "status": "empty",
                    "reason": "actual_blank_cell",
                    "evidence": "styled_blank",
                    "included_in_formula_semantics": True,
                    "sources": [{"node_id": "P&L!C13", "range": "P&L!K8:L8"}],
                },
            ],
        },
    )
    formulas = _write(
        tmp_path / "formulas.json",
        {"cells": [{"node_id": "P&L!C13", "formula": "=SUM(C9:C12)"}]},
    )

    result = _run(
        str(context),
        str(graph),
        "--edges",
        str(edges),
        "--dangling",
        str(dangling),
        "--formulas",
        str(formulas),
        "--print-origin",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "P&L!C13"


def test_rejects_swapped_edge_roles(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(tmp_path / "graph.json", _ok_graph())
    swapped = _ok_edge()
    swapped["source"] = "P&L!C9"
    edges = _write(
        tmp_path / "graph-edges.json",
        {"direction": "formula_depends_on_precedent", "edges": [swapped]},
    )

    result = _run(str(context), str(graph), "--edges", str(edges))

    assert result.returncode == 1
    assert "formula_cell.node_id" in result.stderr


def test_rejects_schema_1_2(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(tmp_path / "graph.json", _ok_graph(schema_version="1.2.0"))

    result = _run(str(context), str(graph))

    assert result.returncode == 1
    assert "schema_version" in result.stderr


def test_rejects_schema_1_3(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(tmp_path / "graph.json", _ok_graph(schema_version="1.3.0"))

    result = _run(str(context), str(graph))

    assert result.returncode == 1
    assert "schema_version" in result.stderr


def test_rejects_empty_status_with_parser_failure(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(tmp_path / "graph.json", _ok_graph())
    dangling = _write(
        tmp_path / "graph-dangling.json",
        {
            "count": 1,
            "by_class": {"empty_range_member": 1},
            "ids": [
                {
                    "node_id": "P&L!K8",
                    "period_id": "2024",
                    "class": "empty_range_member",
                    "status": "empty",
                    "reason": "parser_resolution_failure",
                    "evidence": "populated_missing_from_index",
                    "included_in_formula_semantics": True,
                    "sources": [{"node_id": "P&L!C13", "range": "P&L!K8:K8"}],
                }
            ],
        },
    )

    result = _run(str(context), str(graph), "--dangling", str(dangling))

    assert result.returncode == 1
    assert "parser_resolution_failure" in result.stderr


def test_rejects_truncated_dangling_list(tmp_path: Path) -> None:
    context = _write(tmp_path / "context.json", _ok_context())
    graph = _write(tmp_path / "graph.json", _ok_graph())
    dangling = _write(
        tmp_path / "graph-dangling.json",
        {"count": 718, "by_class": {}, "ids": [{"node_id": "A!J8", "class": "empty_range_member"}]},
    )

    result = _run(str(context), str(graph), "--dangling", str(dangling))

    assert result.returncode == 1
    assert "len(ids)" in result.stderr
