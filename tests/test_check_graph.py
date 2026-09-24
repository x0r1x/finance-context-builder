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
        "schema_version": "1.13.0",
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


def test_accepts_slim_period_without_empty_phase_fields(tmp_path: Path) -> None:
    context = _write(
        tmp_path / "context.json",
        _ok_context(
            axes=[
                {
                    "id": "P&L!r1",
                    "sheet": "P&L",
                    "grain": "year",
                    "header_row": 1,
                    "periods": [
                        {
                            "col": 2,
                            "text": "2024",
                            "role": "historical",
                            "period_key": "2024",
                            "index": 1,
                        }
                    ],
                }
            ]
        ),
    )
    graph = _write(tmp_path / "graph.json", _ok_graph())
    result = _run(str(context), str(graph))
    assert result.returncode == 0, result.stderr


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


def test_markdown_axis_must_head_a_period_column_table(tmp_path: Path) -> None:
    axis = {
        "id": "PF Model!r9",
        "sheet": "PF Model",
        "grain": "year",
        "header_row": 9,
        "periods": [
            {"col": 14, "period_key": "2021"},
            {"col": 15, "period_key": "2022"},
        ],
    }
    context = _write(tmp_path / "context.json", _ok_context(axes=[axis]))
    graph = _write(tmp_path / "graph.json", _ok_graph())
    body = "EBITDA\nP&L\\|13\\|P&L!r2\npnl.ebitda\nP&L!r2\n=SUM(RC[-4]:RC[-1])\n"
    graph_md = tmp_path / "graph.md"
    graph_md.write_text(
        "Nodes: 8\nEdges: 20\nP&L!C13\nP&L\\|13\\|P&L!r2\n2024\naggregation\n=SUM(C9:C12)\n",
        encoding="utf-8",
    )
    context_md = tmp_path / "context.md"
    args = (str(context), str(graph), "--context-md", str(context_md), "--graph-md", str(graph_md))

    context_md.write_text(
        "### `PF Model!r9`\n\n| Period |\n| --- |\n| 2021 |\n| 2022 |\n\n" + body,
        encoding="utf-8",
    )
    result = _run(*args)
    assert result.returncode == 1
    assert "PF Model!r9" in result.stderr

    context_md.write_text(
        "### `PF Model!r9`\n\n| PF Model!r9 | 2021 | 2022 |\n| --- | --- | --- |\n\n" + body,
        encoding="utf-8",
    )
    result = _run(*args)
    assert result.returncode == 0, result.stderr


def _dated_axis(*periods: tuple[int, str, str, str]) -> dict:
    return {
        "id": "PF!r7",
        "sheet": "PF",
        "grain": "year",
        "header_row": 7,
        "periods": [
            {"col": col, "period_key": key, "start_date": start, "end_date": end}
            for col, key, start, end in periods
        ],
    }


def test_axis_periods_must_be_contiguous_dates(tmp_path: Path) -> None:
    axis = _dated_axis(
        (13, "2024", "2024-01-01", "2024-12-31"),
        (14, "2026", "2026-01-01", "2026-12-31"),
    )
    context = _write(tmp_path / "context.json", _ok_context(axes=[axis]))
    graph = _write(tmp_path / "graph.json", _ok_graph())
    result = _run(str(context), str(graph))
    assert result.returncode == 1
    assert "gap before 2026" in result.stderr


