from __future__ import annotations

import gc
import json
import logging
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
from finance_context.formulas.edge_file import iterate_edges
from finance_context.formulas.models import Edge, as_edge
from finance_context.formulas.stage import IR_CELL_EDGE_COLUMNS, EdgeCount
from finance_context.graph.cycles import classify_cycles
from finance_context.graph.formula_class import classify_formula, classify_formula_bits, note_lag
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
from finance_context.graph.ranges import CellGrid, RangeHops, SegmentIds, hop_flags, is_cell_id
from finance_context.graph.refs import parse_node_id
from finance_context.layout.models import Layout
from finance_context.layout.periods import period_position
from finance_context.layout.resolve import period_headers
from finance_context.memory import trim_process_heap
from finance_context.models.context import GraphPointer
from finance_context.observability import log_event
from finance_context.settings import Settings
from finance_context.store.fs import (
    atomic_replace,
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

# GRAPH_RSS_LOG=1 emits graph_rss lines on the worker log. Anything else, including
# a filesystem path, stays off so the log never receives a path or a formula.
_LOGGER = logging.getLogger(__name__)
_rss_enabled: bool | None = None
_RSS_MARKS = frozenset(
    {
        "formulas_loaded",
        "edges_begin",
        "row_group_begin",
        "row_group_pylist",
        "row_group_done",
        "edges_scanned",
        "before_hops",
        "after_hops",
        "before_cycles",
        "after_cycles",
        "after_release",
        "after_refs",
        "before_formula_text",
        "after_formula_text",
        "graph_written",
    }
)


def _reset_rss_mark() -> None:
    global _rss_enabled
    _rss_enabled = None


def _rss_on() -> bool:
    global _rss_enabled
    if _rss_enabled is None:
        raw = os.environ.get("GRAPH_RSS_LOG", "").strip().lower()
        _rss_enabled = raw in {"1", "true", "yes", "on"}
    return _rss_enabled


def _rss_mark(mark: str, **fields: object) -> None:
    """One RSS sample on the worker log. Counts only, and only when enabled."""
    if mark not in _RSS_MARKS or not _rss_on():
        return
    from finance_context.memory import rss_bytes

    counts = {key: value for key, value in fields.items() if type(value) is int}
    log_event(
        _LOGGER,
        logging.INFO,
        "graph_rss",
        mark,
        mark=mark,
        rss_mb=rss_bytes() // (1024 * 1024),
        **counts,
    )


class _Periods:
    """Period of a node from its column. No per-cell dictionary."""

    def __init__(self, period_by_cell: dict[tuple[str, int], str]) -> None:
        self._columns = period_by_cell

    def get(self, node: str, default: str | None = None) -> str | None:
        parsed = parse_node_id(node)
        if parsed is None:
            return default
        return self._columns.get((parsed[0], parsed[1]), default)


class _Formula:
    """One graph link: its layout identity, its refs, and later its formula text.

    ``formula_cell`` marks a cell that has a formula. The text stays out of this
    record through the edge walk and the cycle matrix, then ``_attach_formula_text``
    reads it back for classification and ``graph.json``.
    """

    __slots__ = ("formula", "row_key", "period_id", "refs", "formula_cell", "lag_bits")

    def __init__(
        self,
        formula: str | None,
        row_key: str | None,
        period_id: str | None,
        *,
        formula_cell: bool | None = None,
    ) -> None:
        self.formula = formula
        self.row_key = row_key
        self.period_id = period_id
        self.refs: list[str] = []
        self.formula_cell = bool(formula) if formula_cell is None else formula_cell
        self.lag_bits = 0


def _formula_record(sheet, row, col, addr, raw, row_meta, period_by_cell) -> tuple[str, _Formula]:
    return f"{sheet}!{addr}", _Formula(
        str(raw),
        row_meta.get((str(sheet), int(row))),
        period_by_cell.get((str(sheet), int(col))),
    )


def _formula_identity(sheet, row, col, addr, row_meta, period_by_cell) -> tuple[str, _Formula]:
    """Same identity as ``_formula_record`` with the formula text left on disk."""
    return f"{sheet}!{addr}", _Formula(
        None,
        row_meta.get((str(sheet), int(row))),
        period_by_cell.get((str(sheet), int(col))),
        formula_cell=True,
    )


def _load_formulas(book: Path, row_meta, period_by_cell) -> dict[str, _Formula]:
    """Formula rows only. Value cells and formula text stay in the parquet file."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    path = book / "ir" / "cells.parquet"
    if not path.is_file():
        return {}
    found: dict[str, _Formula] = {}
    columns = ["sheet", "row", "col", "addr", "formula_raw"]
    for batch in pq.ParquetFile(path).iter_batches(columns=columns, batch_size=65536):
        raw = batch.column(4)
        mask = pc.and_(pc.invert(pc.is_null(raw)), pc.not_equal(raw, ""))
        if not pc.any(mask).as_py():
            continue
        kept = batch.select(["sheet", "row", "col", "addr"]).filter(mask)
        sheets = kept.column(0).to_pylist()
        rows = kept.column(1).to_pylist()
        cols = kept.column(2).to_pylist()
        addrs = kept.column(3).to_pylist()
        for index, sheet in enumerate(sheets):
            node, rec = _formula_identity(
                sheet,
                rows[index],
                cols[index],
                addrs[index],
                row_meta,
                period_by_cell,
            )
            found[node] = rec
    return found


def _attach_formula_text(book: Path, formulas: dict[str, _Formula]) -> None:
    """Fill formula text after the cycle matrix is gone.

    Records built from an in-memory cell list already hold the text. A record
    created for an edge source that is not a formula cell stays empty.
    """
    if not any(rec.formula_cell and not rec.formula for rec in formulas.values()):
        return
    import pyarrow.parquet as pq

    path = book / "ir" / "cells.parquet"
    if not path.is_file():
        return
    columns = ["sheet", "addr", "formula_raw"]
    for batch in pq.ParquetFile(path).iter_batches(columns=columns, batch_size=65536):
        raw = batch.column(2).to_pylist()
        sheets = batch.column(0).to_pylist()
        addrs = batch.column(1).to_pylist()
        for index, formula in enumerate(raw):
            if not formula:
                continue
            rec = formulas.get(f"{sheets[index]}!{addrs[index]}")
            if rec is None or not rec.formula_cell:
                continue
            rec.formula = str(formula)
        del raw


def _formulas_from_cells(cells, row_meta, period_by_cell) -> dict[str, _Formula]:
    found: dict[str, _Formula] = {}
    for cell in cells:
        raw = cell.get("formula_raw")
        if not raw:
            continue
        node, rec = _formula_record(
            cell["sheet"],
            cell["row"],
            cell["col"],
            cell["addr"],
            raw,
            row_meta,
            period_by_cell,
        )
        found[node] = rec
    return found


def _bind_refs(formulas: dict[str, _Formula], formula_edges) -> None:
    """Attach first-seen targets. A source with no formula cell still becomes a link."""
    for edge in formula_edges:
        source = str(edge.source or "")
        if not source:
            continue
        rec = formulas.get(source)
        if rec is None:
            rec = _Formula(None, None, None)
            formulas[source] = rec
        target = edge.target
        if target and (text := str(target)) not in rec.refs:
            rec.refs.append(text)


def _links_from_formulas(
    formulas: dict[str, _Formula], lags: dict | None = None, *, bits: bool = False
) -> list[FormulaLink]:
    lags = lags or {}
    links: list[FormulaLink] = []
    for cell in sorted(formulas):
        rec = formulas[cell]
        if bits:
            formula_class = classify_formula_bits(rec.formula, rec.refs, rec.lag_bits)
        else:
            formula_class = classify_formula(rec.formula, rec.refs, lags.get(cell))
        links.append(
            FormulaLink(
                cell=cell,
                formula=rec.formula,
                formula_class=formula_class,
                refs=rec.refs,
                row_key=rec.row_key,
                period_id=rec.period_id,
            )
        )
    return links


def _circular_rows(layout: Layout) -> dict[tuple[str, int], str]:
    found: dict[tuple[str, int], str] = {}
    for sheet in layout.sheets:
        for block in sheet.blocks:
            for row in block.rows:
                if "circular" in (row.label or "").casefold():
                    found.setdefault((sheet.name, int(row.row)), row.label)
    return found


def _stream_index(
    book: Path,
    path: Path,
    row_meta: dict[tuple[str, int], str],
    period_by_cell: dict[tuple[str, int], str],
    circular: dict[tuple[str, int], str],
    cell_edge_path: Path | None,
    cell_edges: list | None,
    row_group_rows: int,
) -> tuple[int, dict[tuple[str, int], list[str]]]:
    """Write the graph index from parquet. The cell dictionaries are not kept."""
    import pyarrow.parquet as pq

    from finance_context.store.fs import RowGroupWriter
    circular_ids: dict[tuple[str, int], list[str]] = {}
    count = 0
    names = [name for name, _dtype in INDEX_COLUMNS]
    source = book / "ir" / "cells.parquet"
    columns = ["sheet", "addr", "row", "col", "formula_raw", "cached_value"]
    empty_ids = _empty_target_ids(cell_edge_path, cell_edges)
    still_empty = set(empty_ids)
    with RowGroupWriter(path, INDEX_COLUMNS, row_group_rows) as writer:
        chunk: list[list[object]] = [[] for _ in names]
        if source.is_file():
            for batch in pq.ParquetFile(source).iter_batches(columns=columns, batch_size=65536):
                sheets = batch.column(0).to_pylist()
                addrs = batch.column(1).to_pylist()
                row_numbers = batch.column(2).to_pylist()
                col_numbers = batch.column(3).to_pylist()
                formulas = batch.column(4).to_pylist()
                cached = batch.column(5).to_pylist()
                for index, sheet_name in enumerate(sheets):
                    sheet = str(sheet_name)
                    addr = str(addrs[index])
                    row_n = int(row_numbers[index])
                    col_n = int(col_numbers[index])
                    formula = formulas[index]
                    value = cached[index]
                    if formula:
                        node_type = "calculated"
                    elif value not in (None, ""):
                        node_type = "input"
                    else:
                        node_type = "empty"
                    node_id = f"{sheet}!{addr}"
                    row = (
                        node_id,
                        sheet,
                        addr,
                        row_meta.get((sheet, row_n)),
                        period_by_cell.get((sheet, col_n)),
                        node_type,
                    )
                    for slot, item in enumerate(row):
                        chunk[slot].append(item)
                    count += 1
                    still_empty.discard(node_id)
                    label_row = (sheet, row_n)
                    if label_row in circular:
                        bucket = circular_ids.setdefault(label_row, [])
                        if node_id not in bucket:
                            bucket.append(node_id)
                    if len(chunk[0]) >= 65536:
                        writer.write_lists(chunk)
                        chunk = [[] for _ in names]
        for node_id in empty_ids:
            if node_id not in still_empty:
                continue
            row = _empty_index_row(node_id, row_meta, period_by_cell)
            for slot, item in enumerate(row):
                chunk[slot].append(item)
            count += 1
            if len(chunk[0]) >= 65536:
                writer.write_lists(chunk)
                chunk = [[] for _ in names]
        if chunk[0]:
            writer.write_lists(chunk)
    return count, circular_ids


def _empty_target_ids(cell_edge_path: Path | None, cell_edges: list | None) -> list[str]:
    """Empty range targets in first-seen order. Cells drop the ones they already cover."""
    if cell_edges is not None:
        return _empty_range_nodes(cell_edges, set())
    if cell_edge_path is None or not cell_edge_path.is_file():
        return []
    from finance_context.store.duckdb_io import limited_connection

    with limited_connection(cell_edge_path) as con:
        rows = con.execute(
            """
            SELECT target FROM (
                SELECT target, row_number() OVER () AS ord
                FROM read_parquet(?)
                WHERE status = 'empty' AND target IS NOT NULL AND target <> ''
            )
            QUALIFY row_number() OVER (PARTITION BY target ORDER BY ord) = 1
            ORDER BY ord
            """,
            [str(cell_edge_path)],
        ).fetchall()
    return [str(row[0]) for row in rows]


def _accept_layout(layout: Layout | list[Layout]) -> Layout:
    """Take a layout, emptying a one-slot list so the caller can drop it."""
    if isinstance(layout, list):
        if len(layout) != 1:
            raise RuntimeError("layout release slot is empty")
        return layout.pop()
    return layout


def build_formula_graph(
    dest_dir: Path,
    *,
    job_id: str,
    layout: Layout | list[Layout],
    cells: list[dict] | None = None,
    edges: list | None = None,
    cell_edges: list | None = None,
    book_dir: Path | None = None,
    settings: Settings | None = None,
) -> GraphPointer:
    chosen = settings if isinstance(settings, Settings) else Settings()
    book = book_dir or dest_dir
    layout = _accept_layout(layout)
    cell_edge_path = book / "ir" / "cell_edges.parquet"
    stream = (
        chosen.graph_stream
        and cell_edge_path.is_file()
        and (cell_edges is None or isinstance(cell_edges, EdgeCount))
    )
    # A passed list is the test path. Production leaves edges on disk and
    # folds them once while the formula links are bound.
    if edges is None and not stream:
        edges = list(iterate_edges(book / "ir" / "edges.parquet"))
    elif edges is not None:
        edges = [as_edge(edge) for edge in edges]
    if not stream:
        if cell_edges is None or isinstance(cell_edges, EdgeCount):
            cell_edges = read_parquet(cell_edge_path) if cell_edge_path.is_file() else []
        cell_edges = [as_cell_edge(edge) for edge in cell_edges]

    row_meta = _row_meta(layout)
    period_by_cell, _period_index = _period_maps(layout)
    circular = _circular_rows(layout)
    if stream:
        # Row maps are copied. The caller's list slot is already empty.
        del layout
        gc.collect()
        trim_process_heap()
    if cells is None:
        node_count, circular_ids = _stream_index(
            book,
            dest_dir / "graph" / "index.parquet",
            row_meta,
            period_by_cell,
            circular,
            cell_edge_path if stream else None,
            cell_edges if not stream else None,
            chosen.parquet_row_group_rows,
        )
        formulas = _load_formulas(book, row_meta, period_by_cell)
        del row_meta
        _rss_mark("formulas_loaded", nodes=node_count, formulas=len(formulas))
        index_rows: list[tuple[object, ...]] = []
    else:
        index_rows = _index_rows(cells, row_meta, period_by_cell)
        if stream:
            empty_nodes = _empty_nodes_from_parquet(cell_edge_path, {row[0] for row in index_rows})
        else:
            empty_nodes = _empty_range_nodes(cell_edges, {row[0] for row in index_rows})
        index_rows.extend(
            _empty_index_row(node_id, row_meta, period_by_cell) for node_id in empty_nodes
        )
        write_parquet(
            dest_dir / "graph" / "index.parquet",
            INDEX_COLUMNS,
            index_rows,
            row_group_rows=chosen.parquet_row_group_rows,
        )
        node_count = len(index_rows)
        circular_ids = None
        formulas = _formulas_from_cells(cells, row_meta, period_by_cell)

    if stream:
        cycles, doc, iterate = _graph_from_parquet(
            dest_dir,
            book,
            job_id,
            circular,
            formulas,
            edges,
            index_rows,
            period_by_cell,
            cell_edge_path,
            stream_json=chosen.json_stream,
            node_count=node_count,
            circular_ids=circular_ids,
        )
    else:
        period_of = _Periods(period_by_cell)
        view = _RangeView(book, formulas, period_by_cell)
        enriched = [_enrich_edge(edge, period_of, view) for edge in cell_edges]
        write_parquet(
            dest_dir / "graph" / "edges.parquet",
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
        hints = circularity_hints(layout, index_rows, cycles, cells_by_row=circular_ids)
        iterate = _workbook_iterate(book)
        doc = _summary(job_id, node_count, edges, enriched, cycles, iterate, hints)
        lags = _lags_by_source(enriched)
        _add_range_lags(lags, users, view, period_of)
        _bind_refs(formulas, edges)
        _attach_formula_text(book, formulas)
        doc.links = _links_from_formulas(formulas, lags)
        if chosen.json_stream:
            _stream_graph_files(dest_dir, doc)
        else:
            write_json(
                dest_dir / "graph" / "graph.json",
                doc.model_dump(mode="json", by_alias=True),
                stream=False,
            )
            from finance_context.render.graph import render_graph_markdown

            markdown = render_graph_markdown(doc)
            atomic_write_bytes(dest_dir / "graph" / "graph.md", markdown.encode("utf-8"))
    unexpected = sum(1 for c in cycles if c.class_ == "unexpected")
    iterative = sum(1 for c in cycles if c.class_ == "iterative_ok")
    classes = doc.dangling_classes
    return GraphPointer(
        artifact="graph.json",
        cell_edges="ir/cell_edges.parquet",
        index="graph/index.parquet",
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
    layout: Layout | None,
    index_rows: list[tuple[object, ...]],
    cycles: list[CycleRecord],
    cells_by_row: dict[tuple[str, int], list[str]] | None = None,
    circular: dict[tuple[str, int], str] | None = None,
) -> list[CircularityHint]:
    if cycles:
        return []
    cells = cells_by_row if cells_by_row is not None else _cells_by_row(index_rows)
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

    if circular is None:
        circular = {} if layout is None else _circular_rows(layout)
    for (sheet, row), label in circular.items():
        add(sheet, row, label)
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


class _RangeView:
    """Range nodes as the graph stage sees them, built only when a book has one.

    A node's cells are not in the edge files. Its first member (for the edge's
    offset and lag), its member columns (for formula_class) and its formula
    cells (for cycles) come from its bounds and the book's cells.
    """

    def __init__(self, book: Path, formulas: dict[str, _Formula], period_by_cell: dict) -> None:
        self._book = book
        self._formulas = formulas
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
                node for node, rec in self._formulas.items() if rec.formula_cell
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


def _note_range_lags(formulas, users, view: _RangeView, period_of, stray: dict[str, int]) -> None:
    """Same member lags as ``_add_range_lags``, folded into class bits."""
    for source, node in users:
        member_periods = view.member_periods(node)
        if not member_periods:
            continue
        rec = formulas.get(source)
        bits = stray.get(source, 0) if rec is None else rec.lag_bits
        src_period = period_of.get(source)
        for member_period in member_periods:
            if not src_period or not member_period:
                lag = "unaligned"
            else:
                lag = calendar_lag(str(src_period), str(member_period))
            bits = note_lag(bits, lag)
        if rec is None:
            stray[source] = bits
        else:
            rec.lag_bits = bits


def _release_range_view(view: _RangeView) -> None:
    """Drop the presence index and hop rows once lags no longer need them."""
    view._grid = None
    view._hops = None
    view._nodes.clear()
    view._periods.clear()


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


def _numpy_ids(values):
    """Copy packed edge ids into an array the cycle matrix can keep alone.

    ``array.array`` and the NumPy copy used to live together through
    ``connected_components``. Dropping the first copy is what keeps that crest
    under the job RSS cap.
    """
    import numpy as np

    return np.array(values, dtype=np.int32, copy=True)


def _cycles_packed(
    id_of: dict[str, int],
    src_ids,
    tgt_ids,
    flags,
    extra=None,
    *,
    n_nodes: int | None = None,
    order_names=None,
) -> list[CycleRecord]:
    """Same records as classify_cycles, from integer endpoints and a period-chain bit.

    An edge flagged ``_STRUCTURAL`` links a range node to its cells. It joins
    components but is not a formula edge, so it does not vote on the class.
    ``extra(label_of)`` yields (component, bit, self loop) for the formula ->
    member edges a per-formula expansion would have had inside a component.

    ``n_nodes`` counts anonymous segment ids that are not in ``id_of``.
    ``order_names(accept)`` reports those ids and their ``#seg|...`` names so
    cycle ids stay in the order of a sorted walk over every node name.
    """
    import numpy as np
    from scipy import sparse
    from scipy.sparse.csgraph import connected_components

    if not id_of:
        return []
    anonymous = n_nodes is not None
    if anonymous:
        nodes = None
        n = n_nodes
        n_edges = len(src_ids)
        if n_edges:
            rows = np.asarray(src_ids, dtype=np.int32)
            cols = np.asarray(tgt_ids, dtype=np.int32)
        else:
            rows = np.empty(0, dtype=np.int32)
            cols = np.empty(0, dtype=np.int32)
        rank = None
    else:
        nodes = sorted(id_of)
        n = len(nodes)
        # slot -> index in `nodes`. An array, not a dict of every node.
        rank = np.empty(n, dtype=np.int32)
        rank[np.fromiter((id_of[name] for name in nodes), dtype=np.int32, count=n)] = np.arange(
            n, dtype=np.int32
        )
        n_edges = len(src_ids)
        if n_edges:
            rows = rank[np.asarray(src_ids, dtype=np.int32)]
            cols = rank[np.asarray(tgt_ids, dtype=np.int32)]
        else:
            rows = np.empty(0, dtype=np.int32)
            cols = np.empty(0, dtype=np.int32)
    data = np.ones(n_edges, dtype=np.uint8)
    matrix = sparse.csr_matrix((data, (rows, cols)), shape=(n, n), dtype=np.uint8)
    _n_comp, labels = connected_components(matrix, directed=True, connection="strong")
    del matrix
    sizes = np.bincount(labels)
    all_period: dict[int, bool] = {}
    saw: set[int] = set()
    self_loop: set[int] = set()
    if n_edges:
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
        del same
    del rows, cols
    if extra is not None:
        if anonymous:

            def label_of(name: str, _labels=labels) -> int | None:
                slot = id_of.get(name)
                return None if slot is None else int(_labels[int(slot)])

        else:

            def label_of(name: str, _labels=labels, _rank=rank) -> int | None:
                slot = id_of.get(name)
                return None if slot is None else int(_labels[int(_rank[slot])])

        for label, bit, is_self in extra(label_of):
            saw.add(label)
            if is_self:
                self_loop.add(label)
            previous = all_period.get(label)
            all_period[label] = bit if previous is None else previous and bit
    del rank
    if anonymous:
        return _cycles_named(
            id_of, labels, sizes, self_loop, saw, all_period, order_names
        )
    # Member lists only for components that can be cycles. Singletons stay in `labels`.
    order: list[int] = []
    members: dict[int, list[str]] = {}
    for name, label_raw in zip(nodes, labels, strict=True):
        label = int(label_raw)
        if sizes[label] < 2 and label not in self_loop:
            continue
        bucket = members.get(label)
        if bucket is None:
            members[label] = [name]
            order.append(label)
        else:
            bucket.append(name)
    del nodes, labels, sizes
    return _emit_cycles(order, members, self_loop, saw, all_period)


def _cycles_named(
    id_of, labels, sizes, self_loop, saw, all_period, order_names
) -> list[CycleRecord]:
    """Cycle ids in min-name order, segments included, without storing their strings."""
    mins: dict[int, str] = {}
    cells: dict[int, list[str]] = {}

    def offer(slot: int, name: str) -> None:
        label = int(labels[slot])
        if sizes[label] < 2 and label not in self_loop:
            return
        current = mins.get(label)
        if current is None or name < current:
            mins[label] = name

    for name, slot in id_of.items():
        offer(int(slot), name)
        if is_cell_id(name):
            label = int(labels[int(slot)])
            if sizes[label] < 2 and label not in self_loop:
                continue
            bucket = cells.get(label)
            if bucket is None:
                cells[label] = [name]
            else:
                bucket.append(name)
    if order_names is not None:
        order_names(offer)
    order = sorted(mins, key=lambda label: mins[label])
    members = {label: cells.get(label, []) for label in order}
    return _emit_cycles(order, members, self_loop, saw, all_period)


def _emit_cycles(order, members, self_loop, saw, all_period) -> list[CycleRecord]:
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


def _fold_formula_edges(
    formulas, formula_edges, stray: dict[str, int] | None = None
) -> tuple[IdCount, IdCount, IdCount]:
    """One pass: unresolved, dynamic and external ids, and formula refs.

    First-seen order matches ``_edge_bucket`` and ``_bind_refs`` walking the
    same rows separately. Repeated targets share one string.
    """
    unresolved: list[str] = []
    dynamic: list[str] = []
    external: list[str] = []
    seen_unresolved: set[str] = set()
    seen_dynamic: set[str] = set()
    seen_external: set[str] = set()
    shared: dict[str, str] = {}
    for edge in formula_edges:
        source = str(edge.source or "")
        if not source:
            continue
        if edge.unresolved and edge.kind not in {"dynamic", "external"}:
            if source not in seen_unresolved:
                seen_unresolved.add(source)
                unresolved.append(source)
        if edge.kind == "dynamic" and source not in seen_dynamic:
            seen_dynamic.add(source)
            dynamic.append(source)
        if edge.kind == "external" and source not in seen_external:
            seen_external.add(source)
            external.append(source)
        rec = formulas.get(source)
        if rec is None:
            rec = _Formula(None, None, None)
            if stray is not None:
                rec.lag_bits = stray.pop(source, 0)
            formulas[source] = rec
        elif stray is not None and source in stray:
            rec.lag_bits |= stray.pop(source)
        target = edge.target
        if not target:
            continue
        raw = str(target)
        text = shared.get(raw)
        if text is None:
            shared[raw] = raw
            text = raw
        if text not in rec.refs:
            rec.refs.append(text)
    return (
        IdCount(count=len(unresolved), ids=unresolved[:ID_CAP]),
        IdCount(count=len(dynamic), ids=dynamic[:ID_CAP]),
        IdCount(count=len(external), ids=external[:ID_CAP]),
    )


def _graph_from_parquet(
    dest_dir,
    book,
    job_id,
    circular,
    formulas,
    edges,
    index_rows,
    period_by_cell,
    path: Path,
    *,
    stream_json: bool,
    node_count: int,
    circular_ids: dict[tuple[str, int], list[str]] | None,
):
    """Enrich and classify from parquet row groups. The cell-edge list is not loaded."""
    import array

    import pyarrow.parquet as pq

    from finance_context.store.fs import RowGroupWriter

    period_of = _Periods(period_by_cell)
    kinds: Counter[str] = Counter()
    classes: Counter[str] = Counter()
    seen_class: set[tuple[str, str]] = set()
    dangling_ids: list[str] = []
    dangling_seen: set[str] = set()
    truncated_ids: list[str] = []
    truncated_seen: set[str] = set()
    # Sources that are not formula cells. Formula records keep their own bits.
    stray_lags: dict[str, int] = {}
    lag_pool: dict[str, str] = {}
    view = _RangeView(book, formulas, period_by_cell)
    range_users: list[tuple[str, RangeNode]] = []
    ids = _NodeIds()
    src_ids = array.array("i")
    tgt_ids = array.array("i")
    flags = array.array("B")
    n_edges = 0
    names = [name for name, _dtype in IR_CELL_EDGE_COLUMNS]
    with RowGroupWriter(dest_dir / "graph" / "edges.parquet", IR_CELL_EDGE_COLUMNS, 0) as writer:
        # One output group per source group. A single source group stays write_table.
        parquet = pq.ParquetFile(path)
        multi = parquet.num_row_groups > 1
        if multi:
            writer.begin_row_groups()
        group_count = parquet.num_row_groups
        _rss_mark("edges_begin", groups=group_count, rows=parquet.metadata.num_rows)
        for group in range(group_count):
            group_rows = parquet.metadata.row_group(group).num_rows
            _rss_mark("row_group_begin", group=group, rows=group_rows)
            table = parquet.read_row_group(group, columns=names)
            data = {name: table.column(name).to_pylist() for name in names}
            del table
            width = len(data["source"])
            _rss_mark("row_group_pylist", group=group, rows=width)
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
                    rec = formulas.get(source)
                    if rec is None:
                        stray_lags[source] = note_lag(stray_lags.get(source, 0), lag)
                    else:
                        rec.lag_bits = note_lag(rec.lag_bits, lag)
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
            _rss_mark(
                "row_group_done",
                group=group,
                nodes=len(ids.map),
                packed=len(src_ids),
            )
    _rss_mark(
        "edges_scanned",
        edges=n_edges,
        nodes=len(ids.map),
        packed=len(src_ids),
        range_users=len(range_users),
        formulas=len(formulas),
    )
    dangling_count = len(dangling_ids)
    dangling_ids = dangling_ids[:ID_CAP]
    truncated_count = len(truncated_ids)
    truncated_ids = truncated_ids[:ID_CAP]
    del dangling_seen, truncated_seen, seen_class, lag_pool
    # The row-group lists are gone. Return their arenas before the hop matrix.
    gc.collect()
    trim_process_heap()
    extra = None
    hops = None
    segments = None
    n_nodes = None
    order_names = None
    if range_users:
        _rss_mark("before_hops", range_users=len(range_users))
        hops = view.hops()
        unique = {node.node_id: node for _source, node in range_users}
        # New leaf and range ids are assigned during wire. The gap keeps them
        # below the anonymous segment ids.
        segments = SegmentIds(len(ids.map) + len(formulas) + len(unique) + 1)

        def add_hop(
            parent: int,
            child: int,
            _src=src_ids,
            _tgt=tgt_ids,
            _flags=flags,
        ) -> None:
            _src.append(parent)
            _tgt.append(child)
            _flags.append(_STRUCTURAL)

        for node in unique.values():
            hops.wire(node, ids.nid, add_hop, segments)
        # The default args are the only other owners of the packed id arrays.
        del add_hop

        def extra(label_of, _hops=hops, _users=range_users, _period=period_of):
            return hop_flags(
                label_of,
                _users,
                _hops,
                lambda source, member: _member_chain(source, member, _period),
            )

        n_nodes = segments.n_nodes if segments.span else len(ids.map)
        order_names = segments.offer
        _rss_mark("after_hops", nodes=len(ids.map), packed=len(src_ids))
    row_ids = _numpy_ids(src_ids)
    col_ids = _numpy_ids(tgt_ids)
    del src_ids, tgt_ids
    gc.collect()
    trim_process_heap()
    _rss_mark("before_cycles", nodes=len(ids.map), packed=int(row_ids.size))
    cycles = _cycles_packed(
        ids.map,
        row_ids,
        col_ids,
        flags,
        extra,
        n_nodes=n_nodes,
        order_names=order_names,
    )
    del extra
    del ids, row_ids, col_ids, flags, hops, segments
    gc.collect()
    trim_process_heap()
    _rss_mark("after_cycles", cycles=len(cycles))
    # Same lags _add_range_lags would append, stored as class bits on the formula.
    _note_range_lags(formulas, range_users, view, period_of, stray_lags)
    _release_range_view(view)
    del range_users, view
    gc.collect()
    trim_process_heap()
    _rss_mark("after_release", formulas=len(formulas))
    hints = circularity_hints(
        None, index_rows, cycles, cells_by_row=circular_ids, circular=circular
    )
    iterate = _workbook_iterate(book)
    edge_source = edges if edges is not None else book / "ir" / "edges.parquet"
    unresolved, dynamic, external = _fold_formula_edges(
        formulas, iterate_edges(edge_source), stray_lags
    )
    del stray_lags
    _rss_mark("after_refs", formulas=len(formulas))
    doc = GraphDocument(
        schema_version=GRAPH_SCHEMA_VERSION,
        job_id=job_id,
        nodes=node_count,
        edges=n_edges,
        kinds=dict(kinds),
        contract=GraphContract(),
        unresolved=unresolved,
        dynamic=dynamic,
        external=external,
        truncated=IdCount(count=truncated_count, ids=truncated_ids),
        dangling=IdCount(count=dangling_count, ids=dangling_ids),
        dangling_classes=dict(classes),
        cycles=cycles,
        iterate=iterate,
        circularity_hints=list(hints or []),
    )
    _rss_mark("before_formula_text", formulas=len(formulas))
    _attach_formula_text(book, formulas)
    _rss_mark("after_formula_text", formulas=len(formulas))
    if stream_json:
        _stream_graph_files(dest_dir, doc, formulas)
    else:
        doc.links = _links_from_formulas(formulas, bits=True)
        write_json(
            dest_dir / "graph" / "graph.json",
            doc.model_dump(mode="json", by_alias=True),
            stream=False,
        )
        from finance_context.render.graph import render_graph_markdown

        markdown = render_graph_markdown(doc)
        atomic_write_bytes(dest_dir / "graph" / "graph.md", markdown.encode("utf-8"))
    _rss_mark("graph_written", formulas=len(formulas), cycles=len(cycles))
    return cycles, doc, iterate


def _stream_graph_files(
    dest_dir,
    doc: GraphDocument,
    formulas: dict[str, _Formula] | None = None,
) -> None:
    """Write graph.json with json.dump. Links are streamed when the caller kept them columnar."""
    from finance_context.render.graph import _cell, render_graph_markdown

    if formulas is None:
        payload = doc.model_dump(mode="json", by_alias=True)
        write_json(dest_dir / "graph" / "graph.json", payload, stream=True)
        markdown = render_graph_markdown(doc)
        atomic_write_bytes(dest_dir / "graph" / "graph.md", markdown.encode("utf-8"))
        return
    n_links = len(formulas)
    head = doc.model_dump(mode="json", by_alias=True)
    head["links"] = _LINKS_SLOT
    text = json.dumps(head, ensure_ascii=False, indent=2)
    slot = json.dumps(_LINKS_SLOT)
    prefix, suffix = text.split(slot, 1)
    md_head = render_graph_markdown(doc)
    md_head = md_head.replace("- Links: 0\n", f"- Links: {n_links}\n", 1)
    json_tmp = temporary_path(dest_dir / "graph" / "graph.json")
    md_tmp = temporary_path(dest_dir / "graph" / "graph.md")
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
                for cell in sorted(formulas):
                    rec = formulas.pop(cell)
                    formula_class = classify_formula_bits(rec.formula, rec.refs, rec.lag_bits)
                    item = {
                        "cell": cell,
                        "formula": rec.formula,
                        "formula_class": formula_class,
                        "refs": rec.refs,
                        "row_key": rec.row_key,
                        "period_id": rec.period_id,
                    }
                    body = json.dumps(item, ensure_ascii=False, indent=2).replace("\n", "\n    ")
                    json_file.write(("" if first else ",\n") + "    " + body)
                    first = False
                    md_file.write(
                        "| "
                        + " | ".join(
                            [
                                _cell(cell),
                                _cell(rec.row_key or ""),
                                _cell(rec.period_id or ""),
                                _cell(formula_class or ""),
                                _cell(rec.formula or ""),
                                _cell(", ".join(rec.refs)),
                            ]
                        )
                        + " |\n"
                    )
                json_file.write("\n  ]")
            json_file.write(suffix + "\n")
            json_file.flush()
            os.fsync(json_file.fileno())
            md_file.flush()
            os.fsync(md_file.fileno())
        atomic_replace(json_tmp, dest_dir / "graph" / "graph.json")
        atomic_replace(md_tmp, dest_dir / "graph" / "graph.md")
    except BaseException:
        json_tmp.unlink(missing_ok=True)
        md_tmp.unlink(missing_ok=True)
        raise
    json_tmp.unlink(missing_ok=True)
    md_tmp.unlink(missing_ok=True)
    doc.links = []
