from __future__ import annotations

import hashlib
import json
import zlib
from pathlib import Path

from finance_context.excel.a1 import format_addr, parse_addr
from finance_context.formulas.csr import (
    active_range_node_min_cells,
    expand_cell_edges,
    expand_cell_edges_column_batches,
)
from finance_context.formulas.engine import FormulaEngine, shift_parsed
from finance_context.formulas.models import (
    CompileResult,
    Edge,
    IrCell,
    ParsedFormula,
    ast_json_text,
    cell_parquet_row,
)
from finance_context.formulas.names import resolve_defined_name_edges
from finance_context.memory import usage_for_book
from finance_context.settings import Settings
from finance_context.store.fs import (
    ColumnRows,
    RowGroupWriter,
    read_parquet,
    write_json,
    write_parquet,
)

COMPILE_FILES = ("cells.parquet", "edges.parquet", "cell_edges.parquet")

IR_CELL_COLUMNS = (
    ("sheet", "VARCHAR"),
    ("row", "INTEGER"),
    ("col", "INTEGER"),
    ("addr", "VARCHAR"),
    ("formula_raw", "VARCHAR"),
    ("formula_template", "VARCHAR"),
    ("unparsed", "BOOLEAN"),
    ("cached_value", "VARCHAR"),
    ("hidden", "BOOLEAN"),
    ("number_format", "VARCHAR"),
    ("comment", "VARCHAR"),
    ("ast_json", "VARCHAR"),
)

IR_EDGE_COLUMNS = (
    ("source", "VARCHAR"),
    ("kind", "VARCHAR"),
    ("target", "VARCHAR"),
    ("unresolved", "BOOLEAN"),
    ("truncated", "BOOLEAN"),
    ("named", "BOOLEAN"),
)

IR_CELL_EDGE_COLUMNS = (
    ("source", "VARCHAR"),
    ("target", "VARCHAR"),
    ("kind", "VARCHAR"),
    ("unresolved", "BOOLEAN"),
    ("truncated", "BOOLEAN"),
    ("dangling", "BOOLEAN"),
    ("col_offset", "INTEGER"),
    ("period_lag", "VARCHAR"),
    ("dangling_reason", "VARCHAR"),
    ("status", "VARCHAR"),
    ("reason", "VARCHAR"),
    ("evidence", "VARCHAR"),
    ("range_ref", "VARCHAR"),
    ("abs_col", "BOOLEAN"),
    ("abs_row", "BOOLEAN"),
    ("abs_col_end", "BOOLEAN"),
    ("abs_row_end", "BOOLEAN"),
    ("named", "BOOLEAN"),
)


def canonical_node_id(sheet: str, addr: str) -> str:
    try:
        col, row = parse_addr(str(addr))
    except ValueError:
        return f"{sheet}!{addr}"
    return f"{sheet}!{format_addr(col, row)}"


def compile_workbook(
    dest_dir: Path,
    *,
    usage: object | None = None,
    settings: object | None = None,
) -> CompileResult:
    chosen = settings if isinstance(settings, Settings) else Settings()
    meta = json.loads((dest_dir / "raw" / "workbook.json").read_text(encoding="utf-8"))
    engine = FormulaEngine(locale_hint=meta.get("locale_hint"))
    raw_path = dest_dir / "raw" / "cells.parquet"
    if chosen.parse_streaming:
        cells, edges, extra_nodes, formula_count, pool = _compile_cells_streaming(
            dest_dir, raw_path, engine, meta, chosen
        )
    else:
        cells, edges, extra_nodes, formula_count, pool = _compile_cells_loaded(
            raw_path, engine, meta, chosen
        )
    known = set(extra_nodes)
    sheets = {
        str(item["name"] if isinstance(item, dict) else item.name)
        for item in (meta.get("sheets") or [])
    }
    presence = _presence_index(dest_dir, columnar=chosen.parse_streaming, pool=pool)
    if usage is None:
        usage = usage_for_book(dest_dir)
    if chosen.graph_stream:
        cell_edges: list | EdgeCount = _write_cell_edge_batches(
            dest_dir,
            edges,
            known,
            sheets=sheets,
            presence=presence,
            usage=usage,
            pool=pool,
            row_group_rows=chosen.parquet_row_group_rows,
        )
    else:
        cell_edges = expand_cell_edges(
            edges,
            known,
            known_sheets=sheets,
            presence=presence,
            usage=usage,
            pool=pool,
        )
    if pool is not None:
        pool.clear()
    if chosen.parse_streaming:
        _write_edges(dest_dir, edges, settings=chosen)
    else:
        _write_cells_and_edges(dest_dir, cells, edges, settings=chosen)
    if not chosen.graph_stream:
        _write_cell_edge_rows(
            dest_dir,
            cell_edges,
            row_group_rows=chosen.parquet_row_group_rows,
        )
    write_json(
        dest_dir / "ir" / "compile.json",
        {"schema_id": compile_schema_id(), "files": list(COMPILE_FILES)},
        stream=chosen.json_stream,
    )
    return CompileResult(cells=cells, edges=edges, cell_edges=cell_edges)


