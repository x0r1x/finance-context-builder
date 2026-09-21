from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from finance_context.excel.a1 import index_to_col
from finance_context.formulas.stage import IR_CELL_EDGE_COLUMNS
from finance_context.graph.cycles import classify_cycles
from finance_context.graph.models import (
    GRAPH_SCHEMA_VERSION,
    ID_CAP,
    CircularityHint,
    CycleRecord,
    GraphContract,
    GraphDocument,
    IdCount,
)
from finance_context.graph.refs import parse_node_id
from finance_context.layout.models import Layout
from finance_context.mapping.models import MappingDocument
from finance_context.models.context import GraphPointer
from finance_context.store.fs import read_parquet, write_json, write_parquet

INDEX_COLUMNS = (
    ("node_id", "VARCHAR"),
    ("sheet", "VARCHAR"),
    ("addr", "VARCHAR"),
    ("row_key", "VARCHAR"),
    ("concept_id", "VARCHAR"),
    ("period_id", "VARCHAR"),
    ("node_type", "VARCHAR"),
)


def build_formula_graph(
    dest_dir: Path,
    *,
    job_id: str,
    layout: Layout,
    mapping: MappingDocument,
) -> GraphPointer:
    cells = read_parquet(dest_dir / "ir" / "cells.parquet")
    edges = read_parquet(dest_dir / "ir" / "edges.parquet") if (
        dest_dir / "ir" / "edges.parquet"
    ).is_file() else []
    cell_edges_path = dest_dir / "ir" / "cell_edges.parquet"
    cell_edges = read_parquet(cell_edges_path) if cell_edges_path.is_file() else []

    row_meta = _row_meta(layout, mapping)
    period_by_cell, period_index = _period_maps(layout)
    index_rows = _index_rows(cells, row_meta, period_by_cell)
    empty_nodes = _empty_range_nodes(cell_edges, {row[0] for row in index_rows})
    index_rows.extend(
        _empty_index_row(node_id, row_meta, period_by_cell) for node_id in empty_nodes
    )
    write_parquet(dest_dir / "ir" / "graph_index.parquet", INDEX_COLUMNS, index_rows)

    period_of = {row[0]: row[5] for row in index_rows}
    enriched = [_enrich_edge(edge, period_of, period_index) for edge in cell_edges]
    write_parquet(
        dest_dir / "ir" / "cell_edges.parquet",
        IR_CELL_EDGE_COLUMNS,
        [
            (
                e["source"],
                e["target"],
                e.get("kind"),
                bool(e.get("unresolved")),
                bool(e.get("truncated")),
                bool(e.get("dangling")),
                e.get("col_offset"),
                e.get("period_lag"),
                e.get("dangling_reason"),
                e.get("status"),
                e.get("reason"),
                e.get("evidence"),
                e.get("range_ref"),
                _opt_bool(e.get("abs_col")),
                _opt_bool(e.get("abs_row")),
                _opt_bool(e.get("abs_col_end")),
                _opt_bool(e.get("abs_row_end")),
                bool(e.get("named")),
            )
            for e in enriched
        ],
    )

    cycles = attach_cycle_breakers(classify_cycles(enriched), mapping)
    hints = circularity_hints(mapping, layout, index_rows, cycles)
    iterate = _workbook_iterate(dest_dir)
    doc = _summary(job_id, len(index_rows), edges, enriched, cycles, iterate, hints)
    write_json(dest_dir / "graph.json", doc.model_dump(mode="json", by_alias=True))
    _write_audit_sidecars(dest_dir, cells, enriched, period_of)
    unexpected = sum(1 for c in cycles if c.class_ == "unexpected")
    iterative = sum(1 for c in cycles if c.class_ == "iterative_ok")
    classes = doc.dangling_classes
    return GraphPointer(
        artifact="graph.json",
        cell_edges="ir/cell_edges.parquet",
        index="ir/graph_index.parquet",
        nodes=doc.nodes,
        edges=doc.edges,
        cycles_unexpected=unexpected,
        cycles_iterative=iterative,
        iterate=iterate,
        unresolved=doc.unresolved.count,
        dangling=doc.dangling.count,
        empty_range_members=int(classes.get("empty_range_member") or 0),
    )


def _workbook_iterate(dest_dir: Path) -> bool:
    path = dest_dir / "raw" / "workbook.json"
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(payload.get("iterate")) if isinstance(payload, dict) else False


