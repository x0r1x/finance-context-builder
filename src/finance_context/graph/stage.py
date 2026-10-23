from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from finance_context.excel.a1 import index_to_col
from finance_context.formulas.csr import (
    CellEdge,
    RangeNode,
    as_cell_edge,
    is_range_node,
    parse_range_node,
)
from finance_context.formulas.models import Edge, as_edge
from finance_context.formulas.stage import IR_CELL_EDGE_COLUMNS, EdgeCount
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
from finance_context.graph.ranges import CellGrid, RangeHops, hop_flags, is_cell_id
from finance_context.graph.refs import parse_node_id
from finance_context.layout.models import Layout
from finance_context.layout.periods import period_position
from finance_context.layout.resolve import period_headers
from finance_context.models.context import GraphPointer
from finance_context.settings import Settings
from finance_context.store.fs import (
    atomic_write_bytes,
    read_parquet,
    temporary_path,
    write_json,
    write_parquet,
)

INDEX_COLUMNS = (
    ("node_id", "VARCHAR"),
    ("sheet", "VARCHAR"),
    ("addr", "VARCHAR"),
    ("row_key", "VARCHAR"),
    ("period_id", "VARCHAR"),
    ("node_type", "VARCHAR"),
)


def build_formula_graph(
    dest_dir: Path,
    *,
    job_id: str,
    layout: Layout,
    cells: list[dict] | None = None,
    edges: list | None = None,
    cell_edges: list | None = None,
    book_dir: Path | None = None,
    settings: Settings | None = None,
) -> GraphPointer:
    chosen = settings if isinstance(settings, Settings) else Settings()
    book = book_dir or dest_dir
    cell_edge_path = book / "ir" / "cell_edges.parquet"
    stream = (
        chosen.graph_stream
        and cell_edge_path.is_file()
        and (cell_edges is None or isinstance(cell_edges, EdgeCount))
    )
    if cells is None:
        cells = read_parquet(book / "ir" / "cells.parquet")
    if edges is None:
        edges_path = book / "ir" / "edges.parquet"
        edges = read_parquet(edges_path) if edges_path.is_file() else []
    edges = [as_edge(edge) for edge in edges]
    if not stream:
        if cell_edges is None or isinstance(cell_edges, EdgeCount):
            cell_edges = read_parquet(cell_edge_path) if cell_edge_path.is_file() else []
        cell_edges = [as_cell_edge(edge) for edge in cell_edges]

    row_meta = _row_meta(layout)
    period_by_cell, _period_index = _period_maps(layout)
    index_rows = _index_rows(cells, row_meta, period_by_cell)
    if stream:
        empty_nodes = _empty_nodes_from_parquet(cell_edge_path, {row[0] for row in index_rows})
    else:
        empty_nodes = _empty_range_nodes(cell_edges, {row[0] for row in index_rows})
    index_rows.extend(
        _empty_index_row(node_id, row_meta, period_by_cell) for node_id in empty_nodes
    )
    write_parquet(
        dest_dir / "ir" / "graph_index.parquet",
        INDEX_COLUMNS,
        index_rows,
        row_group_rows=chosen.parquet_row_group_rows,
    )

    if stream:
        cycles, doc, iterate = _graph_from_parquet(
            dest_dir,
            book,
            job_id,
            layout,
            cells,
            edges,
            index_rows,
            row_meta,
            period_by_cell,
            cell_edge_path,
            stream_json=chosen.json_stream,
        )
    else:
        period_of = {row[0]: row[4] for row in index_rows}
        view = _RangeView(book, cells, period_by_cell)
        enriched = [_enrich_edge(edge, period_of, view) for edge in cell_edges]
        write_parquet(
            dest_dir / "ir" / "graph_edges.parquet",
            IR_CELL_EDGE_COLUMNS,
            [
                (
                    edge.source,
                    edge.target,
                    edge.kind,
                    bool(edge.unresolved),
                    bool(edge.truncated),
                    bool(edge.dangling),
                    edge.col_offset,
                    edge.period_lag,
                    edge.dangling_reason,
                    edge.status,
                    edge.reason,
                    edge.evidence,
                    edge.range_ref,
                    _opt_bool(edge.abs_col),
                    _opt_bool(edge.abs_row),
                    _opt_bool(edge.abs_col_end),
                    _opt_bool(edge.abs_row_end),
                    bool(edge.named),
                )
                for edge in enriched
            ],
            row_group_rows=chosen.parquet_row_group_rows,
        )

        users = view.users(enriched)
        cycles = classify_cycles(
            enriched,
            hops=view.hops() if users else None,
            range_edges=users,
            period_chain=lambda source, member: _member_chain(source, member, period_of),
        )
        hints = circularity_hints(layout, index_rows, cycles)
        iterate = _workbook_iterate(book)
        doc = _summary(job_id, len(index_rows), edges, enriched, cycles, iterate, hints)
        lags = _lags_by_source(enriched)
        _add_range_lags(lags, users, view, period_of)
        doc.links = _formula_links(cells, edges, row_meta, period_by_cell, lags=lags)
        if chosen.json_stream:
            _stream_graph_files(dest_dir, doc)
        else:
            write_json(
                dest_dir / "graph.json",
                doc.model_dump(mode="json", by_alias=True),
                stream=False,
            )
            from finance_context.render.graph import render_graph_markdown

            markdown = render_graph_markdown(doc)
            atomic_write_bytes(dest_dir / "graph.md", markdown.encode("utf-8"))
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