def _compile_cells_loaded(
    raw_path: Path,
    engine: FormulaEngine,
    meta: dict,
    chosen: Settings,
) -> tuple[list[IrCell], list[Edge], list[str], int, dict[str, str] | None]:
    raw_rows = read_parquet(raw_path)
    cells: list[IrCell] = []
    edges: list[Edge] = []
    extra_nodes: list[str] = []
    formula_count = 0
    pool: dict[str, str] | None = {} if chosen.compile_string_pool else None
    for row, (parsed, release) in zip(raw_rows, _iter_parsed(engine, raw_rows), strict=True):
        cell, added = _take_cell(
            row, parsed, release, pool=pool, edges=edges, extra_nodes=extra_nodes, chosen=chosen
        )
        if added:
            formula_count += 1
        cells.append(cell)
    _intern_resolved_edges(edges, meta.get("defined_names"), pool)
    return cells, edges, extra_nodes, formula_count, pool


def _compile_cells_streaming(
    dest_dir: Path,
    raw_path: Path,
    engine: FormulaEngine,
    meta: dict,
    chosen: Settings,
) -> tuple[list[IrCell], list[Edge], list[str], int, dict[str, str] | None]:
    """Read raw cells in chunks and publish ``ir/cells.parquet`` from the batch."""
    from finance_context.store.duckdb_io import DuckDBBatch, limited_connection

    cells_path = dest_dir / "ir" / "cells.parquet"
    batch = DuckDBBatch(Path(str(cells_path) + ".build.duckdb"), IR_CELL_COLUMNS)
    cells: list[IrCell] = []
    edges: list[Edge] = []
    extra_nodes: list[str] = []
    formula_count = 0
    pool: dict[str, str] | None = {} if chosen.compile_string_pool else None
    defined_names = meta.get("defined_names")
    try:
        with limited_connection() as reader:
            cursor = reader.execute("SELECT * FROM read_parquet(?)", [str(raw_path)])
            names = [item[0] for item in cursor.description]
            while True:
                fetched = cursor.fetchmany(2048)
                if not fetched:
                    break
                raw_rows = [dict(zip(names, record, strict=True)) for record in fetched]
                edge_at = len(edges)
                chunk: list[tuple[object, ...]] = []
                for row, (parsed, release) in zip(
                    raw_rows, _iter_parsed(engine, raw_rows), strict=True
                ):
                    cell, added = _take_cell(
                        row,
                        parsed,
                        release,
                        pool=pool,
                        edges=edges,
                        extra_nodes=extra_nodes,
                        chosen=chosen,
                    )
                    if added:
                        formula_count += 1
                    cells.append(cell)
                    chunk.append(cell_parquet_row(cell))
                _intern_resolved_edges(edges[edge_at:], defined_names, pool)
                batch.add_rows(chunk)
            batch.finish(cells_path, chosen.parquet_row_group_rows)
    except Exception:
        batch.abort()
        raise
    return cells, edges, extra_nodes, formula_count, pool


def _take_cell(
    row: dict,
    parsed: ParsedFormula | None,
    release,
    *,
    pool: dict[str, str] | None,
    edges: list[Edge],
    extra_nodes: list[str],
    chosen: Settings,
) -> tuple[IrCell, bool]:
    added = bool(row.get("formula_raw"))
    node_id = canonical_node_id(str(row["sheet"]), str(row["addr"]))
    if pool is not None:
        node_id = pool.setdefault(node_id, node_id)
        if parsed is not None:
            for edge in parsed.edges:
                edge.source = pool.setdefault(edge.source, edge.source)
                if edge.target:
                    edge.target = pool.setdefault(edge.target, edge.target)
    extra_nodes.append(node_id)
    template = None
    unparsed = False
    ast_json = None
    if parsed is not None:
        template = parsed.template
        unparsed = parsed.unparsed
        if parsed.ast is not None:
            ast_json = json.dumps(parsed.ast, ensure_ascii=False)
            if chosen.compile_ast_zlib:
                ast_json = zlib.compress(ast_json.encode("utf-8"), 1)
        edges.extend(parsed.edges)
    release()
    sheet = row["sheet"]
    addr = row["addr"]
    return (
        IrCell(
            sheet=sheet if isinstance(sheet, str) else str(sheet),
            row=int(row["row"]),
            col=int(row["col"]),
            addr=addr if isinstance(addr, str) else str(addr),
            formula_raw=row.get("formula_raw"),
            formula_template=template,
            unparsed=unparsed,
            cached_value=row.get("cached_value"),
            hidden=row.get("hidden"),
            number_format=row.get("number_format"),
            comment=row.get("comment"),
            ast_json=ast_json,
        ),
        added,
    )


