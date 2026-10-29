from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path

from finance_context.formulas.csr import SheetUsage, _span_has, present_count, whole_axis
from finance_context.graph.models import TraceDocument, TraceEdge, TraceNode
from finance_context.graph.ranges import CellGrid, CoverIndex, range_nodes
from finance_context.graph.refs import parse_node_id
from finance_context.labels import own_label_contains
from finance_context.settings import DEFAULT_TRACE_RANGE_MEMBERS
from finance_context.store.fs import read_parquet

_FILLED = {"calculated", "input"}


def trace_graph(
    dest_dir: Path,
    *,
    origin: str,
    direction: str = "precedents",
    depth: int = 8,
    book_dir: Path | None = None,
    range_members: int = DEFAULT_TRACE_RANGE_MEMBERS,
) -> TraceDocument:
    """Walk precedents or dependents.

    A range node is passed through, never visited. Its members are the filled
    cells inside its bounds, row then column. ``range_members`` caps how many of
    them a precedents walk follows (0 follows all); the edge then says how many
    there are and how many were followed. A dependents walk finds the formulas
    reading a cell through every range node whose bounds contain it.
    """
    if direction not in {"precedents", "dependents"}:
        direction = "precedents"
    depth = max(1, min(int(depth), 32))
    book = book_dir or dest_dir
    cells = {f"{c['sheet']}!{c['addr']}": c for c in read_parquet(book / "ir" / "cells.parquet")}
    index_path = dest_dir / "graph" / "index.parquet"
    if index_path.is_file():
        index = {row["node_id"]: row for row in read_parquet(index_path)}
    else:
        index = {}
    edges_path = dest_dir / "graph" / "edges.parquet"
    if not edges_path.is_file():
        # Enriched edges are written with the graph. A job that only kept the
        # formula cell edges still has the precedents (PF Model!AA172 → Z172).
        edges_path = book / "ir" / "cell_edges.parquet"
    edges = read_parquet(edges_path) if edges_path.is_file() else []

    fwd: dict[str, list[dict]] = defaultdict(list)
    rev: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        if edge.get("unresolved") or edge.get("dangling") or not edge.get("target"):
            continue
        src, tgt = str(edge["source"]), str(edge["target"])
        fwd[src].append(edge)
        rev[tgt].append(edge)
    nodes_by_id = range_nodes(rev)
    ranges = _Ranges(nodes_by_id, index, book) if nodes_by_id else None

    starts = _resolve_origin(origin, index, context_path=dest_dir / "context" / "context.json")
    if not starts and origin.strip() in cells:
        starts = [origin.strip()]
    if not starts and ranges is not None and ranges.covers(origin.strip()):
        # A blank cell inside a range: what reads it is still a fair question.
        starts = [origin.strip()]
    if not starts:
        return TraceDocument(
            origin=origin,
            direction=direction,  # type: ignore[arg-type]
            depth=depth,
            stopped="empty",
        )

    limit = max(0, int(range_members))
    seen: set[str] = set()
    nodes: list[TraceNode] = []
    used_edges: list[TraceEdge] = []
    queue: deque[tuple[str, int]] = deque((node, 0) for node in starts)
    stopped: str | None = None
    hit_depth = False
    while queue:
        current, level = queue.popleft()
        if current in seen:
            stopped = "cycle"
            continue
        seen.add(current)
        meta = index.get(current) or (ranges.blank_meta(current) if ranges is not None else {})
        nodes.append(_node(current, cells.get(current, {}), meta, level))
        if direction == "precedents" and meta.get("node_type") == "input" and level > 0:
            stopped = "input"
            continue
        if direction == "precedents":
            outgoing = fwd[current]
            readers: list[tuple[dict, str]] = []
        else:
            outgoing = rev[current]
            readers = ranges.readers(current, rev) if ranges is not None else []
        if level >= depth:
            if outgoing or readers:
                hit_depth = True
            continue
        emitted: set[tuple[str, str]] = set()

        for edge in outgoing:
            range_ref = edge.get("range_ref")
            target = str(range_ref) if range_ref else str(edge["target"])
            if direction == "precedents":
                node = nodes_by_id.get(str(edge["target"]))
                if node is not None and ranges is not None:
                    if not ranges.has_cells(node):
                        # An expansion of a whole column with no cells had no
                        # edge to report. A bounded block always has cells.
                        continue
                    members = ranges.members(node)
                    shown = members if limit == 0 else members[:limit]
                    _emit(
                        used_edges,
                        emitted,
                        edge,
                        target,
                        members_total=len(members),
                        members_shown=len(shown),
                    )
                    for member in shown:
                        if member in seen:
                            stopped = "cycle"
                            continue
                        queue.append((member, level + 1))
                    continue
                nxt = str(edge["target"])
            else:
                nxt = str(edge["source"])
            _emit(used_edges, emitted, edge, target)
            if index.get(nxt, {}).get("node_type") == "empty":
                continue
            if nxt in seen:
                stopped = "cycle"
                continue
            queue.append((nxt, level + 1))

        for edge, reader in readers:
            range_ref = edge.get("range_ref")
            offset, lag = ranges.offset_and_lag(reader, current)  # type: ignore[union-attr]
            _emit(
                used_edges,
                emitted,
                edge,
                str(range_ref or edge["target"]),
                period_lag=lag,
                col_offset=offset,
            )
            if reader in seen:
                stopped = "cycle"
                continue
            queue.append((reader, level + 1))

    if stopped is None:
        stopped = "depth" if hit_depth else ("leaf" if not used_edges else "complete")
    return TraceDocument(
        origin=origin,
        direction=direction,  # type: ignore[arg-type]
        depth=depth,
        stopped=stopped,
        nodes=nodes,
        edges=_unique_edges(used_edges),
    )


