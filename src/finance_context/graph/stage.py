from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from finance_context.excel.a1 import index_to_col
from finance_context.formulas.csr import CellEdge, as_cell_edge
from finance_context.formulas.models import Edge, as_edge
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
from finance_context.layout.periods import period_position
from finance_context.layout.resolve import period_headers
from finance_context.models.context import GraphPointer
from finance_context.store.fs import read_parquet, write_json, write_parquet

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
) -> GraphPointer:
    book = book_dir or dest_dir
    import os as _os

    pack = bool(_os.environ.get("PROTO_PACK")) and (book / "ir" / "cell_edges.parquet").is_file()
    if cells is None:
        cells = read_parquet(book / "ir" / "cells.parquet")
    if edges is None:
        edges_path = book / "ir" / "edges.parquet"
        edges = read_parquet(edges_path) if edges_path.is_file() else []
    if pack:
        edges = [as_edge(edge) for edge in edges]
        cell_edges_arg = None
        cell_edges = []
    else:
        if cell_edges is None:
            cell_edges_path = book / "ir" / "cell_edges.parquet"
            if _os.environ.get("PROTO_DROP_CE") and cell_edges_path.is_file():
                cell_edges = _proto_read_cell_edges(cell_edges_path)
            else:
                cell_edges = read_parquet(cell_edges_path) if cell_edges_path.is_file() else []
        edges = [as_edge(edge) for edge in edges]
        cell_edges_arg = cell_edges
        cell_edges = [as_cell_edge(edge) for edge in cell_edges]

    row_meta = _row_meta(layout)
    period_by_cell, _period_index = _period_maps(layout)
    index_rows = _index_rows(cells, row_meta, period_by_cell)
    if pack:
        empty_nodes = _pack_empty_nodes(
            book / "ir" / "cell_edges.parquet", {row[0] for row in index_rows}
        )
    else:
        empty_nodes = _empty_range_nodes(cell_edges, {row[0] for row in index_rows})
    index_rows.extend(
        _empty_index_row(node_id, row_meta, period_by_cell) for node_id in empty_nodes
    )
    write_parquet(dest_dir / "ir" / "graph_index.parquet", INDEX_COLUMNS, index_rows)

    if pack:
        cycles, doc, iterate = _pack_graph_tail(
            dest_dir,
            book,
            job_id,
            layout,
            cells,
            edges,
            index_rows,
            row_meta,
            period_by_cell,
        )
    elif _os.environ.get("PROTO_GRAPH"):
        cycles, doc, iterate = _proto_graph_tail(
            dest_dir, book, job_id, layout, cells, edges, cell_edges, cell_edges_arg,
            index_rows, row_meta, period_by_cell,
        )
    else:
        period_of = {row[0]: row[4] for row in index_rows}
        enriched = [_enrich_edge(edge, period_of) for edge in cell_edges]
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
        )

        cycles = classify_cycles(enriched)
        hints = circularity_hints(layout, index_rows, cycles)
        iterate = _workbook_iterate(book)
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


def _enrich_edge(edge: CellEdge, period_of: dict[str, str | None]) -> CellEdge:
    source = str(edge.source or "")
    target = str(edge.target or "")
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


_LINKS_SLOT = "__PROTO_LINKS_SLOT__"


def _proto_graph_tail(
    dest_dir, book, job_id, layout, cells, edges, cell_edges, cell_edges_arg,
    index_rows, row_meta, period_by_cell,
):
    """PROTOTYPE: enrich edges in place, write parquet column-wise, stream links
    into graph.json and graph.md without FormulaLink models or a whole-document string."""
    from finance_context.store.fs import write_parquet_objects

    period_of = {row[0]: row[4] for row in index_rows}
    owned = cell_edges_arg if isinstance(cell_edges_arg, list) else None
    for i, edge in enumerate(cell_edges):
        enriched_edge = _enrich_edge(edge, period_of)
        cell_edges[i] = enriched_edge
        if owned is not None and owned is not cell_edges:
            owned[i] = enriched_edge  # drop the caller's pre-enrich tuple too
    del period_of
    enriched = cell_edges

    def b(name):
        return lambda e: bool(getattr(e, name))

    def ob(name):
        return lambda e: _opt_bool(getattr(e, name))

    getters = []
    for name, _t in IR_CELL_EDGE_COLUMNS:
        if name in ("unresolved", "truncated", "dangling", "named"):
            getters.append(b(name))
        elif name in ("abs_col", "abs_row", "abs_col_end", "abs_row_end"):
            getters.append(ob(name))
        else:
            getters.append(lambda e, _n=name: getattr(e, _n))
    write_parquet_objects(
        dest_dir / "ir" / "graph_edges.parquet", IR_CELL_EDGE_COLUMNS, enriched, getters
    )
    cycles = classify_cycles(enriched)
    hints = circularity_hints(layout, index_rows, cycles)
    iterate = _workbook_iterate(book)
    doc = _summary(job_id, len(index_rows), edges, enriched, cycles, iterate, hints)
    grouped, identity, formula_by_node, lags = _proto_link_inputs(
        cells, edges, row_meta, period_by_cell, enriched
    )
    _emit_graph_files(dest_dir, doc, grouped, identity, formula_by_node, lags)
    return cycles, doc, iterate


