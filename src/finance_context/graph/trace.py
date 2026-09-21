from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from finance_context.graph.models import TraceDocument, TraceEdge, TraceNode
from finance_context.store.fs import read_parquet


def trace_graph(
    dest_dir: Path,
    *,
    origin: str,
    direction: str = "precedents",
    depth: int = 8,
) -> TraceDocument:
    if direction not in {"precedents", "dependents"}:
        direction = "precedents"
    depth = max(1, min(int(depth), 32))
    cells = {
        f"{c['sheet']}!{c['addr']}": c
        for c in read_parquet(dest_dir / "ir" / "cells.parquet")
    }
    index_path = dest_dir / "ir" / "graph_index.parquet"
    if index_path.is_file():
        index = {row["node_id"]: row for row in read_parquet(index_path)}
    else:
        index = {}
    edges_path = dest_dir / "ir" / "cell_edges.parquet"
    edges = read_parquet(edges_path) if edges_path.is_file() else []

    fwd: dict[str, list[dict]] = defaultdict(list)
    rev: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        if edge.get("unresolved") or edge.get("dangling") or not edge.get("target"):
            continue
        src, tgt = str(edge["source"]), str(edge["target"])
        fwd[src].append(edge)
        rev[tgt].append(edge)

    starts = _resolve_origin(origin, index)
    if not starts and origin.strip() in cells:
        starts = [origin.strip()]
    if not starts:
        return TraceDocument(
            origin=origin,
            direction=direction,  # type: ignore[arg-type]
            depth=depth,
            stopped="empty",
        )

    seen: set[str] = set()
    nodes: list[TraceNode] = []
    used_edges: list[TraceEdge] = []
    queue: deque[tuple[str, int]] = deque((node, 0) for node in starts)
    stopped = "depth"
    while queue:
        current, level = queue.popleft()
        if current in seen:
            stopped = "cycle"
            continue
        seen.add(current)
        meta = index.get(current, {})
        nodes.append(_node(current, cells.get(current, {}), meta, level))
        if direction == "precedents" and meta.get("node_type") == "input" and level > 0:
            stopped = "input"
            continue
        if level >= depth:
            continue
        outgoing = fwd[current] if direction == "precedents" else rev[current]
        emitted: set[tuple[str, str]] = set()
        for edge in outgoing:
            nxt = str(edge["target"] if direction == "precedents" else edge["source"])
            if index.get(nxt, {}).get("node_type") == "empty":
                range_ref = edge.get("range_ref")
                target = str(range_ref) if range_ref else nxt
                key = (str(edge["source"]), target)
                if key not in emitted:
                    emitted.add(key)
                    used_edges.append(
                        TraceEdge(
                            source=str(edge["source"]),
                            target=target,
                            kind=edge.get("kind"),
                            period_lag=edge.get("period_lag"),
                            col_offset=edge.get("col_offset"),
                        )
                    )
                continue
            range_ref = edge.get("range_ref")
            target = str(range_ref) if range_ref else str(edge["target"])
            key = (str(edge["source"]), target)
            if key not in emitted:
                emitted.add(key)
                used_edges.append(
                    TraceEdge(
                        source=str(edge["source"]),
                        target=target,
                        kind=edge.get("kind"),
                        period_lag=edge.get("period_lag"),
                        col_offset=edge.get("col_offset"),
                    )
                )
            if nxt in seen:
                stopped = "cycle"
                continue
            queue.append((nxt, level + 1))

    return TraceDocument(
        origin=origin,
        direction=direction,  # type: ignore[arg-type]
        depth=depth,
        stopped=stopped,
        nodes=nodes,
        edges=_unique_edges(used_edges),
    )


def _resolve_origin(origin: str, index: dict[str, dict]) -> list[str]:
    text = origin.strip()
    if not text:
        return []
    if text in index:
        return [text]
    if "|" in text:
        return sorted(nid for nid, row in index.items() if row.get("row_key") == text)
    hits = sorted(nid for nid, row in index.items() if row.get("concept_id") == text)
    return hits


def _node(node_id: str, cell: dict, meta: dict, depth: int) -> TraceNode:
    sheet = meta.get("sheet") or cell.get("sheet")
    addr = meta.get("addr") or cell.get("addr")
    cached = cell.get("cached_value")
    return TraceNode(
        node_id=node_id,
        sheet=None if sheet is None else str(sheet),
        addr=None if addr is None else str(addr),
        row_key=meta.get("row_key"),
        concept_id=meta.get("concept_id"),
        period_id=meta.get("period_id"),
        node_type=meta.get("node_type"),
        formula=cell.get("formula_raw"),
        formula_template=cell.get("formula_template"),
        cached_value=None if cached is None else str(cached),
        depth=depth,
    )


def _unique_edges(edges: list[TraceEdge]) -> list[TraceEdge]:
    seen: set[tuple[str, str]] = set()
    out: list[TraceEdge] = []
    for edge in edges:
        key = (edge.source, edge.target)
        if key in seen:
            continue
        seen.add(key)
        out.append(edge)
    return out