def _emit(
    used_edges: list[TraceEdge],
    emitted: set[tuple[str, str]],
    edge: dict,
    target: str,
    **extra: object,
) -> None:
    key = (str(edge["source"]), target)
    if key in emitted:
        return
    emitted.add(key)
    used_edges.append(
        TraceEdge(
            source=str(edge["source"]),
            target=target,
            kind=edge.get("kind"),
            period_lag=extra.get("period_lag", edge.get("period_lag")),  # type: ignore[arg-type]
            col_offset=extra.get("col_offset", edge.get("col_offset")),  # type: ignore[arg-type]
            members_total=extra.get("members_total"),  # type: ignore[arg-type]
            members_shown=extra.get("members_shown"),  # type: ignore[arg-type]
        )
    )


class _Ranges:
    """Range nodes of one book for a trace: members, covering nodes, blank cells."""

    def __init__(self, nodes_by_id: dict, index: dict[str, dict], book: Path) -> None:
        self._index = index
        self._book = book
        self._present: SheetUsage | None = None
        self._grid = CellGrid.from_cells(
            node_id for node_id, row in index.items() if row.get("node_type") in _FILLED
        )
        self._cover = CoverIndex(nodes_by_id.values())
        self._members: dict[str, list[str]] = {}
        self._period_by_col: dict[tuple[str, int], str] = {}
        self._row_key_by_row: dict[tuple[str, int], str] = {}
        for node_id, row in index.items():
            parsed = parse_node_id(node_id)
            if parsed is None:
                continue
            sheet, col, row_n = parsed
            if row.get("period_id"):
                self._period_by_col.setdefault((sheet, col), str(row["period_id"]))
            if row.get("row_key"):
                self._row_key_by_row.setdefault((sheet, row_n), str(row["row_key"]))

    def members(self, node) -> list[str]:
        found = self._members.get(node.node_id)
        if found is None:
            found = self._grid.members(node)
            self._members[node.node_id] = found
        return found

    def covering(self, cell: str):
        parsed = parse_node_id(cell)
        if parsed is None or ":" in cell:
            return []
        sheet, col, row = parsed
        found = []
        for node in self._cover.covering(sheet, col, row):
            # A whole column or row holds only the cells that exist, as its
            # expansion did; a bounded rectangle holds its blanks too.
            if whole_axis(node.c1, node.c2, node.r1, node.r2) and not self._exists(sheet, col, row):
                continue
            found.append(node)
        return found

    def _usage(self) -> SheetUsage:
        if self._present is None:
            from finance_context.memory import usage_for_book

            self._present = usage_for_book(self._book)
        return self._present

    def has_cells(self, node) -> bool:
        if not whole_axis(node.c1, node.c2, node.r1, node.r2):
            return True
        return present_count(node, self._usage()) > 0

    def _exists(self, sheet: str, col: int, row: int) -> bool:
        spans = self._usage().rows_by_col.get(sheet, {}).get(col, ())
        return _span_has(spans, row)

    def covers(self, cell: str) -> bool:
        return bool(self.covering(cell))

    def readers(self, cell: str, rev: dict[str, list[dict]]) -> list[tuple[dict, str]]:
        found: list[tuple[dict, str]] = []
        for node in self.covering(cell):
            for edge in rev.get(node.node_id, ()):
                found.append((edge, str(edge["source"])))
        return found

    def period(self, cell: str) -> str | None:
        row = self._index.get(cell)
        if row is not None:
            return row.get("period_id")
        parsed = parse_node_id(cell)
        if parsed is None:
            return None
        return self._period_by_col.get((parsed[0], parsed[1]))

    def offset_and_lag(self, reader: str, cell: str) -> tuple[int | None, str]:
        """The offset and lag of the reader -> cell edge a per-formula expansion had."""
        from finance_context.graph.stage import calendar_lag

        src = parse_node_id(reader)
        tgt = parse_node_id(cell)
        offset = None if src is None or tgt is None else tgt[1] - src[1]
        src_period = self.period(reader)
        tgt_period = self.period(cell)
        if not src_period or not tgt_period:
            return offset, "unaligned"
        return offset, calendar_lag(str(src_period), str(tgt_period))

    def blank_meta(self, cell: str) -> dict:
        parsed = parse_node_id(cell)
        if parsed is None or not self.covers(cell):
            return {}
        sheet, col, row = parsed
        return {
            "sheet": sheet,
            "addr": cell.rsplit("!", 1)[1],
            "row_key": self._row_key_by_row.get((sheet, row)),
            "period_id": self._period_by_col.get((sheet, col)),
            "node_type": "empty",
        }


