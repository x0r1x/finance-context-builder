"""One-level resolution of defined names into formula edges."""

from __future__ import annotations

from typing import Any

from finance_context.excel.a1 import format_addr
from finance_context.formulas.engine import FormulaEngine
from finance_context.formulas.models import Edge


def resolve_defined_name_edges(
    edges: list[Edge], defined_names: list[Any] | None
) -> None:
    """Point a name edge at its local cell, or mark an external / broken name.

    The formula text is left unchanged. A local range, another name, or a
    formula that is not one cell stays an unresolved name token.
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
        _resolve_local_cell(edge, formula, engine)


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


def _resolve_local_cell(edge: Edge, formula: str, engine: FormulaEngine) -> None:
    text = formula if formula.startswith("=") else f"={formula}"
    parsed = engine.parse(text, sheet="_", addr="A1")
    ast = parsed.ast
    if (
        parsed.unparsed
        or not isinstance(ast, dict)
        or ast.get("op") != "ref"
        or ast.get("external")
        or not ast.get("sheet")
    ):
        return
    edge.kind = "ref"
    edge.target = f"{ast['sheet']}!{format_addr(int(ast['col']), int(ast['row']))}"
    edge.unresolved = False
    edge.abs_col = bool(ast.get("abs_col"))
    edge.abs_row = bool(ast.get("abs_row"))