def circularity_hints(
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

    for sheet in layout.sheets:
        for block in sheet.blocks:
            for row in block.rows:
                if "circular" in (row.label or "").casefold():
                    add(sheet.name, row.row, row.label)
    return hints


def _row_meta(layout: Layout) -> dict[tuple[str, int], str]:
    out: dict[tuple[str, int], str] = {}
    for sheet in layout.sheets:
        for block in sheet.blocks:
            for row in block.rows:
                out[(sheet.name, row.row)] = f"{sheet.name}|{row.row}|{block.block_id}"
    return out


def calendar_key(period_id: str) -> str:
    """Lag slot for a period id. ``2024@факт`` and ``2024`` share ``2024``.

    The part before ``@`` is the calendar key. The marker stays in the period id
    so two columns of one year do not collapse in the index.
    """
    head, sep, _marker = period_id.partition("@")
    return head if sep else period_id


def calendar_lag(source_id: str, target_id: str) -> str:
    """Distance inside one grain. Equal valid period keys are ``same``.

    Calendar grains use the calendar index. Ordinal keys ``Y`` / ``Q`` / ``M`` /
    ``P`` use the number after the letter. Another grain, or a key that is not
    a period, is ``unaligned``, including two copies of that key.
    ``classify_formula`` skips that, so it is not a prior step.
    """
    source = calendar_key(source_id)
    target = calendar_key(target_id)
    src = period_position(source)
    tgt = period_position(target)
    if src is None or tgt is None or src[0] != tgt[0]:
        return "unaligned"
    if source == target:
        return "same"
    return str(tgt[1] - src[1])


def _period_maps(layout: Layout) -> tuple[dict[tuple[str, int], str], dict[str, int]]:
    """Cell period ids stay distinct. Variant ids of one period key share its index.

    The index is the absolute position from ``period_position``, comparable
    only inside that grain. A key that is not a period is omitted.
    """
    period_by_cell: dict[tuple[str, int], str] = {}
    index: dict[str, int] = {}
    for sheet in layout.sheets:
        for block in sheet.blocks:
            for header in period_headers(sheet, block):
                period_by_cell[(sheet.name, header.col)] = header.period_key
                position = period_position(calendar_key(header.period_key))
                if position is not None:
                    index[header.period_key] = position[1]
    return period_by_cell, index


def _index_rows(
    cells: list[dict],
    row_meta: dict[tuple[str, int], str],
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
                meta,
                period_by_cell.get((sheet, col_n)),
                node_type,
            )
        )
    return rows


