#!/usr/bin/env python3
"""Validate the published formula-graph sidecar against context.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CONTEXT_FORBIDDEN = frozenset(
    {
        "precedents_rows",
        "dependents_rows",
        "precedent_cells",
        "formula_ast",
        "formula_raw",
    }
)
GRAPH_FORBIDDEN = frozenset(
    {
        "precedents_rows",
        "dependents_rows",
        "precedent_cells",
        "formula_ast",
        "formula_raw",
        "formula",
        "inventory",
        "values",
        "blocks",
    }
)
GRAPH_REQUIRED = ("schema_version", "job_id", "nodes", "edges", "artifacts")
ARTIFACT_REQUIRED = (
    "cells",
    "edges",
    "cell_edges",
    "index",
    "edges_json",
    "dangling",
    "formulas",
)
GRAPH_SCHEMA_PREFIX = "1.1"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _walk_keys(node: Any) -> set[str]:
    found: set[str] = set()
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            found.update(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def check_context(context: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    forbidden = _walk_keys(context) & CONTEXT_FORBIDDEN
    if forbidden:
        errors.append(
            "context.json must not embed formula/row-graph fields: "
            + ", ".join(sorted(forbidden))
        )
    pointer = context.get("graph")
    if not isinstance(pointer, dict):
        errors.append("context.json missing graph pointer")
        return errors
    if pointer.get("artifact") != "graph.json":
        errors.append("context.graph.artifact must be graph.json")
    return errors


def check_graph(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = [key for key in GRAPH_REQUIRED if key not in graph]
    if missing:
        errors.append("graph.json missing " + ", ".join(missing))
    schema = str(graph.get("schema_version") or "")
    if schema and not schema.startswith(GRAPH_SCHEMA_PREFIX):
        errors.append(
            f"graph.json schema_version must be {GRAPH_SCHEMA_PREFIX}.x, got {schema!r}"
        )
    for key in ("nodes", "edges"):
        if key in graph and not isinstance(graph[key], int):
            errors.append(f"graph.json {key} must be an int count, not a list")
    forbidden = _walk_keys(graph) & GRAPH_FORBIDDEN
    if forbidden:
        errors.append(
            "graph.json must not embed formulas or report rows: "
            + ", ".join(sorted(forbidden))
        )
    artifacts = graph.get("artifacts")
    if isinstance(artifacts, dict):
        for name in ARTIFACT_REQUIRED:
            path = artifacts.get(name)
            if not isinstance(path, str) or not path:
                errors.append(f"graph.json artifacts.{name} missing")
    elif "artifacts" in graph:
        errors.append("graph.json artifacts must be an object")
    return errors


def check_pointer_matches(context: dict[str, Any], graph: dict[str, Any]) -> list[str]:
    pointer = context.get("graph")
    if not isinstance(pointer, dict):
        return []
    errors: list[str] = []
    for key in ("nodes", "edges"):
        if key in pointer and key in graph and pointer[key] != graph[key]:
            errors.append(
                f"context.graph.{key}={pointer[key]} != graph.json {key}={graph[key]}"
            )
    return errors


def pick_origin(context: dict[str, Any]) -> str:
    for block in context.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for metric in block.get("metrics") or []:
            if not isinstance(metric, dict):
                continue
            for value in metric.get("values") or []:
                if not isinstance(value, dict) or not value.get("has_formula"):
                    continue
                source = value.get("source") if isinstance(value.get("source"), dict) else {}
                sheet, addr = source.get("sheet"), source.get("addr")
                if sheet and addr:
                    return f"{sheet}!{addr}"
            if metric.get("concept_id"):
                return str(metric["concept_id"])
            if metric.get("row_key"):
                return str(metric["row_key"])
    for row in context.get("inventory") or []:
        if not isinstance(row, dict):
            continue
        if row.get("concept_id"):
            return str(row["concept_id"])
        if row.get("row_key"):
            return str(row["row_key"])
    return ""


def check_trace(trace: dict[str, Any], origin: str) -> list[str]:
    errors: list[str] = []
    if trace.get("origin") != origin:
        errors.append(f"trace origin {trace.get('origin')!r} != {origin!r}")
    if not isinstance(trace.get("nodes"), list):
        errors.append("graph-trace.json missing nodes list")
    if not isinstance(trace.get("edges"), list):
        errors.append("graph-trace.json missing edges list")
    return errors


def check_edges_json(doc: Any) -> list[str]:
    if not isinstance(doc, dict) or not isinstance(doc.get("edges"), list):
        return ["graph-edges.json must be an object with an edges list"]
    errors: list[str] = []
    if _walk_keys(doc) & {"formula", "formula_ast", "formula_raw"}:
        errors.append("graph-edges.json must not embed formula text or AST")
    for edge in doc["edges"]:
        if not isinstance(edge, dict) or not edge.get("source") or "target" not in edge:
            errors.append("graph-edges.json entries need source and target")
            break
    return errors


def check_dangling_json(doc: Any) -> list[str]:
    if not isinstance(doc, dict):
        return ["graph-dangling.json must be a JSON object"]
    errors: list[str] = []
    ids = doc.get("ids")
    if not isinstance(ids, list):
        errors.append("graph-dangling.json missing ids list")
        return errors
    if doc.get("count") != len(ids):
        errors.append(
            f"graph-dangling.json count={doc.get('count')!r} != len(ids)={len(ids)}"
        )
    if not isinstance(doc.get("by_class"), dict):
        errors.append("graph-dangling.json missing by_class object")
    return errors


def check_formulas_json(doc: Any) -> list[str]:
    if not isinstance(doc, dict) or not isinstance(doc.get("cells"), list):
        return ["formulas.json must be an object with a cells list"]
    errors: list[str] = []
    for cell in doc["cells"]:
        if not isinstance(cell, dict) or not cell.get("node_id") or not cell.get("formula"):
            errors.append("formulas.json cells need node_id and formula")
            break
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check that context.json and graph.json keep one fact in one place."
    )
    parser.add_argument("context", type=Path, nargs="?", help="path to context.json")
    parser.add_argument("graph", type=Path, nargs="?", help="path to graph.json")
    parser.add_argument(
        "--print-origin",
        action="store_true",
        help="print a smoke-trace origin (formula cell, else concept_id / row_key)",
    )
    parser.add_argument("--trace", type=Path, help="path to graph-trace.json")
    parser.add_argument("--origin", help="expected trace origin (required with --trace)")
    parser.add_argument("--edges", type=Path, help="path to graph-edges.json")
    parser.add_argument("--dangling", type=Path, help="path to graph-dangling.json")
    parser.add_argument("--formulas", type=Path, help="path to formulas.json")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    errors: list[str] = []

    if args.trace is not None:
        if not args.origin:
            parser.error("--trace requires --origin")
        try:
            trace = load_json(args.trace)
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        if not isinstance(trace, dict):
            parser.error("trace document must be a JSON object")
        errors.extend(check_trace(trace, args.origin))
    else:
        if args.context is None or args.graph is None:
            parser.error("context.json and graph.json are required unless --trace is set")
        try:
            context = load_json(args.context)
            graph = load_json(args.graph)
        except (OSError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        if not isinstance(context, dict) or not isinstance(graph, dict):
            parser.error("context.json and graph.json must be JSON objects")
        errors.extend(check_context(context))
        errors.extend(check_graph(graph))
        errors.extend(check_pointer_matches(context, graph))
        for path, checker in (
            (args.edges, check_edges_json),
            (args.dangling, check_dangling_json),
            (args.formulas, check_formulas_json),
        ):
            if path is None:
                continue
            try:
                payload = load_json(path)
            except (OSError, json.JSONDecodeError) as exc:
                parser.error(str(exc))
            errors.extend(checker(payload))
        if args.print_origin and not errors:
            print(pick_origin(context))
            return 0

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