def _intern_resolved_edges(
    edges: list[Edge], defined_names: list | None, pool: dict[str, str] | None
) -> None:
    resolve_defined_name_edges(edges, defined_names)
    if pool is None:
        return
    for edge in edges:
        edge.source = pool.setdefault(edge.source, edge.source)
        if edge.target:
            edge.target = pool.setdefault(edge.target, edge.target)


def _iter_parsed(engine: FormulaEngine, raw_rows: list[dict]):
    """Parse each shared master once, and drop its AST after the last member.

    A zero offset reuses the master AST object. That object stays until every
    member of the group has been serialized. Each formula's own edge list is
    cleared as soon as the caller has copied it.
    """
    groups: dict[tuple[str, int], list[int]] = {}
    masters: dict[tuple[str, int], int] = {}
    for index, row in enumerate(raw_rows):
        key = _shared_key(row)
        if key is None:
            continue
        groups.setdefault(key, []).append(index)
        if row.get("shared_master") and key not in masters:
            masters[key] = index
    cache: dict[tuple[str, int], ParsedFormula] = {}
    remaining = {key: len(indexes) for key, indexes in groups.items() if key in masters}

    def master_of(key: tuple[str, int]) -> ParsedFormula:
        cached = cache.get(key)
        if cached is not None:
            return cached
        master = raw_rows[masters[key]]
        parsed = engine.parse(
            str(master["formula_raw"]),
            sheet=str(master["sheet"]),
            addr=str(master["addr"]),
        )
        cache[key] = parsed
        return parsed

    for index, row in enumerate(raw_rows):
        key = _shared_key(row)
        if key is not None and key in masters:
            master_parsed = master_of(key)
            master_row = raw_rows[masters[key]]
            if index == masters[key]:
                parsed = master_parsed
            else:
                parsed = shift_parsed(
                    master_parsed,
                    sheet=str(row["sheet"]),
                    from_col=int(master_row["col"]),
                    from_row=int(master_row["row"]),
                    to_col=int(row["col"]),
                    to_row=int(row["row"]),
                    sep=engine.sep,
                    decimal=engine.decimal,
                )
            remaining[key] -= 1
            yield parsed, _release_parsed(parsed, master_parsed, key, cache, remaining[key] == 0)
            continue
        formula = row.get("formula_raw")
        if not formula:
            yield None, _release_nothing
            continue
        parsed = engine.parse(str(formula), sheet=str(row["sheet"]), addr=str(row["addr"]))
        yield parsed, _release_parsed(parsed, parsed, None, cache, True)


def _shared_key(row: dict) -> tuple[str, int] | None:
    si = row.get("shared_si")
    if si is None or not row.get("formula_raw"):
        return None
    return str(row["sheet"]), int(si)


def _release_nothing() -> None:
    return None


def _release_parsed(
    parsed: ParsedFormula,
    master: ParsedFormula,
    key: tuple[str, int] | None,
    cache: dict[tuple[str, int], ParsedFormula],
    last: bool,
):
    shared = parsed.ast is master.ast

    def release() -> None:
        parsed.edges.clear()
        if not shared or last:
            parsed.ast = None
        if last:
            master.ast = None
            if master is not parsed:
                master.edges.clear()
            if key is not None:
                cache.pop(key, None)

    return release


def compile_schema_id() -> str:
    names = [
        name
        for columns in (IR_CELL_COLUMNS, IR_EDGE_COLUMNS, IR_CELL_EDGE_COLUMNS)
        for name, _dtype in columns
    ]
    # RANGE_NODE_MIN_CELLS decides which ranges compile to one node edge, so IR
    # compiled under another threshold is stale.
    node_min = f"range-node-min={active_range_node_min_cells()}"
    payload = "\n".join([*names, "name-edges-1", "range-nodes-2", node_min])
    return hashlib.sha256(payload.encode()).hexdigest()


def ir_is_current(dest_dir: Path) -> bool:
    ir = dest_dir / "ir"
    if not all((ir / name).is_file() for name in COMPILE_FILES):
        return False
    path = ir / "compile.json"
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    return payload.get("schema_id") == compile_schema_id() and list(
        payload.get("files") or []
    ) == list(COMPILE_FILES)


