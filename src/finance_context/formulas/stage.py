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
from finance_context.formulas.known import KnownNodes
from finance_context.formulas.models import (
    CompileResult,
    Edge,
    IrCell,
    ParsedFormula,
    ast_json_text,
    cell_parquet_row,
)
from finance_context.formulas.names import resolve_defined_name_edges
from finance_context.memory import load_presence, trim_process_heap, usage_from_presence
from finance_context.settings import Settings
from finance_context.store.fs import (
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
    spill: Path | None = None
    if chosen.parse_streaming:
        cell_count, spill, edge_count, known, _formula_count, pool = _compile_cells_streaming(
            dest_dir, raw_path, engine, meta, chosen
        )
    else:
        cells, edges, known, formula_count, pool = _compile_cells_loaded(
            raw_path, engine, meta, chosen
        )
        cell_count = len(cells)
        edge_count = len(edges)
    sheets = {
        str(item["name"] if isinstance(item, dict) else item.name)
        for item in (meta.get("sheets") or [])
    }
    presence = load_presence(dest_dir / "raw" / "cell_presence.parquet", pool=pool)
    if usage is None:
        usage = usage_from_presence(presence)
    try:
        if spill is not None and chosen.graph_stream:
            # The spill is the edge list. Expand from it, then publish edges.
            cell_edges = _expand_spilled_edges(
                dest_dir,
                spill,
                edge_count,
                known,
                sheets=sheets,
                presence=presence,
                usage=usage,
                settings=chosen,
            )
            edges = []
        elif chosen.graph_stream:
            cell_edges = _write_cell_edge_batches(
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
            if spill is not None:
                edges = list(_iter_spilled_edges(spill))
                edge_count = len(edges)
            cell_edges = expand_cell_edges(
                edges,
                known,
                known_sheets=sheets,
                presence=presence,
                usage=usage,
                pool=pool,
            )
            if spill is not None:
                _write_edges(dest_dir, edges, settings=chosen)
        if pool is not None:
            pool.clear()
        if spill is None and not chosen.parse_streaming:
            _write_cells_and_edges(dest_dir, cells, edges, settings=chosen)
            del cells
        elif spill is None:
            _write_edges(dest_dir, edges, settings=chosen)
        if not chosen.graph_stream:
            _write_cell_edge_rows(
                dest_dir,
                cell_edges,
                row_group_rows=chosen.parquet_row_group_rows,
            )
    finally:
        if spill is not None:
            spill.unlink(missing_ok=True)
    write_json(
        dest_dir / "ir" / "compile.json",
        {"schema_id": compile_schema_id(), "files": list(COMPILE_FILES)},
        stream=chosen.json_stream,
    )
    if spill is None:
        edge_count = len(edges)
        edges.clear()
    trim_process_heap()
    return CompileResult(
        cells=[],
        edges=[],
        cell_edges=cell_edges,
        cell_count=cell_count,
        edge_count=edge_count,
        usage=usage,
    )


def _compile_cells_loaded(
    raw_path: Path,
    engine: FormulaEngine,
    meta: dict,
    chosen: Settings,
) -> tuple[list[IrCell], list[Edge], KnownNodes, int, dict[str, str] | None]:
    raw_rows = read_parquet(raw_path)
    cells: list[IrCell] = []
    edges: list[Edge] = []
    known = KnownNodes()
    formula_count = 0
    pool: dict[str, str] | None = {} if chosen.compile_string_pool else None
    for row, (parsed, release) in zip(raw_rows, _iter_parsed(engine, raw_rows), strict=True):
        cell, added = _take_cell(
            row, parsed, release, pool=pool, edges=edges, known=known, chosen=chosen
        )
        if added:
            formula_count += 1
        cells.append(cell)
    _intern_resolved_edges(edges, meta.get("defined_names"), pool)
    return cells, edges, known, formula_count, pool


# Published edges plus the absolute-reference bits expand needs and parquet does not keep.
_SPILL_COLUMNS = (
    *IR_EDGE_COLUMNS,
    ("abs_col", "BOOLEAN"),
    ("abs_row", "BOOLEAN"),
    ("abs_col_end", "BOOLEAN"),
    ("abs_row_end", "BOOLEAN"),
)


def _compile_cells_streaming(
    dest_dir: Path,
    raw_path: Path,
    engine: FormulaEngine,
    meta: dict,
    chosen: Settings,
) -> tuple[int, Path, int, KnownNodes, int, dict[str, str] | None]:
    """Read raw cells in chunks. Cells and formula edges both leave memory with the chunk.

    Edges go to a spill file, not ``ir/edges.parquet``: expansion still has to
    set ``truncated``, and that flag is written with the published file.
    """
    from finance_context.store.duckdb_io import DuckDBBatch, limited_connection

    cells_path = dest_dir / "ir" / "cells.parquet"
    spill_path = dest_dir / "ir" / ".edges-spill.parquet"
    batch = DuckDBBatch(Path(str(cells_path) + ".build.duckdb"), IR_CELL_COLUMNS)
    spill = RowGroupWriter(spill_path, _SPILL_COLUMNS, chosen.parquet_row_group_rows)
    known = KnownNodes()
    formula_count = 0
    cell_count = 0
    edge_count = 0
    pool: dict[str, str] | None = {} if chosen.compile_string_pool else None
    defined_names = meta.get("defined_names")
    published = False
    try:
        try:
            with limited_connection(raw_path) as reader:
                cursor = reader.execute("SELECT * FROM read_parquet(?)", [str(raw_path)])
                names = [item[0] for item in cursor.description]
                while True:
                    fetched = cursor.fetchmany(2048)
                    if not fetched:
                        break
                    raw_rows = [dict(zip(names, record, strict=True)) for record in fetched]
                    chunk_edges: list[Edge] = []
                    chunk: list[tuple[object, ...]] = []
                    for row, (parsed, release) in zip(
                        raw_rows, _iter_parsed(engine, raw_rows), strict=True
                    ):
                        cell, added = _take_cell(
                            row,
                            parsed,
                            release,
                            pool=pool,
                            edges=chunk_edges,
                            known=known,
                            chosen=chosen,
                        )
                        if added:
                            formula_count += 1
                        cell_count += 1
                        chunk.append(cell_parquet_row(cell))
                    _intern_resolved_edges(chunk_edges, defined_names, pool)
                    if chunk_edges:
                        spill.write_lists(
                            _columns_from_rows([_spill_row(edge) for edge in chunk_edges])
                        )
                        edge_count += len(chunk_edges)
                        chunk_edges.clear()
                    if pool is not None:
                        pool.clear()
                    batch.add_rows(chunk)
                batch.finish(cells_path, chosen.parquet_row_group_rows)
        except Exception:
            batch.abort()
            raise
        spill.close()
        published = True
    except Exception:
        if not published:
            spill._abort_tmp()
        spill_path.unlink(missing_ok=True)
        raise
    # Cell chunks and the edge spill are on disk. Drop spare arenas before expand.
    trim_process_heap()
    return cell_count, spill_path, edge_count, known, formula_count, pool


def _take_cell(
    row: dict,
    parsed: ParsedFormula | None,
    release,
    *,
    pool: dict[str, str] | None,
    edges: list[Edge],
    known: KnownNodes,
    chosen: Settings,
) -> tuple[IrCell, bool]:
    added = bool(row.get("formula_raw"))
    sheet = row["sheet"]
    addr = row["addr"]
    sheet_s = sheet if isinstance(sheet, str) else str(sheet)
    addr_s = addr if isinstance(addr, str) else str(addr)
    # Value cells are only a membership bit. Their ids are not pooled.
    if pool is not None and parsed is not None:
        node_id = canonical_node_id(sheet_s, addr_s)
        pool.setdefault(node_id, node_id)
        for edge in parsed.edges:
            edge.source = pool.setdefault(edge.source, edge.source)
            if edge.target:
                edge.target = pool.setdefault(edge.target, edge.target)
    known.add(sheet_s, addr_s)
    template = None
    unparsed = False
    ast_json = None
    if parsed is not None:
        template = parsed.template
        unparsed = parsed.unparsed
        if parsed.ast is not None:
            ast_json = json.dumps(parsed.ast, ensure_ascii=False)
            # Streaming writes the cell before anything else can read the bytes.
            if chosen.compile_ast_zlib and not chosen.parse_streaming:
                ast_json = zlib.compress(ast_json.encode("utf-8"), 1)
        edges.extend(parsed.edges)
    release()
    return (
        IrCell(
            sheet=sheet_s,
            row=int(row["row"]),
            col=int(row["col"]),
            addr=addr_s,
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


# Low bits of a packed cell key. Kind is the cell-edge kind string, not a
# coarser class: ``ref`` and ``range`` to the same target both stay.
_KIND_CODE = {"ref": 1, "range": 2, "cross_sheet": 3, "external": 4, "dynamic": 5}
_KIND_BITS = 4
_ROW_BITS = 24
_COL_BITS = 15  # 1..16384 is XFD; 15 bits still covers it
_ROW_LIMIT = 1 << _ROW_BITS
_COL_LIMIT = 1 << _COL_BITS


def _pack_cell(target: str, sheet_ids: dict[str, int]) -> int | None:
    """Positive key for a canonical ``Sheet!A1``, or None when the text is not one.

    The key matches the target string. ``Sheet!$A$1`` and ``Sheet!A01`` do not
    pack onto ``Sheet!A1``. A range node id contains ``:`` and does not pack.
    """
    if "!" not in target:
        return None
    sheet, body = target.rsplit("!", 1)
    if ":" in body or "$" in body:
        return None
    try:
        col, row = parse_addr(body)
    except ValueError:
        return None
    if format_addr(col, row) != body or not (1 <= col < _COL_LIMIT and 1 <= row < _ROW_LIMIT):
        return None
    sheet_id = sheet_ids.get(sheet)
    if sheet_id is None:
        sheet_id = len(sheet_ids) + 1
        sheet_ids[sheet] = sheet_id
    return (
        (sheet_id << (_COL_BITS + _ROW_BITS + _KIND_BITS))
        | (col << (_ROW_BITS + _KIND_BITS))
        | (row << _KIND_BITS)
    )


class _KeptEdgeWriter:
    """First ``(target, kind)`` of the current source, in emission order.

    Each formula cell is one contiguous run. The live keys are one new set per
    source: a packed positive int for a canonical cell id whose kind fits in
    4 bits, or a ``(target id, kind code)`` pair for any other target or a
    wider kind code. The pair is exact for every code and cannot equal a packed
    int. The set is replaced when the source changes, so its size follows the
    largest formula, not the book.

    A malformed sheet whose formula address comes back after another formula
    starts a new run: a ``(source, target, kind)`` already written for the first
    run is written again. Main's window dropped that repeat. Nothing else about
    the row changes. Parse counts that sheet in file order and context keeps
    the one warning ahead of the 50-line cap.

    Parquet is still DuckDB ``COPY``. One ``write_parquet`` when the table fits
    in a group (or ``row_group_rows <= 0``). Otherwise full groups of
    ``row_group_rows`` go through :class:`RowGroupWriter`.
    """

    def __init__(self, path: Path, row_group_rows: int) -> None:
        self.path = path
        self.row_group_rows = row_group_rows
        self.names = [name for name, _dtype in IR_CELL_EDGE_COLUMNS]
        self.width = len(self.names)
        self.count = 0
        self._buf: list[list] = [[] for _ in range(self.width)]
        self._buf_n = 0
        self._first: list[list] | None = None
        self._writer: RowGroupWriter | None = None
        self._source: str | None = None
        self._keys: set[int | tuple[int, int]] = set()
        self._sheet_ids: dict[str, int] = {}
        self._other_ids: dict[str, int] = {}
        self._extra_kinds: dict[str, int] = {}
        self._key_high = 0

    def _new_keys(self) -> None:
        self._key_high = max(self._key_high, len(self._keys))
        self._keys = set()
        self._sheet_ids = {}
        self._other_ids = {}
        self._extra_kinds = {}

    def _kind_code(self, kind: str) -> int:
        code = _KIND_CODE.get(kind)
        if code is not None:
            return code
        found = self._extra_kinds.get(kind)
        if found is not None:
            return found
        found = len(self._extra_kinds) + len(_KIND_CODE) + 1
        self._extra_kinds[kind] = found
        return found

    def _key(self, target: str, kind: str) -> int | tuple[int, int]:
        kind_code = self._kind_code(kind)
        packed = _pack_cell(target, self._sheet_ids)
        # Four kind bits sit under the packed cell. A wider code, or a target
        # that is not a canonical cell, is a pair. A shifted int would let a
        # large code spill into the target id and drop an edge.
        if packed is not None and kind_code < (1 << _KIND_BITS):
            return packed | kind_code
        other = self._other_ids.get(target)
        if other is None:
            other = len(self._other_ids) + 1
            self._other_ids[target] = other
        return (other, kind_code)

    def consume(self, columns: dict[str, list]) -> None:
        incoming = [columns[name] for name in self.names]
        sources = columns["source"]
        targets = columns["target"]
        kinds = columns["kind"]
        keys = self._keys
        source_now = self._source
        for index in range(len(sources)):
            source = sources[index]
            if source != source_now:
                self._new_keys()
                keys = self._keys
                source_now = source
                self._source = source
            key = self._key(targets[index], kinds[index])
            if key in keys:
                continue
            keys.add(key)
            if len(keys) > self._key_high:
                self._key_high = len(keys)
            for col_index, column in enumerate(incoming):
                self._buf[col_index].append(column[index])
            self._buf_n += 1
            self.count += 1
            self._maybe_flush()

    def _maybe_flush(self) -> None:
        row_group_rows = self.row_group_rows
        if row_group_rows <= 0 or self._buf_n < row_group_rows:
            return
        full = self._buf
        self._buf = [[] for _ in range(self.width)]
        self._buf_n = 0
        if self._writer is not None:
            self._writer.write_lists(full)
            return
        if self._first is None:
            self._first = full
            return
        self._writer = RowGroupWriter(self.path, IR_CELL_EDGE_COLUMNS, row_group_rows)
        self._writer.write_lists(self._first)
        self._writer.write_lists(full)
        self._first = None

    def abort(self) -> None:
        self._new_keys()
        self._buf = []
        self._first = None
        writer = self._writer
        self._writer = None
        if writer is not None and not writer._closed:
            writer.__exit__(Exception, Exception(), None)

    def finish(self) -> int:
        self._new_keys()
        if self.count == 0:
            write_parquet(self.path, IR_CELL_EDGE_COLUMNS, [], row_group_rows=self.row_group_rows)
            return 0
        if self._writer is None:
            rows = _rows_from_columns(self._pending_columns())
            row_group_rows = 0 if self.row_group_rows <= 0 else self.row_group_rows
            write_parquet(self.path, IR_CELL_EDGE_COLUMNS, rows, row_group_rows=row_group_rows)
            return self.count
        if self._buf_n:
            self._writer.write_lists(self._buf)
        self._writer.close()
        self._writer = None
        return self.count

    def _pending_columns(self) -> list[list]:
        if self._first is None:
            return self._buf
        if self._buf_n == 0:
            return self._first
        merged = [list(column) for column in self._first]
        for index, column in enumerate(self._buf):
            merged[index].extend(column)
        return merged


def _rows_from_columns(columns: list[list]) -> list[tuple]:
    if not columns or not columns[0]:
        return []
    width = len(columns)
    return [tuple(columns[col][index] for col in range(width)) for index in range(len(columns[0]))]


def _write_cell_edge_batches(
    dest_dir: Path,
    edges: list[Edge],
    known: set[str] | KnownNodes,
    *,
    sheets: set[str],
    presence: dict[str, str],
    usage: object,
    pool: dict[str, str] | None,
    row_group_rows: int,
) -> EdgeCount:
    path = dest_dir / "ir" / "cell_edges.parquet"
    sink = _KeptEdgeWriter(path, row_group_rows)
    try:
        expand_cell_edges_column_batches(
            edges,
            known,
            known_sheets=sheets,
            presence=presence,
            usage=usage,  # type: ignore[arg-type]
            pool=pool,
            consume=sink.consume,
            batch_size=row_group_rows,
        )
        # Classification is done. finish() writes Parquet through DuckDB, whose
        # 256MB buffer manager sits on top of whatever Python still holds.
        known.clear()
        if pool is not None:
            pool.clear()
        trim_process_heap()
        return EdgeCount(sink.finish())
    except Exception:
        sink.abort()
        raise


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


def _spill_row(edge: Edge) -> tuple[object, ...]:
    return (
        edge.source,
        edge.kind,
        edge.target,
        edge.unresolved,
        edge.truncated,
        edge.named,
        edge.abs_col,
        edge.abs_row,
        edge.abs_col_end,
        edge.abs_row_end,
    )


def _published_edge_row(edge: Edge) -> tuple[object, ...]:
    return (edge.source, edge.kind, edge.target, edge.unresolved, edge.truncated, edge.named)


def _columns_from_rows(rows: list[tuple[object, ...]]) -> list[list[object]]:
    width = len(rows[0])
    columns: list[list[object]] = [[] for _ in range(width)]
    for row in rows:
        for index, value in enumerate(row):
            columns[index].append(value)
    return columns


def _opt_spill_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _iter_spilled_edges(path: Path):
    """One spilled edge at a time, in write order."""
    import pyarrow.parquet as pq

    names = [name for name, _dtype in _SPILL_COLUMNS]
    for batch in pq.ParquetFile(path).iter_batches(columns=names, batch_size=65536):
        columns = [batch.column(index).to_pylist() for index in range(len(names))]
        width = batch.num_rows
        del batch
        for index in range(width):
            target = columns[2][index]
            kind = columns[1][index] or "ref"
            yield Edge(
                kind=kind,  # type: ignore[arg-type]
                source="" if columns[0][index] is None else str(columns[0][index]),
                target=None if target is None else str(target),
                unresolved=bool(columns[3][index]),
                truncated=bool(columns[4][index]),
                named=bool(columns[5][index]),
                abs_col=_opt_spill_bool(columns[6][index]),
                abs_row=_opt_spill_bool(columns[7][index]),
                abs_col_end=_opt_spill_bool(columns[8][index]),
                abs_row_end=_opt_spill_bool(columns[9][index]),
            )
        del columns


def _expand_spilled_edges(
    dest_dir: Path,
    spill: Path,
    edge_count: int,
    known: KnownNodes,
    *,
    sheets: set[str],
    presence: object,
    usage: object,
    settings: Settings,
) -> EdgeCount:
    """Expand formula edges from the spill. A book that fits in one row group is written
    by the same function as a resident list, so its parquet bytes stay the same.
    """
    row_group_rows = settings.parquet_row_group_rows
    fits = row_group_rows <= 0 or edge_count <= row_group_rows
    if fits:
        edges = list(_iter_spilled_edges(spill))
        count = _write_cell_edge_batches(
            dest_dir,
            edges,
            known,
            sheets=sheets,
            presence=presence,  # type: ignore[arg-type]
            usage=usage,
            pool=None,
            row_group_rows=row_group_rows,
        )
        _write_edges(dest_dir, edges, settings=settings)
        edges.clear()
        return count
    path = dest_dir / "ir" / "cell_edges.parquet"
    sink = _KeptEdgeWriter(path, row_group_rows)
    published: list[tuple[object, ...]] = []
    try:
        edges_path = dest_dir / "ir" / "edges.parquet"
        with RowGroupWriter(edges_path, IR_EDGE_COLUMNS, row_group_rows) as writer:

            def flush_published() -> None:
                if not published:
                    return
                writer.write_lists(_columns_from_rows(published))
                published.clear()

            def after_edge(edge: Edge) -> None:
                published.append(_published_edge_row(edge))
                if row_group_rows > 0 and len(published) >= row_group_rows:
                    flush_published()

            expand_cell_edges_column_batches(
                _iter_spilled_edges(spill),
                known,
                consume=sink.consume,
                known_sheets=sheets,
                presence=presence,  # type: ignore[arg-type]
                usage=usage,  # type: ignore[arg-type]
                pool=None,
                batch_size=row_group_rows,
                after_edge=after_edge,
            )
            flush_published()
        known.clear()
        trim_process_heap()
        return EdgeCount(sink.finish())
    except Exception:
        sink.abort()
        raise


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
