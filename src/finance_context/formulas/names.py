"""One-level resolution of defined names into formula edges."""

from __future__ import annotations

from typing import Any

from finance_context.excel.a1 import format_addr
from finance_context.formulas.engine import FormulaEngine
from finance_context.formulas.models import Edge


def resolve_defined_name_edges(
    edges: list[Edge], defined_names: list[Any] | None
) -> None:
    """Point a name edge at its local cell or range, or mark an external / broken name.

    The formula text is left unchanged. Another name, a column-only range, or a
    formula that is not one cell or one range stays an unresolved name token.
    """
    table = _name_table(defined_names)
    if not table:
        return
    engine = FormulaEngine()
    for edge in edges:
        if not _is_open_name(edge):
            continue
        formula = table.get(str(edge.target).casefold())
        if formula is None or "#REF!" in formula:
            continue
        if "[" in formula:
            edge.kind = "external"
            edge.target = formula[1:] if formula.startswith("=") else formula
            edge.unresolved = False
            continue
        _resolve_local(edge, formula, engine)


def _name_table(defined_names: list[Any] | None) -> dict[str, str]:
    table: dict[str, str] = {}
    for item in defined_names or []:
        if isinstance(item, dict):
            name = item.get("name")
            formula = item.get("formula")
        else:
            name = getattr(item, "name", None)
            formula = getattr(item, "formula", None)
        if name and formula is not None:
            table[str(name).casefold()] = str(formula)
    return table


def _is_open_name(edge: Edge) -> bool:
    target = edge.target or ""
    return bool(
        edge.named
        and edge.unresolved
        and edge.kind == "ref"
        and target
        and "!" not in target
        and ":" not in target
    )


def _resolve_local(edge: Edge, formula: str, engine: FormulaEngine) -> None:
    text = formula if formula.startswith("=") else f"={formula}"
    parsed = engine.parse(text, sheet="_", addr="A1")
    ast = parsed.ast
    if parsed.unparsed or not isinstance(ast, dict) or ast.get("external"):
        return
    if ast.get("op") == "ref" and ast.get("sheet"):
        edge.kind = "ref"
        edge.target = f"{ast['sheet']}!{format_addr(int(ast['col']), int(ast['row']))}"
        edge.unresolved = False
        edge.abs_col = bool(ast.get("abs_col"))
        edge.abs_row = bool(ast.get("abs_row"))
        return
    if ast.get("op") != "range" or ast.get("named") or not ast.get("sheet"):
        return
    start = ast.get("start")
    end = ast.get("end")
    if (
        not isinstance(start, dict)
        or not isinstance(end, dict)
        or "col" not in start
        or "row" not in start
        or "col" not in end
        or "row" not in end
    ):
        return
    edge.kind = "range"
    edge.target = (
        f"{ast['sheet']}!{format_addr(int(start['col']), int(start['row']))}"
        f":{format_addr(int(end['col']), int(end['row']))}"
    )
    edge.unresolved = False
    edge.named = True
    edge.abs_col = bool(start.get("abs_col"))
    edge.abs_row = bool(start.get("abs_row"))
    edge.abs_col_end = bool(end.get("abs_col"))
    edge.abs_row_end = bool(end.get("abs_row"))