def _emit_graph_files(dest_dir, doc, grouped, identity, formula_by_node, lags) -> None:
    """Stream graph.json and graph.md. Links are not held as FormulaLink models."""
    import os

    from finance_context.render.graph import _cell, render_graph_markdown

    n_links = len(grouped)
    head = doc.model_dump(mode="json", by_alias=True)
    head["links"] = _LINKS_SLOT
    text = json.dumps(head, ensure_ascii=False, indent=2)
    prefix, suffix = text.split(json.dumps(_LINKS_SLOT), 1)
    md_head = render_graph_markdown(doc)
    md_head = md_head.replace("- Links: 0\n", f"- Links: {n_links}\n", 1)
    jtmp = dest_dir / "graph.json.tmp"
    mtmp = dest_dir / "graph.md.tmp"
    with jtmp.open("w", encoding="utf-8", newline="\n") as jf, mtmp.open(
        "w", encoding="utf-8", newline="\n"
    ) as mf:
        jf.write(prefix)
        mf.write(md_head)
        if n_links == 0:
            jf.write("[]")
        else:
            jf.write("[\n")
            first = True
            for cell in sorted(grouped):
                refs = grouped.pop(cell)
                row_key, period_id = identity.get(cell, (None, None))
                formula = formula_by_node.get(cell)
                fclass = classify_formula(formula, refs, lags.get(cell))
                item = {
                    "cell": cell,
                    "formula": formula,
                    "formula_class": fclass,
                    "refs": refs,
                    "row_key": row_key,
                    "period_id": period_id,
                }
                body = json.dumps(item, ensure_ascii=False, indent=2).replace("\n", "\n    ")
                jf.write(("" if first else ",\n") + "    " + body)
                first = False
                mf.write(
                    "| "
                    + " | ".join(
                        [
                            _cell(cell),
                            _cell(row_key or ""),
                            _cell(period_id or ""),
                            _cell(fclass or ""),
                            _cell(formula or ""),
                            _cell(", ".join(refs)),
                        ]
                    )
                    + " |\n"
                )
            jf.write("\n  ]")
        jf.write(suffix + "\n")
        jf.flush()
        os.fsync(jf.fileno())
    jtmp.replace(dest_dir / "graph.json")
    mtmp.replace(dest_dir / "graph.md")
    doc.links = []


def _proto_link_inputs(cells, formula_edges, row_meta, period_by_cell, cell_edges, lags=None):
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
    lags = _lags_by_source(cell_edges or []) if lags is None else lags
    return grouped, identity, formula_by_node, lags


def _proto_read_cell_edges(path):
    """PROTOTYPE: parquet columns -> CellEdge tuples, one column list at a time,
    ids deduplicated; no per-row dicts."""
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(path)
    names = list(CellEdge._fields)
    pool: dict = {}
    out: list = []
    for rg in range(pf.num_row_groups):
        table = pf.read_row_group(rg, columns=names)
        cols = []
        for name in names:
            vals = table.column(name).to_pylist()
            if vals and isinstance(next((v for v in vals if v is not None), None), str):
                vals = [v if v is None else pool.setdefault(v, v) for v in vals]
            cols.append(vals)
        del table
        out.extend(CellEdge._make(t) for t in zip(*cols, strict=True))
        del cols
    return out


_USABLE_KINDS = {"ref", "cross_sheet", "range"}
_BOOL_COLUMNS = {"unresolved", "truncated", "dangling", "named"}
_ABS_COLUMNS = {"abs_col", "abs_row", "abs_col_end", "abs_row_end"}