def _resolve_origin(origin: str, index: dict[str, dict], *, context_path: Path) -> list[str]:
    text = origin.strip()
    if not text:
        return []
    if text in index:
        return [text]
    if "|" in text:
        return sorted(nid for nid, row in index.items() if row.get("row_key") == text)
    keys = _row_keys_for_label(context_path, text)
    if not keys:
        return []
    return sorted(nid for nid, row in index.items() if row.get("row_key") in keys)


def _row_keys_for_label(path: Path, needle: str) -> set[str]:
    """Row keys whose own label contains needle. A bad context file matches nothing."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    if not isinstance(loaded, dict):
        return set()
    blocks = loaded.get("blocks")
    if not isinstance(blocks, list):
        return set()
    keys: set[str] = set()
    for block in blocks:
        if not isinstance(block, dict):
            continue
        rows = block.get("rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = row.get("label")
            key = row.get("row_key")
            if not isinstance(label, str) or not isinstance(key, str) or not key:
                continue
            if own_label_contains(label, needle):
                keys.add(key)
    return keys


def _node(node_id: str, cell: dict, meta: dict, depth: int) -> TraceNode:
    sheet = meta.get("sheet") or cell.get("sheet")
    addr = meta.get("addr") or cell.get("addr")
    cached = cell.get("cached_value")
    return TraceNode(
        node_id=node_id,
        sheet=None if sheet is None else str(sheet),
        addr=None if addr is None else str(addr),
        row_key=meta.get("row_key"),
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