def _cells_by_row(index_rows: list[tuple[object, ...]]) -> dict[tuple[str, int], list[str]]:
    out: dict[tuple[str, int], list[str]] = {}
    for row in index_rows:
        node_id = str(row[0])
        parsed = parse_node_id(node_id)
        if parsed is None:
            continue
        key = (parsed[0], parsed[2])
        bucket = out.setdefault(key, [])
        if node_id not in bucket:
            bucket.append(node_id)
    return out


def attach_cycle_breakers(
    cycles: list[CycleRecord],
    mapping: MappingDocument,
) -> list[CycleRecord]:
    if not cycles:
        return cycles
    bridge_rows = {
        (row.sheet, row.row)
        for row in mapping.rows
        if row.exclusion_reason == "technical_bridge"
    }
    if not bridge_rows:
        return cycles
    annotated: list[CycleRecord] = []
    for cycle in cycles:
        breakers: list[str] = []
        for member in cycle.members:
            parsed = parse_node_id(member)
            if parsed is None:
                continue
            if (parsed[0], parsed[2]) in bridge_rows:
                breakers.append(member)
        annotated.append(cycle.model_copy(update={"breakers": sorted(breakers)}))
    return annotated


def circularity_hints(
    mapping: MappingDocument,
    layout: Layout,
    index_rows: list[tuple[object, ...]],
    cycles: list[CycleRecord],
) -> list[CircularityHint]:
    if cycles:
        return []
    cells = _cells_by_row(index_rows)
    hints: list[CircularityHint] = []
    seen: set[tuple[str, int]] = set()

    def add(sheet: str, row: int, label: str) -> None:
        key = (sheet, row)
        if key in seen:
            return
        seen.add(key)
        hints.append(
            CircularityHint(
                sheet=sheet,
                row=row,
                label=label,
                cell_ids=sorted(cells.get(key, [])),
            )
        )

    for row in mapping.rows:
        blob = (row.label or "").casefold()
        if row.exclusion_reason == "technical_bridge" or "circular" in blob:
            add(row.sheet, row.row, row.label)
    for sheet in layout.sheets:
        for block in sheet.blocks:
            for row in block.rows:
                if "circular" in (row.label or "").casefold():
                    add(sheet.name, row.row, row.label)
    return hints


def _row_meta(
    layout: Layout, mapping: MappingDocument
) -> dict[tuple[str, int], tuple[str, str | None]]:
    concepts = {(row.sheet, row.row): row.concept_id for row in mapping.rows}
    out: dict[tuple[str, int], tuple[str, str | None]] = {}
    for sheet in layout.sheets:
        for block in sheet.blocks:
            for row in block.rows:
                key = (sheet.name, row.row)
                row_key = f"{sheet.name}|{row.row}|{block.block_id}"
                out[key] = (row_key, concepts.get(key) or concepts.get((sheet.name, row.row)))
    return out


def _period_maps(layout: Layout) -> tuple[dict[tuple[str, int], str], dict[str, int]]:
    period_by_cell: dict[tuple[str, int], str] = {}
    index: dict[str, int] = {}
    seq = 0
    for sheet in layout.sheets:
        for block in sheet.blocks:
            local: dict[str, int] = {}
            for pos, header in enumerate(block.axis.headers):
                period_by_cell[(sheet.name, header.col)] = header.period_key
                if header.period_key not in local:
                    local[header.period_key] = pos
                if header.period_key not in index:
                    index[header.period_key] = seq
                    seq += 1
    return period_by_cell, index


def _index_rows(
    cells: list[dict],
    row_meta: dict[tuple[str, int], tuple[str, str | None]],
    period_by_cell: dict[tuple[str, int], str],
) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for cell in cells:
        sheet = str(cell["sheet"])
        addr = str(cell["addr"])
        row_n = int(cell["row"])
        col_n = int(cell["col"])
        meta = row_meta.get((sheet, row_n))
        formula = cell.get("formula_raw")
        cached = cell.get("cached_value")
        if formula:
            node_type = "calculated"
        elif cached not in (None, ""):
            node_type = "input"
        else:
            node_type = "empty"
        rows.append(
            (
                f"{sheet}!{addr}",
                sheet,
                addr,
                meta[0] if meta else None,
                meta[1] if meta else None,
                period_by_cell.get((sheet, col_n)),
                node_type,
            )
        )
    return rows