def _pack_empty_nodes(path: Path, existing: set[str]) -> list[str]:
    """Status/target columns only. Same order as _empty_range_nodes."""
    import pyarrow.parquet as pq

    found: list[str] = []
    seen: set[str] = set(existing)
    pf = pq.ParquetFile(path)
    for rg in range(pf.num_row_groups):
        table = pf.read_row_group(rg, columns=["status", "target"])
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
    src_p = period_of.get(source)
    tgt_p = period_of.get(target)
    if not src_p or not tgt_p:
        return col_offset, "unaligned"
    return col_offset, calendar_lag(str(src_p), str(tgt_p))


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


def _cycles_packed(id_of: dict[str, int], src_ids, tgt_ids, flags) -> list[CycleRecord]:
    """Same records as classify_cycles, from integer endpoints and a period-chain bit."""
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
    matrix = sparse.csr_matrix(
        (data, (rows, cols)), shape=(len(nodes), len(nodes)), dtype=np.uint8
    )
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
    for pos in range(n_edges):
        if labels[rows[pos]] != labels[cols[pos]]:
            continue
        label = int(labels[rows[pos]])
        if sizes[label] < 2 and int(rows[pos]) != int(cols[pos]):
            continue
        saw.add(label)
        if int(rows[pos]) == int(cols[pos]):
            self_loop.add(label)
        bit = bool(flags[pos])
        previous = all_period.get(label)
        all_period[label] = bit if previous is None else previous and bit
    cycles: list[CycleRecord] = []
    seq = 1
    for label in order:
        group = members[label]
        if len(group) < 2 and label not in self_loop:
            continue
        if label not in saw or not all_period.get(label, False):
            cls = "unexpected"
        else:
            cls = "iterative_ok"
        cycles.append(
            CycleRecord.model_validate(
                {"id": f"c{seq}", "members": sorted(group), "class": cls}
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


def _pack_graph_tail(
    dest_dir, book, job_id, layout, cells, edges, index_rows, row_meta, period_by_cell
):
    """PROTOTYPE PROTO_PACK: enrich and classify from parquet row groups, no CellEdge list."""
    import array
    from collections import Counter

    import pyarrow.parquet as pq

    from finance_context.store.fs import ObjectBatchWriter

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
    ids = _NodeIds()
    src_ids = array.array("i")
    tgt_ids = array.array("i")
    flags = array.array("B")
    n_edges = 0
    names = [name for name, _dtype in IR_CELL_EDGE_COLUMNS]
    path = book / "ir" / "cell_edges.parquet"

    with ObjectBatchWriter(dest_dir / "ir" / "graph_edges.parquet", IR_CELL_EDGE_COLUMNS) as writer:
        pf = pq.ParquetFile(path)
        for rg in range(pf.num_row_groups):
            table = pf.read_row_group(rg, columns=names)
            data = {name: table.column(name).to_pylist() for name in names}
            del table
            width = len(data["source"])
            offsets: list[int | None] = [None] * width
            lag_col: list[str] = [""] * width
            for i in range(width):
                source = str(data["source"][i] or "")
                target = str(data["target"][i] or "")
                offset, lag = _offset_and_lag(source, target, period_of)
                lag = lag_pool.setdefault(lag, lag)
                offsets[i] = offset
                lag_col[i] = lag
                kind = data["kind"][i]
                kinds[str(kind or "ref")] += 1
                n_edges += 1
                reason = str(data["dangling_reason"][i] or "")
                if reason and target and (target, reason) not in seen_class:
                    seen_class.add((target, reason))
                    classes[reason] += 1
                if data["dangling"][i] and target and target not in dangling_seen:
                    dangling_seen.add(target)
                    dangling_ids.append(target)
                if data["truncated"][i] and source and source not in truncated_seen:
                    truncated_seen.add(source)
                    truncated_ids.append(source)
                if source:
                    bucket = lag_groups.setdefault(source, [])
                    if lag not in bucket:
                        bucket.append(lag)
                if (
                    source
                    and target
                    and not data["unresolved"][i]
                    and not data["dangling"][i]
                    and kind in _USABLE_KINDS
                ):
                    src_ids.append(ids.nid(source))
                    tgt_ids.append(ids.nid(target))
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
    del period_of
    cycles = _cycles_packed(ids.map, src_ids, tgt_ids, flags)
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
    grouped, identity, formula_by_node, lags = _proto_link_inputs(
        cells, edges, row_meta, period_by_cell, [], lags=lag_groups
    )
    _emit_graph_files(dest_dir, doc, grouped, identity, formula_by_node, lags)
    return cycles, doc, iterate
