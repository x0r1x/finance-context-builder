from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_context.excel.a1 import format_addr, parse_addr
from finance_context.formulas.csr import expand_cell_edges, expanded_edge_count
from finance_context.formulas.engine import FormulaEngine, shift_parsed
from finance_context.formulas.models import (
    CompileResult,
    Edge,
    IrCell,
    ParsedFormula,
    cell_parquet_row,
)
from finance_context.formulas.names import resolve_defined_name_edges
from finance_context.memory import check_budget, usage_for_book
from finance_context.store.fs import read_parquet, write_json, write_parquet

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


def compile_workbook(dest_dir: Path, *, memory_budget: int | None = None) -> CompileResult:
    meta = json.loads((dest_dir / "raw" / "workbook.json").read_text(encoding="utf-8"))
    engine = FormulaEngine(locale_hint=meta.get("locale_hint"))
    raw_rows = read_parquet(dest_dir / "raw" / "cells.parquet")
    cells: list[IrCell] = []
    edges: list[Edge] = []
    extra_nodes: list[str] = []
    formula_count = 0
    for row, (parsed, release) in zip(raw_rows, _iter_parsed(engine, raw_rows), strict=True):
        if row.get("formula_raw"):
            formula_count += 1
        extra_nodes.append(canonical_node_id(str(row["sheet"]), str(row["addr"])))
        template = None
        unparsed = False
        ast_json = None
        if parsed is not None:
            template = parsed.template
            unparsed = parsed.unparsed
            if parsed.ast is not None:
                ast_json = json.dumps(parsed.ast, ensure_ascii=False)
            edges.extend(parsed.edges)
        release()
        sheet = row["sheet"]
        addr = row["addr"]
        cells.append(
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
            )
        )
    resolve_defined_name_edges(edges, meta.get("defined_names"))
    known = set(extra_nodes)
    sheets = {
        str(item["name"] if isinstance(item, dict) else item.name)
        for item in (meta.get("sheets") or [])
    }
    presence = _presence_index(dest_dir)
    usage = usage_for_book(dest_dir)
    check_budget(
        formulas=formula_count,
        expanded_cells=expanded_edge_count(edges, usage=usage),
        budget=memory_budget,
        source="compile",
    )
    cell_edges = expand_cell_edges(
        edges, known, known_sheets=sheets, presence=presence, usage=usage
    )
    edge_rows = [
        (e.source, e.kind, e.target, e.unresolved, e.truncated, e.named) for e in edges
    ]
    cell_edge_rows = [
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
        dest_dir / "ir" / "cells.parquet",
        IR_CELL_COLUMNS,
        [cell_parquet_row(cell) for cell in cells],
    )
    write_parquet(dest_dir / "ir" / "edges.parquet", IR_EDGE_COLUMNS, edge_rows)
    write_parquet(dest_dir / "ir" / "cell_edges.parquet", IR_CELL_EDGE_COLUMNS, cell_edge_rows)
    write_json(
        dest_dir / "ir" / "compile.json",
        {"schema_id": compile_schema_id(), "files": list(COMPILE_FILES)},
    )
    result = CompileResult(cells=cells, edges=edges, cell_edges=cell_edges)
    del edge_rows, cell_edge_rows
    return result


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
    payload = "\n".join([*names, "name-edges-1"])
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


def _presence_index(dest_dir: Path) -> dict[str, str]:
    path = dest_dir / "raw" / "cell_presence.parquet"
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for row in read_parquet(path):
        out[canonical_node_id(str(row["sheet"]), str(row["addr"]))] = str(row["presence"])
    return out
