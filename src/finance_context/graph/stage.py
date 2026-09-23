from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from finance_context.excel.a1 import index_to_col
from finance_context.formulas.stage import IR_CELL_EDGE_COLUMNS
from finance_context.graph.cycles import classify_cycles
from finance_context.graph.formula_class import classify_formula
from finance_context.graph.models import (
    GRAPH_SCHEMA_VERSION,
    ID_CAP,
    CircularityHint,
    CycleRecord,
    FormulaLink,
    GraphContract,
    GraphDocument,
    IdCount,
)
from finance_context.graph.refs import parse_node_id
from finance_context.layout.models import Layout
from finance_context.layout.resolve import period_headers
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
        dest_dir / "ir" / "graph_edges.parquet",
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
    doc.links = _formula_links(cells, edges, row_meta, period_by_cell, enriched)
    write_json(dest_dir / "graph.json", doc.model_dump(mode="json", by_alias=True))
    from finance_context.render.graph import render_graph_markdown

    markdown = render_graph_markdown(doc)
    tmp = dest_dir / "graph.md.tmp"
    tmp.write_text(markdown, encoding="utf-8")
    tmp.replace(dest_dir / "graph.md")
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
            for pos, header in enumerate(period_headers(sheet, block)):
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


def _lags_by_source(cell_edges: list[dict]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for edge in cell_edges:
        source = str(edge.get("source") or "")
        lag = edge.get("period_lag")
        if not source or lag is None:
            continue
        bucket = grouped.setdefault(source, [])
        text = str(lag)
        if text not in bucket:
            bucket.append(text)
    return grouped


def _formula_links(
    cells: list[dict],
    formula_edges: list[dict],
    row_meta: dict[tuple[str, int], tuple[str, str | None]],
    period_by_cell: dict[tuple[str, int], str],
    cell_edges: list[dict] | None = None,
) -> list[FormulaLink]:
    formula_by_node: dict[str, str] = {}
    identity: dict[str, tuple[str | None, str | None]] = {}
    for cell in cells:
        raw = cell.get("formula_raw")
        if not raw:
            continue
        node = f"{cell['sheet']}!{cell['addr']}"
        formula_by_node[node] = str(raw)
        meta = row_meta.get((str(cell["sheet"]), int(cell["row"])))
        period = period_by_cell.get((str(cell["sheet"]), int(cell["col"])))
        identity[node] = (meta[0] if meta else None, period)
    grouped: dict[str, list[str]] = {}
    for edge in formula_edges:
        source = str(edge.get("source") or "")
        if not source:
            continue
        refs = grouped.setdefault(source, [])
        target = edge.get("target")
        if target and str(target) not in refs:
            refs.append(str(target))
    for node in formula_by_node:
        grouped.setdefault(node, [])
    lags = _lags_by_source(cell_edges or [])
    links: list[FormulaLink] = []
    for cell, refs in sorted(grouped.items()):
        row_key, period_id = identity.get(cell, (None, None))
        formula = formula_by_node.get(cell)
        links.append(
            FormulaLink(
                cell=cell,
                formula=formula,
                formula_class=classify_formula(formula, refs, lags.get(cell)),
                refs=refs,
                row_key=row_key,
                period_id=period_id,
            )
        )
    return links


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