def test_shifted_sheet_total_is_not_a_period_of_the_shared_axis(tmp_path: Path) -> None:
    axis = {
        "id": "TBA!r2",
        "sheet": "TBA",
        "grain": "model_year",
        "header_row": 2,
        "periods": [
            {"col": 4, "period_key": "Y1"},
            {"col": 5, "period_key": "Y2"},
        ],
    }
    payload = _ok_context(axes=[axis])
    payload["blocks"] = [
        {
            "block_id": "Operation!r2",
            "sheet": "Operation",
            "axis_ids": ["TBA!r2"],
            "periods": [
                {"col": 5, "period_key": "Y1"},
                {"col": 6, "period_key": "Y2"},
            ],
            "rows": [
                {
                    "row_key": "Operation|5|Operation!r2",
                    "sheet": "Operation",
                    "row": 5,
                    "label": "Amount",
                    "concept_id": "pnl.revenue",
                    "formula": "=1",
                    "cells": [{"col": 4, "role": "total", "cached_value": "9"}],
                    "values": ["1", "2"],
                    "value_statuses": ["cached", "cached"],
                    "normalized_values": ["1", "2"],
                    "scale_factor": 1,
                    "period_position": None,
                    "aggregation": None,
                }
            ],
        }
    ]
    context = _write(tmp_path / "context.json", payload)
    graph = _write(tmp_path / "graph.json", _ok_graph(links=[]))
    result = _run(str(context), str(graph))
    assert result.returncode == 0, result.stderr

    context_md = tmp_path / "context.md"
    context_md.write_text(
        "\n| TBA!r2 | Y1 | Y2 |\n\n"
        "Operation!r2\nAmount\nOperation\\|5\\|Operation!r2\n"
        "pnl.revenue\n=1\nD total: 9\n[Operation!E5] [Operation!F5]\n",
        encoding="utf-8",
    )
    result = _run(str(context), str(graph), "--context-md", str(context_md))
    assert result.returncode == 0, result.stderr
    assert "Operation!D5" not in context_md.read_text(encoding="utf-8")


def test_axis_must_not_publish_a_total_column(tmp_path: Path) -> None:
    axis = _dated_axis(
        (12, "2023", "2023-01-01", "2023-12-31"),
        (13, "2024", "2024-01-01", "2024-12-31"),
    )
    payload = _ok_context(axes=[axis])
    payload["blocks"][0]["axis_ids"] = ["PF!r7"]
    payload["blocks"][0]["rows"][0]["cells"] = [
        {"addr": "L13", "col": 12, "role": "total", "cached_value": "36"}
    ]
    context = _write(tmp_path / "context.json", payload)
    graph = _write(tmp_path / "graph.json", _ok_graph())
    result = _run(str(context), str(graph))
    assert result.returncode == 1
    assert "total column" in result.stderr


def test_markdown_repeats_period_dates_and_role_cells(tmp_path: Path) -> None:
    axis = _dated_axis(
        (13, "2024", "2024-01-01", "2024-12-31"),
        (14, "2025", "2025-01-01", "2025-12-31"),
    )
    payload = _ok_context(axes=[axis])
    payload["blocks"][0]["rows"][0]["cells"] = [
        {"addr": "E13", "col": 5, "role": "unit", "cached_value": "EUR'000"},
        {"addr": "L13", "col": 12, "role": "total", "cached_value": "36"},
    ]
    context = _write(tmp_path / "context.json", payload)
    graph = _write(tmp_path / "graph.json", _ok_graph())
    graph_md = tmp_path / "graph.md"
    graph_md.write_text(
        "Nodes: 8\nEdges: 20\nP&L!C13\nP&L\\|13\\|P&L!r2\n2024\naggregation\n=SUM(C9:C12)\n",
        encoding="utf-8",
    )
    context_md = tmp_path / "context.md"
    body = "EBITDA\nP&L\\|13\\|P&L!r2\npnl.ebitda\nP&L!r2\n=SUM(RC[-4]:RC[-1])\n"
    args = (str(context), str(graph), "--context-md", str(context_md), "--graph-md", str(graph_md))

    context_md.write_text(
        "\n| PF!r7 | 2024 | 2025 |\n| --- | --- | --- |\n" + body, encoding="utf-8"
    )
    result = _run(*args)
    assert result.returncode == 1
    assert "missing Start row" in result.stderr

    axes_md = (
        "\n| PF!r7 | 2024 | 2025 |\n| --- | --- | --- |\n"
        "| Start | 2024-01-01 | 2025-01-01 |\n| End | 2024-12-31 | 2025-12-31 |\n"
    )
    context_md.write_text(axes_md + body, encoding="utf-8")
    result = _run(*args)
    assert result.returncode == 1
    assert "missing total cell L13" in result.stderr

    context_md.write_text(axes_md + body + "L total: 36\n", encoding="utf-8")
    result = _run(*args)
    assert result.returncode == 0, result.stderr