class EdgeCount:
    """Length of cell edges that were written and dropped."""

    __slots__ = ("_n",)

    def __init__(self, count: int) -> None:
        self._n = count

    def __len__(self) -> int:
        return self._n

    def __bool__(self) -> bool:
        return self._n > 0


def _write_cell_edge_batches(
    dest_dir: Path,
    edges: list[Edge],
    known: set[str],
    *,
    sheets: set[str],
    presence: dict[str, str],
    usage: object,
    pool: dict[str, str] | None,
    row_group_rows: int,
) -> EdgeCount:
    path = dest_dir / "ir" / "cell_edges.parquet"
    names = [name for name, _dtype in IR_CELL_EDGE_COLUMNS]
    with RowGroupWriter(path, IR_CELL_EDGE_COLUMNS, row_group_rows) as writer:

        def consume(columns: dict[str, list]) -> None:
            writer.write_lists([columns[name] for name in names])

        count = expand_cell_edges_column_batches(
            edges,
            known,
            known_sheets=sheets,
            presence=presence,
            usage=usage,  # type: ignore[arg-type]
            pool=pool,
            consume=consume,
            batch_size=row_group_rows,
        )
    return EdgeCount(count)


def _write_cell_edge_rows(dest_dir: Path, cell_edges: list, *, row_group_rows: int) -> None:
    rows = [
        (
            edge.source,
            edge.target,
            edge.kind,
            edge.unresolved,
            edge.truncated,
            edge.dangling,
            None,
            None,
            edge.dangling_reason,
            edge.status,
            edge.reason,
            edge.evidence,
            edge.range_ref,
            edge.abs_col,
            edge.abs_row,
            edge.abs_col_end,
            edge.abs_row_end,
            edge.named,
        )
        for edge in cell_edges
    ]
    write_parquet(
        dest_dir / "ir" / "cell_edges.parquet",
        IR_CELL_EDGE_COLUMNS,
        rows,
        row_group_rows=row_group_rows,
    )


def _write_cells_and_edges(
    dest_dir: Path, cells: list[IrCell], edges: list[Edge], *, settings: Settings
) -> None:
    row_group_rows = settings.parquet_row_group_rows
    if row_group_rows > 0 and len(cells) > row_group_rows:
        _write_object_batches(
            dest_dir / "ir" / "cells.parquet",
            IR_CELL_COLUMNS,
            cells,
            _cell_column_getters(),
            row_group_rows,
        )
    else:
        write_parquet(
            dest_dir / "ir" / "cells.parquet",
            IR_CELL_COLUMNS,
            [cell_parquet_row(cell) for cell in cells],
            row_group_rows=0,
        )
    _write_edges(dest_dir, edges, settings=settings)


def _write_edges(dest_dir: Path, edges: list[Edge], *, settings: Settings) -> None:
    row_group_rows = settings.parquet_row_group_rows
    if row_group_rows > 0 and len(edges) > row_group_rows:
        _write_object_batches(
            dest_dir / "ir" / "edges.parquet",
            IR_EDGE_COLUMNS,
            edges,
            [lambda edge, name=name: getattr(edge, name) for name, _dtype in IR_EDGE_COLUMNS],
            row_group_rows,
        )
    else:
        write_parquet(
            dest_dir / "ir" / "edges.parquet",
            IR_EDGE_COLUMNS,
            [
                (edge.source, edge.kind, edge.target, edge.unresolved, edge.truncated, edge.named)
                for edge in edges
            ],
            row_group_rows=0,
        )


def _cell_column_getters():
    getters = []
    for name, _dtype in IR_CELL_COLUMNS:
        if name == "ast_json":
            getters.append(lambda cell: ast_json_text(cell.ast_json))
        else:
            getters.append(lambda cell, field=name: getattr(cell, field))
    return getters


def _write_object_batches(path, columns, objs, getters, row_group_rows: int) -> None:
    with RowGroupWriter(path, columns, row_group_rows) as writer:
        for start in range(0, len(objs), row_group_rows):
            part = objs[start : start + row_group_rows]
            writer.write_lists([[getter(obj) for obj in part] for getter in getters])


def _presence_index(
    dest_dir: Path,
    *,
    columnar: bool = False,
    pool: dict[str, str] | None = None,
) -> dict[str, str]:
    path = dest_dir / "raw" / "cell_presence.parquet"
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    rows = ColumnRows(path, dedup=("sheet", "presence")) if columnar else read_parquet(path)
    for row in rows:
        node = canonical_node_id(str(row["sheet"]), str(row["addr"]))
        if pool is not None:
            node = pool.setdefault(node, node)
        out[node] = str(row["presence"])
    return out
