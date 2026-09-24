from __future__ import annotations

import hashlib
import json
from pathlib import Path

from finance_context.excel.a1 import format_addr, parse_addr
from finance_context.formulas.csr import build_csr, expand_cell_edges
from finance_context.formulas.engine import FormulaEngine, shift_parsed
from finance_context.formulas.models import CompileResult, Edge, ParsedFormula
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


def compile_workbook(dest_dir: Path) -> CompileResult:
    meta = json.loads((dest_dir / "raw" / "workbook.json").read_text(encoding="utf-8"))
    engine = FormulaEngine(locale_hint=meta.get("locale_hint"))
    raw_rows = read_parquet(dest_dir / "raw" / "cells.parquet")
    ir_rows: list[tuple[object, ...]] = []
    edges: list[Edge] = []
    extra_nodes: list[str] = []
    parsed_rows = _parse_rows(engine, raw_rows)
    for row, parsed in zip(raw_rows, parsed_rows, strict=True):
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
        ir_rows.append(
            (
                row["sheet"],
                row["row"],
                row["col"],
                row["addr"],
                row.get("formula_raw"),
                template,
                unparsed,
                row.get("cached_value"),
                row.get("hidden"),
                row.get("number_format"),
                row.get("comment"),
                ast_json,
            )
        )
    known = set(extra_nodes)
    sheets = {
        str(item["name"] if isinstance(item, dict) else item.name)
        for item in (meta.get("sheets") or [])
    }
    presence = _presence_index(dest_dir)
    csr = build_csr(edges, extra_nodes=extra_nodes)
    cell_edges = expand_cell_edges(
        edges, known, known_sheets=sheets, presence=presence
    )
    edge_rows = [(e.source, e.kind, e.target, e.unresolved, e.truncated) for e in edges]
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
    write_parquet(dest_dir / "ir" / "cells.parquet", IR_CELL_COLUMNS, ir_rows)
    write_parquet(dest_dir / "ir" / "edges.parquet", IR_EDGE_COLUMNS, edge_rows)
    write_parquet(dest_dir / "ir" / "cell_edges.parquet", IR_CELL_EDGE_COLUMNS, cell_edge_rows)
    write_json(
        dest_dir / "ir" / "compile.json",
        {"schema_id": compile_schema_id(), "files": list(COMPILE_FILES)},
    )
    return CompileResult(
        csr=csr,
        cells=_as_dicts(IR_CELL_COLUMNS, ir_rows),
        edges=_as_dicts(IR_EDGE_COLUMNS, edge_rows),
        cell_edges=_as_dicts(IR_CELL_EDGE_COLUMNS, cell_edge_rows),
    )


def _parse_rows(engine: FormulaEngine, raw_rows: list[dict]) -> list[ParsedFormula | None]:
    """Parse each shared-formula master once and shift that AST onto the group."""
    parsed: list[ParsedFormula | None] = [None] * len(raw_rows)
    groups: dict[tuple[str, int], list[int]] = {}
    masters: dict[tuple[str, int], int] = {}
    for index, row in enumerate(raw_rows):
        si = row.get("shared_si")
        if si is None or not row.get("formula_raw"):
            continue
        key = (str(row["sheet"]), int(si))
        groups.setdefault(key, []).append(index)
        if row.get("shared_master") and key not in masters:
            masters[key] = index
    claimed: set[int] = set()
    for key, indexes in groups.items():
        master_i = masters.get(key)
        if master_i is None:
            continue
        master = raw_rows[master_i]
        master_parsed = engine.parse(
            str(master["formula_raw"]),
            sheet=str(master["sheet"]),
            addr=str(master["addr"]),
        )
        parsed[master_i] = master_parsed
        claimed.add(master_i)
        for index in indexes:
            if index == master_i:
                continue
            row = raw_rows[index]
            parsed[index] = shift_parsed(
                master_parsed,
                sheet=str(row["sheet"]),
                from_col=int(master["col"]),
                from_row=int(master["row"]),
                to_col=int(row["col"]),
                to_row=int(row["row"]),
                sep=engine.sep,
                decimal=engine.decimal,
            )
            claimed.add(index)
    for index, row in enumerate(raw_rows):
        if index in claimed:
            continue
        formula = row.get("formula_raw")
        if not formula:
            continue
        parsed[index] = engine.parse(
            str(formula), sheet=str(row["sheet"]), addr=str(row["addr"])
        )
    return parsed


def _as_dicts(
    columns: tuple[tuple[str, str], ...], rows: list[tuple[object, ...]]
) -> list[dict[str, object]]:
    names = [name for name, _dtype in columns]
    return [dict(zip(names, row, strict=True)) for row in rows]


def compile_schema_id() -> str:
    names = [
        name
        for columns in (IR_CELL_COLUMNS, IR_EDGE_COLUMNS, IR_CELL_EDGE_COLUMNS)
        for name, _dtype in columns
    ]
    return hashlib.sha256("\n".join(names).encode()).hexdigest()


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
