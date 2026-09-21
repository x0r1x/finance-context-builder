#!/usr/bin/env python3
"""Validate context.json and graph.json, and that Markdown repeats them."""

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
        "formula_cell",
        "range_member",
        "inventory",
        "blocks",
    }
)
GRAPH_REQUIRED = ("schema_version", "job_id", "nodes", "edges", "iterate", "links", "artifacts")
ARTIFACT_REQUIRED = ("cells", "edges", "cell_edges", "index")
GRAPH_SCHEMA_PREFIX = "1.6"
LINK_FIELDS = ("cell", "formula", "refs")


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
    catalog = [key for key in ("inventory", "unmapped", "excluded") if key in context]
    if catalog:
        errors.append(
            "context.json must not keep a second row catalog: " + ", ".join(catalog)
        )
    forbidden = _walk_keys(context) & CONTEXT_FORBIDDEN
    if forbidden:
        errors.append(
            "context.json must not embed formula AST: " + ", ".join(sorted(forbidden))
        )
    pointer = context.get("graph")
    if not isinstance(pointer, dict):
        errors.append("context.json missing graph pointer")
    elif pointer.get("artifact") != "graph.json":
        errors.append("context.graph.artifact must be graph.json")
    blocks = context.get("blocks")
    if not isinstance(blocks, list):
        errors.append("context.json blocks must be a list")
        return errors
    for block in blocks:
        if not isinstance(block, dict):
            errors.append("context.json blocks must be objects")
            break
        if "metrics" in block:
            errors.append("context.json block must not copy rows into metrics")
            break
        rows = block.get("rows")
        if not isinstance(rows, list):
            errors.append("context.json block missing rows")
            break
        for row in rows:
            if not isinstance(row, dict):
                errors.append("context.json rows must be objects")
                break
            if "source" in row:
                errors.append("context.json row must not repeat a per-cell source")
                break
            values = row.get("values")
            if isinstance(values, list) and any(isinstance(item, dict) for item in values):
                errors.append("context.json values must be a flat list aligned to the block axis")
                break
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
    if "iterate" in graph and not isinstance(graph["iterate"], bool):
        errors.append("graph.json iterate must be a boolean")
    forbidden = _walk_keys(graph) & GRAPH_FORBIDDEN
    if forbidden:
        errors.append(
            "graph.json must not embed AST or exploded range members: "
            + ", ".join(sorted(forbidden))
        )
    artifacts = graph.get("artifacts")
    if isinstance(artifacts, dict):
        for name in ARTIFACT_REQUIRED:
            path = artifacts.get(name)
            if not isinstance(path, str) or not path:
                errors.append(f"graph.json artifacts.{name} missing")
        for retired in ("edges_json", "dangling", "formulas"):
            if retired in artifacts:
                errors.append(f"graph.json artifacts.{retired} is not a public file")
    elif "artifacts" in graph:
        errors.append("graph.json artifacts must be an object")
    links = graph.get("links")
    if isinstance(links, list):
        for link in links:
            if not isinstance(link, dict):
                errors.append("graph.json links must be objects")
                break
            missing_link = [key for key in LINK_FIELDS if key not in link]
            if missing_link:
                errors.append("graph.json links need " + ", ".join(missing_link))
                break
            refs = link.get("refs")
            if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
                errors.append("graph.json link refs must be a list of addresses")
                break
    elif "links" in graph:
        errors.append("graph.json links must be a list")
    return errors


def check_pointer_matches(context: dict[str, Any], graph: dict[str, Any]) -> list[str]:
    pointer = context.get("graph")
    if not isinstance(pointer, dict):
        return []
    errors: list[str] = []
    for key in ("nodes", "edges", "iterate"):
        if key in pointer and key in graph and pointer[key] != graph[key]:
            errors.append(
                f"context.graph.{key}={pointer[key]} != graph.json {key}={graph[key]}"
            )
    return errors


def _in_markdown(text: str, markdown: str) -> bool:
    if text in markdown:
        return True
    return text.replace("|", "\\|") in markdown


def check_link_identity(context: dict[str, Any], graph: dict[str, Any]) -> list[str]:
    keys: set[str] = set()
    for block in context.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for row in block.get("rows") or []:
            if isinstance(row, dict) and row.get("row_key"):
                keys.add(str(row["row_key"]))
    errors: list[str] = []
    for link in graph.get("links") or []:
        if not isinstance(link, dict):
            continue
        row_key = link.get("row_key")
        if row_key and str(row_key) not in keys:
            errors.append(
                f"graph link {link.get('cell')} row_key {row_key} missing from context"
            )
            break
    return errors


