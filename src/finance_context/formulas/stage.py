from __future__ import annotations

import json
from pathlib import Path

from finance_context.formulas.csr import build_csr
from finance_context.formulas.engine import FormulaEngine
from finance_context.formulas.models import CompileResult, Edge
from finance_context.store.fs import read_parquet, write_parquet

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


def compile_workbook(dest_dir: Path) -> CompileResult:
    meta = json.loads((dest_dir / "raw" / "workbook.json").read_text(encoding="utf-8"))
    engine = FormulaEngine(locale_hint=meta.get("locale_hint"))
    raw_rows = read_parquet(dest_dir / "raw" / "cells.parquet")
    ir_rows: list[tuple[object, ...]] = []
    edges: list[Edge] = []
    extra_nodes: list[str] = []
    for row in raw_rows:
        extra_nodes.append(f"{row['sheet']}!{row['addr']}")
        formula = row.get("formula_raw")
        template = None
        unparsed = False
        ast_json = None
        if formula:
            parsed = engine.parse(str(formula), sheet=row["sheet"], addr=row["addr"])
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
    csr = build_csr(edges, extra_nodes=extra_nodes)
    write_parquet(dest_dir / "ir" / "cells.parquet", IR_CELL_COLUMNS, ir_rows)
    write_parquet(
        dest_dir / "ir" / "edges.parquet",
        IR_EDGE_COLUMNS,
        [(e.source, e.kind, e.target, e.unresolved, e.truncated) for e in edges],
    )
    return CompileResult(csr=csr)