def _empty_range_nodes(cell_edges: list[CellEdge], existing: set[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set(existing)
    for edge in cell_edges:
        if str(edge.status or "") != "empty":
            continue
        target = str(edge.target or "")
        if not target or target in seen:
            continue
        seen.add(target)
        found.append(target)
    return found


def _empty_index_row(
    node_id: str,
    row_meta: dict[tuple[str, int], str],
    period_by_cell: dict[tuple[str, int], str],
) -> tuple[object, ...]:
    parsed = parse_node_id(node_id)
    if parsed is None:
        return (node_id, None, None, None, None, "empty")
    sheet, col_n, row_n = parsed
    addr = f"{index_to_col(col_n)}{row_n}"
    meta = row_meta.get((sheet, row_n))
    return (
        node_id,
        sheet,
        addr,
        meta,
        period_by_cell.get((sheet, col_n)),
        "empty",
    )


def _opt_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _lags_by_source(cell_edges: list[CellEdge]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for edge in cell_edges:
        source = str(edge.source or "")
        lag = edge.period_lag
        if not source or lag is None:
            continue
        bucket = grouped.setdefault(source, [])
        text = str(lag)
        if text not in bucket:
            bucket.append(text)
    return grouped


def _formula_links(
    cells: list[dict],
    formula_edges: list[Edge],
    row_meta: dict[tuple[str, int], str],
    period_by_cell: dict[tuple[str, int], str],
    cell_edges: list[CellEdge] | None = None,
    lags: dict[str, list[str]] | None = None,
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
        identity[node] = (meta, period)
    grouped: dict[str, list[str]] = {}
    for edge in formula_edges:
        source = str(edge.source or "")
        if not source:
            continue
        refs = grouped.setdefault(source, [])
        target = edge.target
        if target and str(target) not in refs:
            refs.append(str(target))
    for node in formula_by_node:
        grouped.setdefault(node, [])
    if lags is None:
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


class _RangeView:
    """Range nodes as the graph stage sees them, built only when a book has one.

    A node's cells are not in the edge files. Its first member (for the edge's
    offset and lag), its member columns (for formula_class) and its formula
    cells (for cycles) come from its bounds and the book's cells.
    """

    def __init__(self, book: Path, cells: list[dict], period_by_cell: dict) -> None:
        self._book = book
        self._cells = cells
        self._period_by_cell = period_by_cell
        self._nodes: dict[str, RangeNode | None] = {}
        self._grid: CellGrid | None = None
        self._hops: RangeHops | None = None
        self._periods: dict[str, list[str | None]] = {}

    def node(self, target: str) -> RangeNode | None:
        if not target or not is_range_node(target):
            return None
        if target not in self._nodes:
            self._nodes[target] = parse_range_node(target)
        return self._nodes[target]

    def users(self, cell_edges: list[CellEdge]) -> list[tuple[str, RangeNode]]:
        found: list[tuple[str, RangeNode]] = []
        for edge in cell_edges:
            node = self.node(str(edge.target or ""))
            if node is not None and edge.source:
                found.append((str(edge.source), node))
        return found

    def grid(self) -> CellGrid:
        if self._grid is None:
            from finance_context.memory import usage_for_book

            self._grid = CellGrid(usage_for_book(self._book))
        return self._grid

    def hops(self) -> RangeHops:
        if self._hops is None:
            self._hops = RangeHops(
                f"{cell['sheet']}!{cell['addr']}" for cell in self._cells if cell.get("formula_raw")
            )
        return self._hops

    def offset_and_lag(
        self, source: str, node: RangeNode, period_of: dict
    ) -> tuple[int | None, str]:
        """Offset and lag to the node's first member, the edge a per-formula
        expansion would have listed first for this range."""
        first = self.grid().first(node)
        if first is None:
            return None, "unaligned"
        col, _row = first
        src = parse_node_id(source)
        offset = None if src is None else col - src[1]
        src_period = period_of.get(source)
        member_period = self._period_by_cell.get((node.sheet, col))
        if not src_period or not member_period:
            return offset, "unaligned"
        return offset, calendar_lag(str(src_period), str(member_period))

    def member_periods(self, node: RangeNode) -> list[str | None]:
        cached = self._periods.get(node.node_id)
        if cached is None:
            periods = {
                self._period_by_cell.get((node.sheet, col))
                for col in self.grid().member_columns(node)
            }
            cached = sorted(periods, key=lambda value: (value is None, value or ""))
            self._periods[node.node_id] = cached
        return cached


def _add_range_lags(
    lags: dict[str, list[str]],
    users: list[tuple[str, RangeNode]],
    view: _RangeView,
    period_of: dict,
) -> None:
    """Give a formula the lags it would have had with one edge per range member.

    ``formula_class`` reads these. A member's period is its column's period, so
    the lags come from the columns the range covers.
    """
    for source, node in users:
        member_periods = view.member_periods(node)
        if not member_periods:
            continue
        bucket = lags.setdefault(source, [])
        src_period = period_of.get(source)
        for member_period in member_periods:
            if not src_period or not member_period:
                lag = "unaligned"
            else:
                lag = calendar_lag(str(src_period), str(member_period))
            if lag not in bucket:
                bucket.append(lag)


def _member_chain(source: str, member: str, period_of: dict) -> bool:
    offset, lag = _offset_and_lag(source, member, period_of)
    return _period_chain(source, member, lag, offset)


def _enrich_edge(
    edge: CellEdge, period_of: dict[str, str | None], view: _RangeView | None = None
) -> CellEdge:
    source = str(edge.source or "")
    target = str(edge.target or "")
    node = view.node(target) if view is not None else None
    if node is not None:
        offset, lag = view.offset_and_lag(source, node, period_of)  # type: ignore[union-attr]
        return edge._replace(col_offset=offset, period_lag=lag)
    src = parse_node_id(source)
    tgt = parse_node_id(target)
    col_offset = None
    if src is not None and tgt is not None:
        col_offset = tgt[1] - src[1]
    src_p = period_of.get(source)
    tgt_p = period_of.get(target)
    if not src_p or not tgt_p:
        lag = "unaligned"
    else:
        lag = calendar_lag(src_p, tgt_p)
    return edge._replace(col_offset=col_offset, period_lag=lag)


def _summary(
    job_id: str,
    node_count: int,
    formula_edges: list[Edge],
    cell_edges: list[CellEdge],
    cycles,
    iterate: bool = False,
    hints: list[CircularityHint] | None = None,
) -> GraphDocument:
    kinds = Counter(str(edge.kind or "ref") for edge in cell_edges)

    def bucket(pred) -> IdCount:
        ids: list[str] = []
        for edge in formula_edges:
            if pred(edge) and edge.source:
                src = str(edge.source)
                if src not in ids:
                    ids.append(src)
        return IdCount(count=len(ids), ids=ids[:ID_CAP])

    dangling_ids: list[str] = []
    classes: Counter[str] = Counter()
    seen_class: set[tuple[str, str]] = set()
    for edge in cell_edges:
        reason = str(edge.dangling_reason or "")
        target = str(edge.target or "")
        if reason and target and (target, reason) not in seen_class:
            seen_class.add((target, reason))
            classes[reason] += 1
        if edge.dangling and target:
            if target not in dangling_ids:
                dangling_ids.append(target)
    truncated_ids: list[str] = []
    for edge in cell_edges:
        if edge.truncated and edge.source:
            src = str(edge.source)
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
            lambda edge: edge.unresolved and edge.kind not in {"dynamic", "external"}
        ),
        dynamic=bucket(lambda edge: edge.kind == "dynamic"),
        external=bucket(lambda edge: edge.kind == "external"),
        truncated=IdCount(count=len(truncated_ids), ids=truncated_ids[:ID_CAP]),
        dangling=IdCount(count=len(dangling_ids), ids=dangling_ids[:ID_CAP]),
        dangling_classes=dict(classes),
        cycles=cycles,
        iterate=iterate,
        circularity_hints=list(hints or []),
    )


_LINKS_SLOT = "__FC_LINKS_SLOT__"
_USABLE_KINDS = {"ref", "cross_sheet", "range"}
# Flag of an edge that only connects a range node to its cells for the SCC pass.
_STRUCTURAL = 2
_BOOL_COLUMNS = {"unresolved", "truncated", "dangling", "named"}
_ABS_COLUMNS = {"abs_col", "abs_row", "abs_col_end", "abs_row_end"}


def _empty_nodes_from_parquet(path: Path, existing: set[str]) -> list[str]:
    """Status and target columns only, in file order. Same result as _empty_range_nodes."""
    import pyarrow.parquet as pq

    found: list[str] = []
    seen: set[str] = set(existing)
    parquet = pq.ParquetFile(path)
    for group in range(parquet.num_row_groups):
        table = parquet.read_row_group(group, columns=["status", "target"])
        statuses = table.column("status").to_pylist()
        targets = table.column("target").to_pylist()
        del table
        for status, target in zip(statuses, targets, strict=True):
            if str(status or "") != "empty":
                continue
            text = str(target or "")
            if not text or text in seen:
                continue
            seen.add(text)
            found.append(text)
        del statuses, targets
    return found


def _offset_and_lag(source: str, target: str, period_of: dict) -> tuple[int | None, str]:
    src = parse_node_id(source)
    tgt = parse_node_id(target)
    col_offset = None
    if src is not None and tgt is not None:
        col_offset = tgt[1] - src[1]
    src_period = period_of.get(source)
    tgt_period = period_of.get(target)
    if not src_period or not tgt_period:
        return col_offset, "unaligned"
    return col_offset, calendar_lag(str(src_period), str(tgt_period))


def _period_chain(source: str, target: str, lag: str, offset: int | None) -> bool:
    if lag in {"same", "0", "unaligned"}:
        return False
    if lag not in (None, ""):
        try:
            return int(lag) != 0
        except (TypeError, ValueError):
            return False
    if offset in (None, 0):
        return False
    src = parse_node_id(source)
    tgt = parse_node_id(target)
    return src is not None and tgt is not None and src[0] == tgt[0] and src[2] == tgt[2]


class _NodeIds:
    __slots__ = ("map",)

    def __init__(self) -> None:
        self.map: dict[str, int] = {}

    def nid(self, text: str) -> int:
        slot = self.map.get(text)
        if slot is None:
            slot = len(self.map)
            self.map[text] = slot
        return slot


def _cycles_packed(
    id_of: dict[str, int], src_ids, tgt_ids, flags, extra=None
) -> list[CycleRecord]:
    """Same records as classify_cycles, from integer endpoints and a period-chain bit.

    An edge flagged ``_STRUCTURAL`` links a range node to its cells. It joins
    components but is not a formula edge, so it does not vote on the class.
    ``extra(label_of)`` yields (component, bit, self loop) for the formula ->
    member edges a per-formula expansion would have had inside a component.
    """
    import numpy as np
    from scipy import sparse
    from scipy.sparse.csgraph import connected_components

    if not id_of:
        return []
    nodes = sorted(id_of)
    rank = {id_of[name]: index for index, name in enumerate(nodes)}
    n_edges = len(src_ids)
    rows = np.fromiter((rank[i] for i in src_ids), dtype=np.int32, count=n_edges)
    cols = np.fromiter((rank[i] for i in tgt_ids), dtype=np.int32, count=n_edges)
    data = np.ones(n_edges, dtype=np.uint8)
    matrix = sparse.csr_matrix((data, (rows, cols)), shape=(len(nodes), len(nodes)), dtype=np.uint8)
    _n_comp, labels = connected_components(matrix, directed=True, connection="strong")
    order: list[int] = []
    members: dict[int, list[str]] = {}
    for name, label_raw in zip(nodes, labels, strict=True):
        label = int(label_raw)
        bucket = members.get(label)
        if bucket is None:
            members[label] = [name]
            order.append(label)
        else:
            bucket.append(name)
    sizes = np.bincount(labels)
    all_period: dict[int, bool] = {}
    saw: set[int] = set()
    self_loop: set[int] = set()
    same = labels[rows] == labels[cols]
    for pos_raw in np.flatnonzero(same):
        pos = int(pos_raw)
        label = int(labels[rows[pos]])
        if sizes[label] < 2 and int(rows[pos]) != int(cols[pos]):
            continue
        if flags[pos] == _STRUCTURAL:
            continue
        saw.add(label)
        if int(rows[pos]) == int(cols[pos]):
            self_loop.add(label)
        bit = bool(flags[pos])
        previous = all_period.get(label)
        all_period[label] = bit if previous is None else previous and bit
    if extra is not None:

        def label_of(name: str) -> int | None:
            slot = id_of.get(name)
            return None if slot is None else int(labels[rank[slot]])

        for label, bit, is_self in extra(label_of):
            saw.add(label)
            if is_self:
                self_loop.add(label)
            previous = all_period.get(label)
            all_period[label] = bit if previous is None else previous and bit
    cycles: list[CycleRecord] = []
    seq = 1
    for label in order:
        group = members[label]
        if len(group) < 2 and label not in self_loop:
            continue
        # A range node and its tree segments are hops, not cells:
        # A100 = SUM(A:A) is the cycle [A100].
        cells_only = [name for name in group if is_cell_id(name)]
        if not cells_only or (len(cells_only) < 2 and label not in self_loop):
            continue
        cls = "iterative_ok" if label in saw and all_period.get(label, False) else "unexpected"
        cycles.append(
            CycleRecord.model_validate(
                {"id": f"c{seq}", "members": sorted(cells_only), "class": cls}
            )
        )
        seq += 1
    return cycles


def _edge_bucket(formula_edges, pred) -> IdCount:
    ids: list[str] = []
    for edge in formula_edges:
        if pred(edge) and edge.source:
            src = str(edge.source)
            if src not in ids:
                ids.append(src)
    return IdCount(count=len(ids), ids=ids[:ID_CAP])


def _graph_from_parquet(
    dest_dir,
    book,
    job_id,
    layout,
    cells,
    edges,
    index_rows,
    row_meta,
    period_by_cell,
    path: Path,
    *,
    stream_json: bool,
):
    """Enrich and classify from parquet row groups. The cell-edge list is not loaded."""
    import array

    import pyarrow.parquet as pq

    from finance_context.store.fs import RowGroupWriter

    period_of = {row[0]: row[4] for row in index_rows}
    kinds: Counter[str] = Counter()
    classes: Counter[str] = Counter()
    seen_class: set[tuple[str, str]] = set()
    dangling_ids: list[str] = []
    dangling_seen: set[str] = set()
    truncated_ids: list[str] = []
    truncated_seen: set[str] = set()
    lag_groups: dict[str, list[str]] = {}
    lag_pool: dict[str, str] = {}
    view = _RangeView(book, cells, period_by_cell)
    range_users: list[tuple[str, RangeNode]] = []
    ids = _NodeIds()
    src_ids = array.array("i")
    tgt_ids = array.array("i")
    flags = array.array("B")
    n_edges = 0
    names = [name for name, _dtype in IR_CELL_EDGE_COLUMNS]
    with RowGroupWriter(dest_dir / "ir" / "graph_edges.parquet", IR_CELL_EDGE_COLUMNS, 0) as writer:
        # One output group per source group. A single source group stays write_table.
        parquet = pq.ParquetFile(path)
        multi = parquet.num_row_groups > 1
        if multi:
            writer.begin_row_groups()
        for group in range(parquet.num_row_groups):
            table = parquet.read_row_group(group, columns=names)
            data = {name: table.column(name).to_pylist() for name in names}
            del table
            width = len(data["source"])
            offsets: list[int | None] = [None] * width
            lag_col: list[str] = [""] * width
            for index in range(width):
                source = str(data["source"][index] or "")
                target = str(data["target"][index] or "")
                node = view.node(target)
                if node is not None:
                    offset, lag = view.offset_and_lag(source, node, period_of)
                else:
                    offset, lag = _offset_and_lag(source, target, period_of)
                lag = lag_pool.setdefault(lag, lag)
                offsets[index] = offset
                lag_col[index] = lag
                kind = data["kind"][index]
                kinds[str(kind or "ref")] += 1
                n_edges += 1
                reason = str(data["dangling_reason"][index] or "")
                if reason and target and (target, reason) not in seen_class:
                    seen_class.add((target, reason))
                    classes[reason] += 1
                if data["dangling"][index] and target and target not in dangling_seen:
                    dangling_seen.add(target)
                    dangling_ids.append(target)
                if data["truncated"][index] and source and source not in truncated_seen:
                    truncated_seen.add(source)
                    truncated_ids.append(source)
                if source:
                    if node is not None:
                        range_users.append((source, node))
                    bucket = lag_groups.setdefault(source, [])
                    if lag not in bucket:
                        bucket.append(lag)
                if (
                    source
                    and target
                    and not data["unresolved"][index]
                    and not data["dangling"][index]
                    and kind in _USABLE_KINDS
                ):
                    src_ids.append(ids.nid(source))
                    tgt_ids.append(ids.nid(target))
                    if node is not None:
                        flags.append(_STRUCTURAL)
                    else:
                        flags.append(1 if _period_chain(source, target, lag, offset) else 0)
            out_cols = []
            for name in names:
                if name == "col_offset":
                    out_cols.append(offsets)
                elif name == "period_lag":
                    out_cols.append(lag_col)
                elif name in _BOOL_COLUMNS:
                    out_cols.append([bool(value) for value in data[name]])
                elif name in _ABS_COLUMNS:
                    out_cols.append([_opt_bool(value) for value in data[name]])
                else:
                    out_cols.append(data[name])
            writer.write_lists(out_cols)
            del data, offsets, lag_col, out_cols
    _add_range_lags(lag_groups, range_users, view, period_of)
    extra = None
    if range_users:
        hops = view.hops()
        for node in {node.node_id: node for _source, node in range_users}.values():
            for parent, child in hops.edges(node):
                src_ids.append(ids.nid(parent))
                tgt_ids.append(ids.nid(child))
                flags.append(_STRUCTURAL)

        def extra(label_of):
            return hop_flags(
                label_of,
                range_users,
                hops,
                lambda source, member: _member_chain(source, member, period_of),
            )

    cycles = _cycles_packed(ids.map, src_ids, tgt_ids, flags, extra)
    del ids, src_ids, tgt_ids, flags
    hints = circularity_hints(layout, index_rows, cycles)
    iterate = _workbook_iterate(book)
    doc = GraphDocument(
        schema_version=GRAPH_SCHEMA_VERSION,
        job_id=job_id,
        nodes=len(index_rows),
        edges=n_edges,
        kinds=dict(kinds),
        contract=GraphContract(),
        unresolved=_edge_bucket(
            edges, lambda edge: edge.unresolved and edge.kind not in {"dynamic", "external"}
        ),
        dynamic=_edge_bucket(edges, lambda edge: edge.kind == "dynamic"),
        external=_edge_bucket(edges, lambda edge: edge.kind == "external"),
        truncated=IdCount(count=len(truncated_ids), ids=truncated_ids[:ID_CAP]),
        dangling=IdCount(count=len(dangling_ids), ids=dangling_ids[:ID_CAP]),
        dangling_classes=dict(classes),
        cycles=cycles,
        iterate=iterate,
        circularity_hints=list(hints or []),
    )
    grouped, identity, formula_by_node = _link_inputs(cells, edges, row_meta, period_by_cell)
    if stream_json:
        _stream_graph_files(dest_dir, doc, grouped, identity, formula_by_node, lag_groups)
    else:
        doc.links = _formula_links(cells, edges, row_meta, period_by_cell, lags=lag_groups)
        write_json(
            dest_dir / "graph.json",
            doc.model_dump(mode="json", by_alias=True),
            stream=False,
        )
        from finance_context.render.graph import render_graph_markdown

        markdown = render_graph_markdown(doc)
        atomic_write_bytes(dest_dir / "graph.md", markdown.encode("utf-8"))
    return cycles, doc, iterate


def _link_inputs(cells, formula_edges, row_meta, period_by_cell):
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
        identity[node] = (meta, period)
    grouped: dict[str, list[str]] = {}
    for edge in formula_edges:
        source = str(edge.source or "")
        if not source:
            continue
        refs = grouped.setdefault(source, [])
        target = edge.target
        if target and str(target) not in refs:
            refs.append(str(target))
    for node in formula_by_node:
        grouped.setdefault(node, [])
    return grouped, identity, formula_by_node


def _stream_graph_files(
    dest_dir,
    doc: GraphDocument,
    grouped: dict[str, list[str]] | None = None,
    identity: dict | None = None,
    formula_by_node: dict | None = None,
    lags: dict | None = None,
) -> None:
    """Write graph.json with json.dump. Links are streamed when the caller kept them columnar."""
    from finance_context.render.graph import _cell, render_graph_markdown

    if grouped is None:
        payload = doc.model_dump(mode="json", by_alias=True)
        write_json(dest_dir / "graph.json", payload, stream=True)
        markdown = render_graph_markdown(doc)
        atomic_write_bytes(dest_dir / "graph.md", markdown.encode("utf-8"))
        return
    identity = identity or {}
    formula_by_node = formula_by_node or {}
    lags = lags or {}
    n_links = len(grouped)
    head = doc.model_dump(mode="json", by_alias=True)
    head["links"] = _LINKS_SLOT
    text = json.dumps(head, ensure_ascii=False, indent=2)
    slot = json.dumps(_LINKS_SLOT)
    prefix, suffix = text.split(slot, 1)
    md_head = render_graph_markdown(doc)
    md_head = md_head.replace("- Links: 0\n", f"- Links: {n_links}\n", 1)
    json_tmp = temporary_path(dest_dir / "graph.json")
    md_tmp = temporary_path(dest_dir / "graph.md")
    try:
        with (
            json_tmp.open("w", encoding="utf-8", newline="\n") as json_file,
            md_tmp.open("w", encoding="utf-8", newline="\n") as md_file,
        ):
            json_file.write(prefix)
            md_file.write(md_head)
            if n_links == 0:
                json_file.write("[]")
            else:
                json_file.write("[\n")
                first = True
                for cell in sorted(grouped):
                    refs = grouped.pop(cell)
                    row_key, period_id = identity.get(cell, (None, None))
                    formula = formula_by_node.get(cell)
                    formula_class = classify_formula(formula, refs, lags.get(cell))
                    item = {
                        "cell": cell,
                        "formula": formula,
                        "formula_class": formula_class,
                        "refs": refs,
                        "row_key": row_key,
                        "period_id": period_id,
                    }
                    body = json.dumps(item, ensure_ascii=False, indent=2).replace("\n", "\n    ")
                    json_file.write(("" if first else ",\n") + "    " + body)
                    first = False
                    md_file.write(
                        "| "
                        + " | ".join(
                            [
                                _cell(cell),
                                _cell(row_key or ""),
                                _cell(period_id or ""),
                                _cell(formula_class or ""),
                                _cell(formula or ""),
                                _cell(", ".join(refs)),
                            ]
                        )
                        + " |\n"
                    )
                json_file.write("\n  ]")
            json_file.write(suffix + "\n")
            json_file.flush()
            os.fsync(json_file.fileno())
        json_tmp.replace(dest_dir / "graph.json")
        md_tmp.replace(dest_dir / "graph.md")
    except BaseException:
        json_tmp.unlink(missing_ok=True)
        md_tmp.unlink(missing_ok=True)
        raise
    doc.links = []