def check_context_markdown(context: dict[str, Any], markdown: str) -> list[str]:
    errors: list[str] = []
    for block in context.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        block_id = str(block.get("block_id") or "")
        if block_id and block_id not in markdown:
            errors.append(f"context.md missing block {block_id}")
        for row in block.get("rows") or []:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or "")
            if label and label not in markdown:
                errors.append(f"context.md missing row {label}")
                break
            row_key = row.get("row_key")
            if row_key and not _in_markdown(str(row_key), markdown):
                errors.append(f"context.md missing row_key {row_key}")
                break
            concept = row.get("concept_id")
            if concept and str(concept) not in markdown:
                errors.append(f"context.md missing concept {concept}")
                break
            formula = row.get("formula")
            if formula and str(formula) not in markdown:
                errors.append(f"context.md missing formula {formula}")
                break
    return errors


def check_graph_markdown(graph: dict[str, Any], markdown: str) -> list[str]:
    errors: list[str] = []
    for key in ("nodes", "edges"):
        if key in graph and str(graph[key]) not in markdown:
            errors.append(f"graph.md missing {key} count {graph[key]}")
    for link in graph.get("links") or []:
        if not isinstance(link, dict):
            continue
        cell = str(link.get("cell") or "")
        if cell and cell not in markdown:
            errors.append(f"graph.md missing link {cell}")
            break
        formula = link.get("formula")
        if formula and str(formula) not in markdown:
            errors.append(f"graph.md missing formula {formula}")
            break
        row_key = link.get("row_key")
        if row_key and not _in_markdown(str(row_key), markdown):
            errors.append(f"graph.md missing row_key {row_key}")
            break
        period_id = link.get("period_id")
        if period_id and str(period_id) not in markdown:
            errors.append(f"graph.md missing period {period_id}")
            break
    return errors


def check_trace_markdown(trace: dict[str, Any], markdown: str) -> list[str]:
    errors: list[str] = []
    origin = str(trace.get("origin") or "")
    if origin and origin not in markdown:
        errors.append(f"trace.md missing origin {origin}")
    for node in trace.get("nodes") or []:
        if isinstance(node, dict) and node.get("node_id") and str(node["node_id"]) not in markdown:
            errors.append(f"trace.md missing node {node['node_id']}")
            break
    for edge in trace.get("edges") or []:
        if isinstance(edge, dict) and edge.get("target") and str(edge["target"]) not in markdown:
            errors.append(f"trace.md missing edge target {edge['target']}")
            break
    if "formula_ast" in markdown:
        errors.append("trace.md must not include formula AST")
    return errors


def pick_origin(context: dict[str, Any], graph: dict[str, Any] | None = None) -> str:
    for link in (graph or {}).get("links") or []:
        if isinstance(link, dict) and link.get("cell") and link.get("formula"):
            return str(link["cell"])
    for block in context.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for row in block.get("rows") or []:
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
        errors.append("trace.json missing nodes list")
    if not isinstance(trace.get("edges"), list):
        errors.append("trace.json missing edges list")
    if _walk_keys(trace) & {"formula_ast"}:
        errors.append("trace.json must not embed formula AST")
    return errors


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check that context and graph JSON match their Markdown twins."
    )
    parser.add_argument("context", type=Path, nargs="?", help="path to context.json")
    parser.add_argument("graph", type=Path, nargs="?", help="path to graph.json")
    parser.add_argument("--context-md", type=Path, help="path to context.md")
    parser.add_argument("--graph-md", type=Path, help="path to graph.md")
    parser.add_argument(
        "--print-origin",
        action="store_true",
        help="print a smoke-trace origin (formula cell, else concept_id / row_key)",
    )
    parser.add_argument("--trace", type=Path, help="path to trace.json")
    parser.add_argument("--trace-md", type=Path, help="path to trace.md")
    parser.add_argument("--origin", help="expected trace origin (required with --trace)")
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
        if args.trace_md is not None:
            try:
                markdown = args.trace_md.read_text(encoding="utf-8")
            except OSError as exc:
                parser.error(str(exc))
            errors.extend(check_trace_markdown(trace, markdown))
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
        errors.extend(check_link_identity(context, graph))
        if args.context_md is not None:
            try:
                markdown = args.context_md.read_text(encoding="utf-8")
                errors.extend(check_context_markdown(context, markdown))
            except OSError as exc:
                parser.error(str(exc))
        if args.graph_md is not None:
            try:
                markdown = args.graph_md.read_text(encoding="utf-8")
                errors.extend(check_graph_markdown(graph, markdown))
            except OSError as exc:
                parser.error(str(exc))
        if args.print_origin and not errors:
            print(pick_origin(context, graph))
            return 0

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