def test_normalized_value_that_only_reformats_the_cache_is_not_required(tmp_path: Path) -> None:
    payload = _ok_context()
    row = payload["blocks"][0]["rows"][0]
    row["values"] = ["13.100000000000001", "23"]
    row["normalized_values"] = ["13.1", "23"]
    context = _write(tmp_path / "context.json", payload)
    graph = _write(tmp_path / "graph.json", _ok_graph())
    context_md = tmp_path / "context.md"
    context_md.write_text(
        "EBITDA\nP&L\\|13\\|P&L!r2\npnl.ebitda\nP&L!r2\n=SUM(RC[-4]:RC[-1])\n", encoding="utf-8"
    )
    graph_md = tmp_path / "graph.md"
    graph_md.write_text(
        "Nodes: 8\nEdges: 20\nP&L!C13\nP&L\\|13\\|P&L!r2\n2024\naggregation\n=SUM(C9:C12)\n",
        encoding="utf-8",
    )
    result = _run(
        str(context), str(graph), "--context-md", str(context_md), "--graph-md", str(graph_md)
    )
    assert result.returncode == 0, result.stderr


def test_axes_summary_counts_phases(tmp_path: Path) -> None:
    axis = _dated_axis(
        (13, "2024", "2024-01-01", "2024-12-31"),
        (14, "2025", "2025-01-01", "2025-12-31"),
    )
    axis["periods"][0]["phase"] = "construction"
    axis["periods"][1]["phase"] = "operation"
    context = _write(tmp_path / "context.json", _ok_context(axes=[axis]))
    graph = _write(tmp_path / "graph.json", _ok_graph())
    result = _run(str(context), str(graph), "--axes-summary")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "axes=1 blocks=1 params_blocks=0; axis=PF!r7 periods=2 span=2024..2025 "
        "construction=1 operation=1"
    )


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


def test_period_value_must_carry_the_graph_cell(tmp_path: Path) -> None:
    payload = _ok_context()
    row = payload["blocks"][0]["rows"][0]
    row["sheet"] = "P&L"
    row["row"] = 13
    payload["blocks"][0]["periods"] = [{"col": 3, "period_key": "2024", "text": "2024"}]
    context = _write(tmp_path / "context.json", payload)
    graph = _write(tmp_path / "graph.json", _ok_graph())
    context_md = tmp_path / "context.md"
    graph_md = tmp_path / "graph.md"
    graph_md.write_text(
        "Nodes: 8\nEdges: 20\nP&L!C13\nP&L\\|13\\|P&L!r2\n2024\naggregation\n=SUM(C9:C12)\n",
        encoding="utf-8",
    )
    context_md.write_text(
        "EBITDA\nP&L\\|13\\|P&L!r2\npnl.ebitda\nP&L!r2\n=SUM(RC[-4]:RC[-1])\n13\n",
        encoding="utf-8",
    )
    args = (
        str(context),
        str(graph),
        "--context-md",
        str(context_md),
        "--graph-md",
        str(graph_md),
    )
    bare = _run(*args)
    assert bare.returncode == 1
    assert "missing [P&L!C13]" in bare.stderr
    assert "=SUM(C9:C12)" not in bare.stderr

    context_md.write_text(
        "EBITDA\nP&L\\|13\\|P&L!r2\npnl.ebitda\nP&L!r2\n=SUM(RC[-4]:RC[-1])\n13 [P&L!C13]\n",
        encoding="utf-8",
    )
    linked = _run(*args)
    assert linked.returncode == 0, linked.stderr


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