def _empty_range_nodes(cell_edges: list[dict], existing: set[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set(existing)
    for edge in cell_edges:
        if str(edge.get("status") or "") != "empty":
            continue
        target = str(edge.get("target") or "")
        if not target or target in seen:
            continue
        seen.add(target)
        found.append(target)
    return found


def _empty_index_row(
    node_id: str,
    row_meta: dict[tuple[str, int], tuple[str, str | None]],
    period_by_cell: dict[tuple[str, int], str],
) -> tuple[object, ...]:
    parsed = parse_node_id(node_id)
    if parsed is None:
        return (node_id, None, None, None, None, None, "empty")
    sheet, col_n, row_n = parsed
    addr = f"{index_to_col(col_n)}{row_n}"
    meta = row_meta.get((sheet, row_n))
    return (
        node_id,
        sheet,
        addr,
        meta[0] if meta else None,
        meta[1] if meta else None,
        period_by_cell.get((sheet, col_n)),
        "empty",
    )


def _opt_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _endpoint(node_id: str, period_of: dict[str, str | None]) -> dict[str, object]:
    parsed = parse_node_id(node_id)
    if parsed is None:
        return {
            "sheet": None,
            "address": None,
            "period_id": period_of.get(node_id),
            "node_id": node_id,
        }
    sheet, col_n, row_n = parsed
    return {
        "sheet": sheet,
        "address": f"{index_to_col(col_n)}{row_n}",
        "period_id": period_of.get(node_id),
        "node_id": node_id,
    }


def _reference_kind(edge: dict) -> str:
    kind = str(edge.get("kind") or "")
    if kind == "external":
        return "external"
    if kind == "dynamic":
        return "dynamic"
    if edge.get("named"):
        return "named"
    if kind == "range":
        return "range_member"
    return "direct"


def _resolution_status(edge: dict) -> str:
    kind = str(edge.get("kind") or "")
    if kind == "dynamic":
        return "dynamic"
    if kind == "external":
        return "external"
    status = str(edge.get("status") or "")
    if status == "empty":
        return "empty"
    if edge.get("dangling") or edge.get("unresolved") or status == "unresolved":
        return "unresolved"
    if edge.get("truncated"):
        return "truncated"
    return "resolved"


def _anchors(edge: dict) -> dict[str, bool | None]:
    anchors: dict[str, bool | None] = {
        "abs_col": _opt_bool(edge.get("abs_col")),
        "abs_row": _opt_bool(edge.get("abs_row")),
    }
    if (
        str(edge.get("kind") or "") == "range"
        or edge.get("abs_col_end") is not None
        or edge.get("abs_row_end") is not None
    ):
        anchors["abs_col_end"] = _opt_bool(edge.get("abs_col_end"))
        anchors["abs_row_end"] = _opt_bool(edge.get("abs_row_end"))
    return anchors


def _edge_id(source: str, target: str, kind: str, range_ref: str) -> str:
    payload = f"{source}\0{target}\0{kind}\0{range_ref}".encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _audit_edge(
    edge: dict,
    period_of: dict[str, str | None],
    formula_by_node: dict[str, str],
) -> dict[str, object]:
    source = str(edge.get("source") or "")
    target = str(edge.get("target") or "")
    kind = str(edge.get("kind") or "")
    range_ref = edge.get("range_ref")
    return {
        "edge_id": _edge_id(source, target, kind, str(range_ref or "")),
        "direction": "formula_depends_on_precedent",
        "formula_cell": _endpoint(source, period_of),
        "precedent": _endpoint(target, period_of),
        "source": source,
        "target": target,
        "relation_type": "formula_reference",
        "reference_kind": _reference_kind(edge),
        "anchors": _anchors(edge),
        "formula": formula_by_node.get(source),
        "resolution_status": _resolution_status(edge),
        "kind": kind or None,
        "dangling": bool(edge.get("dangling")),
        "dangling_reason": edge.get("dangling_reason"),
        "status": edge.get("status"),
        "reason": edge.get("reason"),
        "evidence": edge.get("evidence"),
        "range_ref": range_ref,
        "period_lag": edge.get("period_lag"),
        "col_offset": edge.get("col_offset"),
    }


def _write_audit_sidecars(
    dest_dir: Path,
    cells: list[dict],
    cell_edges: list[dict],
    period_of: dict[str, str | None],
) -> None:
    formula_cells = []
    formula_by_node: dict[str, str] = {}
    for cell in cells:
        raw = cell.get("formula_raw")
        if not raw:
            continue
        node_id = f"{cell['sheet']}!{cell['addr']}"
        formula_by_node[node_id] = str(raw)
        ast = cell.get("ast_json")
        if isinstance(ast, str) and ast:
            try:
                ast = json.loads(ast)
            except json.JSONDecodeError:
                pass
        else:
            ast = None
        formula_cells.append(
            {
                "node_id": node_id,
                "formula": raw,
                "formula_template": cell.get("formula_template"),
                "formula_ast": ast,
            }
        )
    write_json(dest_dir / "formulas.json", {"cells": formula_cells})
    write_json(
        dest_dir / "graph-edges.json",
        {
            "direction": "formula_depends_on_precedent",
            "edges": [
                _audit_edge(edge, period_of, formula_by_node) for edge in cell_edges
            ],
        },
    )
    items: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    by_class: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    sources: dict[tuple[str, str], list[dict[str, str]]] = {}
    source_seen: dict[tuple[str, str], set[tuple[str, str]]] = {}
    for edge in cell_edges:
        klass = str(edge.get("dangling_reason") or "")
        target = str(edge.get("target") or "")
        status = str(edge.get("status") or "")
        if not klass or not target or not status:
            continue
        key = (target, klass)
        bucket = sources.setdefault(key, [])
        seen_sources = source_seen.setdefault(key, set())
        source = str(edge.get("source") or "")
        range_ref = str(edge.get("range_ref") or "")
        pair = (source, range_ref)
        if source and pair not in seen_sources:
            seen_sources.add(pair)
            bucket.append({"node_id": source, "range": range_ref})
        if key in seen:
            continue
        seen.add(key)
        by_class[klass] += 1
        by_status[status] += 1
        included: bool | str = True if status == "empty" else "unknown"
        items.append(
            {
                "node_id": target,
                "period_id": period_of.get(target),
                "status": status,
                "reason": edge.get("reason"),
                "evidence": edge.get("evidence"),
                "included_in_formula_semantics": included,
                "class": klass,
                "sources": bucket,
            }
        )
    write_json(
        dest_dir / "graph-dangling.json",
        {
            "count": len(items),
            "by_class": dict(by_class),
            "by_status": dict(by_status),
            "ids": items,
        },
    )


def _enrich_edge(
    edge: dict, period_of: dict[str, str | None], period_index: dict[str, int]
) -> dict:
    source = str(edge.get("source") or "")
    target = str(edge.get("target") or "")
    src = parse_node_id(source)
    tgt = parse_node_id(target)
    col_offset = None
    if src is not None and tgt is not None:
        col_offset = tgt[1] - src[1]
    src_p = period_of.get(source)
    tgt_p = period_of.get(target)
    if not src_p or not tgt_p:
        lag = "unaligned"
    elif src_p == tgt_p:
        lag = "same"
    elif src_p in period_index and tgt_p in period_index:
        lag = str(period_index[tgt_p] - period_index[src_p])
    else:
        lag = "unaligned"
    out = dict(edge)
    out["col_offset"] = col_offset
    out["period_lag"] = lag
    return out


def _summary(
    job_id: str,
    node_count: int,
    formula_edges: list[dict],
    cell_edges: list[dict],
    cycles,
    iterate: bool = False,
    hints: list[CircularityHint] | None = None,
) -> GraphDocument:
    kinds = Counter(str(e.get("kind") or "ref") for e in cell_edges)

    def bucket(pred) -> IdCount:
        ids: list[str] = []
        for edge in formula_edges:
            if pred(edge) and edge.get("source"):
                src = str(edge["source"])
                if src not in ids:
                    ids.append(src)
        return IdCount(count=len(ids), ids=ids[:ID_CAP])

    dangling_ids: list[str] = []
    classes: Counter[str] = Counter()
    seen_class: set[tuple[str, str]] = set()
    for edge in cell_edges:
        reason = str(edge.get("dangling_reason") or "")
        target = str(edge.get("target") or "")
        if reason and target and (target, reason) not in seen_class:
            seen_class.add((target, reason))
            classes[reason] += 1
        if edge.get("dangling") and target:
            if target not in dangling_ids:
                dangling_ids.append(target)
    truncated_ids: list[str] = []
    for edge in cell_edges:
        if edge.get("truncated") and edge.get("source"):
            src = str(edge["source"])
            if src not in truncated_ids:
                truncated_ids.append(src)
    return GraphDocument(
        schema_version=GRAPH_SCHEMA_VERSION,
        job_id=job_id,
        nodes=node_count,
        edges=len(cell_edges),
        kinds=dict(kinds),
        contract=GraphContract(),
        unresolved=bucket(
            lambda e: e.get("unresolved") and e.get("kind") not in {"dynamic", "external"}
        ),
        dynamic=bucket(lambda e: e.get("kind") == "dynamic"),
        external=bucket(lambda e: e.get("kind") == "external"),
        truncated=IdCount(count=len(truncated_ids), ids=truncated_ids[:ID_CAP]),
        dangling=IdCount(count=len(dangling_ids), ids=dangling_ids[:ID_CAP]),
        dangling_classes=dict(classes),
        cycles=cycles,
        iterate=iterate,
        circularity_hints=list(hints or []),
    )
