from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from finance_context.excel.a1 import index_to_col
from finance_context.formulas.stage import IR_CELL_EDGE_COLUMNS
from finance_context.graph.cycles import classify_cycles
from finance_context.graph.models import GRAPH_SCHEMA_VERSION, ID_CAP, GraphContract, GraphDocument, IdCount
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
            )
            for e in enriched
        ],
    )

    cycles = classify_cycles(enriched)
    doc = _summary(job_id, len(index_rows), edges, enriched, cycles)
    write_json(dest_dir / "graph.json", doc.model_dump(mode="json", by_alias=True))
    _write_audit_sidecars(dest_dir, cells, enriched)
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
        unresolved=doc.unresolved.count,
        dangling=doc.dangling.count,
        empty_range_members=int(classes.get("empty_range_member") or 0),
    )


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
        if str(edge.get("dangling_reason") or "") != "empty_range_member":
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


def _write_audit_sidecars(dest_dir: Path, cells: list[dict], cell_edges: list[dict]) -> None:
    formula_cells = []
    for cell in cells:
        raw = cell.get("formula_raw")
        if not raw:
            continue
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
                "node_id": f"{cell['sheet']}!{cell['addr']}",
                "formula": raw,
                "formula_template": cell.get("formula_template"),
                "formula_ast": ast,
            }
        )
    write_json(dest_dir / "formulas.json", {"cells": formula_cells})
    write_json(
        dest_dir / "graph-edges.json",
        {
            "edges": [
                {
                    "source": e.get("source"),
                    "target": e.get("target"),
                    "kind": e.get("kind"),
                    "dangling": bool(e.get("dangling")),
                    "dangling_reason": e.get("dangling_reason"),
                    "period_lag": e.get("period_lag"),
                    "col_offset": e.get("col_offset"),
                }
                for e in cell_edges
            ]
        },
    )
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    by_class: Counter[str] = Counter()
    for edge in cell_edges:
        reason = str(edge.get("dangling_reason") or "")
        target = str(edge.get("target") or "")
        if not reason or not target:
            continue
        key = (target, reason)
        if key in seen:
            continue
        seen.add(key)
        by_class[reason] += 1
        items.append({"node_id": target, "class": reason})
    write_json(
        dest_dir / "graph-dangling.json",
        {
            "count": len(items),
            "by_class": dict(by_class),
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
    )
