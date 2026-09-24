#!/usr/bin/env python3
"""Validate context.json and graph.json, and that Markdown repeats them."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
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
GRAPH_SCHEMA_PREFIX = "1.7"
CONTEXT_SCHEMA_PREFIX = "1.13"
LINK_FIELDS = ("cell", "formula", "refs", "formula_class")
ROW_SERIES_FIELDS = (
    "value_statuses",
    "normalized_values",
    "scale_factor",
    "period_position",
    "aggregation",
)
VALUE_STATUSES = frozenset({"cached", "empty", "zero_explicit", "not_applicable"})
FORMULA_CLASSES = frozenset(
    {
        "same_period",
        "cross_period",
        "aggregation",
        "rollforward",
        "conditional",
        "hardcoded",
    }
)
MERGED_CONTRACT = ("formulas", "concepts", "series", "audit_trail", "source_workbook")


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
    schema = str(context.get("schema_version") or "")
    if schema and not schema.startswith(CONTEXT_SCHEMA_PREFIX):
        errors.append(
            f"context.json schema_version must be {CONTEXT_SCHEMA_PREFIX}.x, got {schema!r}"
        )
    merged = [key for key in MERGED_CONTRACT if key in context]
    if merged:
        errors.append(
            "context.json must stay a row document, not a merged cell contract: "
            + ", ".join(merged)
        )
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
            errors.extend(_check_row_series(row))
            if errors:
                break
    errors.extend(check_axes(context))
    return errors


def check_axes(context: dict[str, Any]) -> list[str]:
    """Axis periods are unique, ordered, contiguous in time, and never a total column."""
    errors: list[str] = []
    axes = [axis for axis in context.get("axes") or [] if isinstance(axis, dict)]
    totals: dict[tuple[str, str | None], set[int]] = {}
    for block in context.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        sheet = block.get("sheet")
        sheet_key = sheet if isinstance(sheet, str) and sheet else None
        cols = {
            int(cell["col"])
            for row in block.get("rows") or []
            if isinstance(row, dict)
            for cell in row.get("cells") or []
            if isinstance(cell, dict) and cell.get("role") == "total" and "col" in cell
        }
        for axis_id in block.get("axis_ids") or []:
            totals.setdefault((str(axis_id), sheet_key), set()).update(cols)
    for axis in axes:
        axis_id = str(axis.get("id") or "")
        axis_sheet = axis.get("sheet")
        axis_sheet = axis_sheet if isinstance(axis_sheet, str) and axis_sheet else None
        periods = [p for p in axis.get("periods") or [] if isinstance(p, dict)]
        keys = [str(p.get("period_key")) for p in periods]
        if len(set(keys)) != len(keys):
            errors.append(f"context.json axis {axis_id} repeats a period key")
        total_cols: set[int] = set()
        for (owner_id, sheet_key), cols in totals.items():
            if owner_id != axis_id:
                continue
            if sheet_key is not None and axis_sheet is not None and sheet_key != axis_sheet:
                continue
            total_cols.update(cols)
        overlap = {int(p["col"]) for p in periods if "col" in p} & total_cols
        if overlap:
            errors.append(f"context.json axis {axis_id} publishes a total column as a period")
        previous_end: date | None = None
        for period in periods:
            start = _iso(period.get("start_date"))
            end = _iso(period.get("end_date"))
            key = period.get("period_key")
            if start and end and start > end:
                errors.append(f"context.json axis {axis_id} period {key} ends before it starts")
                break
            if start and previous_end and start != previous_end + timedelta(days=1):
                errors.append(f"context.json axis {axis_id} has a gap before {key}")
                break
            previous_end = end
    return errors


def _iso(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _check_row_series(row: dict[str, Any]) -> list[str]:
    values = row.get("values")
    if not isinstance(values, list):
        return ["context.json row values must be a list"]
    if any(isinstance(item, dict) for item in values):
        return ["context.json values must be a flat list aligned to the block axis"]
    missing = [key for key in ROW_SERIES_FIELDS if key not in row]
    if missing:
        return ["context.json row missing " + ", ".join(missing)]
    statuses = row.get("value_statuses")
    normalized = row.get("normalized_values")
    if not isinstance(statuses, list) or len(statuses) != len(values):
        return ["context.json value_statuses must align with values"]
    if any(item not in VALUE_STATUSES for item in statuses):
        return ["context.json value_statuses has an unknown status"]
    if not isinstance(normalized, list) or len(normalized) != len(values):
        return ["context.json normalized_values must align with values"]
    factor = row.get("scale_factor")
    if factor is not None and not isinstance(factor, int):
        return ["context.json scale_factor must be an int or null"]
    return []


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
            formula_class = link.get("formula_class")
            if formula_class is not None and formula_class not in FORMULA_CLASSES:
                errors.append(f"graph.json link formula_class {formula_class!r} is unknown")
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


def _timeline_groups(context: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for axis in context.get("axes") or []:
        if not isinstance(axis, dict) or not axis.get("id"):
            continue
        key = str(axis.get("timeline_id") or axis["id"])
        groups.setdefault(key, []).append(axis)
    return groups


def _check_axes_markdown(context: dict[str, Any], markdown: str) -> list[str]:
    for members in _timeline_groups(context).values():
        if len(members) < 2:
            errors = _axis_period_grid(members[0], markdown)
            if errors:
                return errors
            continue
        key = str(members[0].get("timeline_id") or members[0].get("id"))
        owner = next((item for item in members if str(item.get("id")) == key), members[0])
        errors = _shared_axis_table(members, owner, markdown)
        if errors:
            return errors
    return []


def _shared_axis_table(
    members: list[dict[str, Any]], owner: dict[str, Any], markdown: str
) -> list[str]:
    periods = [p for p in owner.get("periods") or [] if isinstance(p, dict)]
    keys = [str(p.get("period_key")) for p in periods if p.get("period_key")]
    header = f"\n| Axis | {keys[0]} |" if keys else "\n| Axis |"
    if header not in markdown:
        return ["context.md shared axes must be one table headed by the period keys"]
    for axis in members:
        axis_id = str(axis["id"]).replace("|", "\\|")
        if f"\n| {axis_id} |" not in markdown:
            return [f"context.md axis {axis['id']} missing from the timeline table"]
    for attribute, key in (("Start", "start_date"), ("End", "end_date")):
        if any(p.get(key) for p in periods) and f"\n| {attribute} |" not in markdown:
            return [f"context.md axis {owner['id']} missing {attribute} row"]
    return []


def _axis_period_grid(axis: dict[str, Any], markdown: str) -> list[str]:
    axis_id = str(axis["id"]).replace("|", "\\|")
    periods = [p for p in axis.get("periods") or [] if isinstance(p, dict)]
    keys = [str(p.get("period_key")) for p in periods if p.get("period_key")]
    header = f"\n| {axis_id} | {keys[0]} |" if keys else f"\n| {axis_id} |"
    if header not in markdown:
        return [f"context.md axis {axis['id']} must head a table with periods as columns"]
    for attribute, key in (("Start", "start_date"), ("End", "end_date")):
        if any(p.get(key) for p in periods) and f"\n| {attribute} |" not in markdown:
            return [f"context.md axis {axis['id']} missing {attribute} row"]
    return []


def check_context_markdown(context: dict[str, Any], markdown: str) -> list[str]:
    errors: list[str] = _check_axes_markdown(context, markdown)
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
            errors.extend(_check_row_markdown(row, markdown))
            errors.extend(_check_period_cell_addresses(context, block, row, markdown))
            if errors:
                break
    return errors


def _column_letter(col: object) -> str:
    if isinstance(col, bool) or not isinstance(col, int) or col < 1:
        return ""
    n = col
    chars: list[str] = []
    while n:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(65 + rem))
    return "".join(reversed(chars))


def _block_periods(context: dict[str, Any], block: dict[str, Any]) -> list[dict[str, Any]]:
    """Periods the block table prints. Role cells are not periods.

    A shared axis keeps the canonical sheet's columns. Another sheet stores its
    own columns on ``block.periods``.
    """
    local = [item for item in block.get("periods") or [] if isinstance(item, dict)]
    if local:
        return local
    axis_ids = [str(item) for item in block.get("axis_ids") or [] if item]
    if axis_ids:
        by_id = {
            str(axis.get("id")): axis
            for axis in context.get("axes") or []
            if isinstance(axis, dict) and axis.get("id")
        }
        periods: list[dict[str, Any]] = []
        for axis_id in axis_ids:
            axis = by_id.get(axis_id)
            if axis is None:
                continue
            periods.extend(item for item in axis.get("periods") or [] if isinstance(item, dict))
        return periods
    return [item for item in block.get("periods") or [] if isinstance(item, dict)]


def _check_period_cell_addresses(
    context: dict[str, Any],
    block: dict[str, Any],
    row: dict[str, Any],
    markdown: str,
) -> list[str]:
    """A period value must carry `[Sheet!A1]`. The formula stays in graph.md."""
    sheet = row.get("sheet")
    number = row.get("row")
    if (
        not isinstance(sheet, str)
        or not sheet
        or isinstance(number, bool)
        or not isinstance(number, int)
        or number < 1
    ):
        return []
    for period in _block_periods(context, block):
        letter = _column_letter(period.get("col"))
        if not letter:
            continue
        addr = f"{sheet}!{letter}{number}"
        if f"[{addr}]" not in markdown:
            return [f"context.md period value missing [{addr}]"]
    return []


def _check_row_markdown(row: dict[str, Any], markdown: str) -> list[str]:
    statuses = row.get("value_statuses") or []
    if isinstance(statuses, list) and "empty" in statuses and "empty" not in markdown:
        return ["context.md missing empty value status"]
    if isinstance(statuses, list) and "not_applicable" in statuses and "n/a" not in markdown:
        return ["context.md missing n/a value status"]
    position = row.get("period_position")
    aggregation = row.get("aggregation")
    if position and aggregation and f"{position}/{aggregation}" not in markdown:
        return [f"context.md missing time profile {position}/{aggregation}"]
    factor = row.get("scale_factor")
    if isinstance(factor, int) and factor not in (0, 1) and f"×{factor}" not in markdown:
        return [f"context.md missing scale factor ×{factor}"]
    values = row.get("values") or []
    normalized = row.get("normalized_values") or []
    if isinstance(values, list) and isinstance(normalized, list):
        for value, norm in zip(values, normalized, strict=False):
            if value and norm and _scaled(str(value), str(norm)) and str(norm) not in markdown:
                return [f"context.md missing normalized value {norm}"]
    for cell in row.get("cells") or []:
        if not isinstance(cell, dict) or cell.get("role") == "unit":
            continue
        value = cell.get("cached_value")
        if value not in (None, "") and not _in_markdown(str(value), markdown):
            return [f"context.md missing {cell.get('role')} cell {cell.get('addr')}"]
    return []


def _scaled(value: str, normalized: str) -> bool:
    """True when the base-unit amount is a different number, not only a different spelling."""
    try:
        left = float(value.replace(",", ""))
        right = float(normalized.replace(",", ""))
    except ValueError:
        return value != normalized
    return abs(left - right) > 1e-9 * max(1.0, abs(left), abs(right))


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
        formula_class = link.get("formula_class")
        if formula_class and str(formula_class) not in markdown:
            errors.append(f"graph.md missing formula class {formula_class}")
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


def axes_summary(context: dict[str, Any]) -> str:
    parts: list[str] = []
    for axis in context.get("axes") or []:
        if not isinstance(axis, dict):
            continue
        periods = [p for p in axis.get("periods") or [] if isinstance(p, dict)]
        keys = [str(p.get("period_key")) for p in periods]
        span = f"{keys[0]}..{keys[-1]}" if keys else "-"
        construction = sum(1 for p in periods if p.get("phase") == "construction")
        operation = sum(1 for p in periods if p.get("phase") == "operation")
        parts.append(
            f"axis={axis.get('id')} periods={len(periods)} span={span} "
            f"construction={construction} operation={operation}"
        )
    blocks = [block for block in context.get("blocks") or [] if isinstance(block, dict)]
    params = sum(1 for block in blocks if block.get("kind") == "params")
    head = f"axes={len(parts)} blocks={len(blocks)} params_blocks={params}"
    return "; ".join([head, *parts])


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
    parser.add_argument(
        "--axes-summary",
        action="store_true",
        help="print one line per axis: periods and construction/operation counts",
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
        if args.axes_summary and not errors:
            print(axes_summary(context))
            return 0

    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
