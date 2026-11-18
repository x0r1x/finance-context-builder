"""One-level resolution of defined names into formula edges."""

from __future__ import annotations

from typing import Any

from finance_context.excel.a1 import format_addr
from finance_context.formulas.engine import FormulaEngine
from finance_context.formulas.models import Edge


class NameIndex:
    """Workbook names and sheet-local names, built once and reused for every chunk.

    A name on the formula's sheet hides the workbook name. A later definition
    replaces an earlier one only inside the same scope.
    """

    def __init__(self, defined_names: list[Any] | None) -> None:
        self._local: dict[tuple[str, str], str] = {}
        self._workbook: dict[str, str] = {}
        self._engine: FormulaEngine | None = None
        self._parsed: dict[str, dict[str, Any] | None] = {}
        for item in defined_names or []:
            fields = _defined_fields(item)
            if fields is None:
                continue
            name, formula, scope = fields
            key = name.casefold()
            if scope:
                self._local[(scope.casefold(), key)] = formula
            else:
                self._workbook[key] = formula

    def apply(self, edge: Edge) -> None:
        if not _is_open_name(edge):
            return
        found = self._formula(str(edge.target), _source_sheet(edge.source))
        if found is None:
            return
        formula, local = found
        if "#REF!" in formula:
            return
        if "[" in formula:
            edge.kind = "external"
            edge.target = formula[1:] if formula.startswith("=") else formula
            edge.unresolved = False
            return
        ast = self._ast(formula)
        if ast is None:
            return
        _apply_formula(edge, ast, home=_source_sheet(edge.source) if local else None)

    def _formula(self, name: str, sheet: str) -> tuple[str, bool] | None:
        key = name.casefold()
        if sheet:
            local = self._local.get((sheet.casefold(), key))
            if local is not None:
                return local, True
        workbook = self._workbook.get(key)
        if workbook is not None:
            return workbook, False
        return None

    def _ast(self, formula: str) -> dict[str, Any] | None:
        if formula in self._parsed:
            return self._parsed[formula]
        if self._engine is None:
            self._engine = FormulaEngine()
        text = formula if formula.startswith("=") else f"={formula}"
        parsed = self._engine.parse(text, sheet="_", addr="A1")
        ast = parsed.ast
        if parsed.unparsed or not isinstance(ast, dict) or ast.get("external"):
            self._parsed[formula] = None
            return None
        self._parsed[formula] = ast
        return ast


def resolve_defined_name_edges(
    edges: list[Edge],
    defined_names: list[Any] | None = None,
    *,
    index: NameIndex | None = None,
) -> None:
    """Point a name edge at its local cell or range, or mark an external / broken name.

    The formula text is left unchanged. Another name, a column-only range, or a
    formula that is not one cell or one range stays an unresolved name token.
    ``index`` is the book-wide table; without it the names are indexed for this call.
    """
    chosen = index if index is not None else NameIndex(defined_names)
    for edge in edges:
        chosen.apply(edge)


def _defined_fields(item: object) -> tuple[str, str, str | None] | None:
    if isinstance(item, dict):
        name = item.get("name")
        formula = item.get("formula")
        scope = item.get("sheet")
    else:
        name = getattr(item, "name", None)
        formula = getattr(item, "formula", None)
        scope = getattr(item, "sheet", None)
    if not name or formula is None:
        return None
    return str(name), str(formula), str(scope) if scope else None


def _source_sheet(source: str) -> str:
    sheet, sep, _addr = source.rpartition("!")
    if not sep:
        return ""
    return sheet


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


def _apply_formula(edge: Edge, ast: dict[str, Any], *, home: str | None) -> None:
    if ast.get("op") == "ref":
        sheet = ast.get("sheet") or home
        if not sheet or "col" not in ast or "row" not in ast:
            return
        edge.kind = "ref"
        edge.target = f"{sheet}!{format_addr(int(ast['col']), int(ast['row']))}"
        edge.unresolved = False
        edge.abs_col = bool(ast.get("abs_col"))
        edge.abs_row = bool(ast.get("abs_row"))
        return
    if ast.get("op") != "range" or ast.get("named"):
        return
    sheet = ast.get("sheet") or home
    start = ast.get("start")
    end = ast.get("end")
    if (
        not sheet
        or not isinstance(start, dict)
        or not isinstance(end, dict)
        or "col" not in start
        or "row" not in start
        or "col" not in end
        or "row" not in end
    ):
        return
    edge.kind = "range"
    edge.target = (
        f"{sheet}!{format_addr(int(start['col']), int(start['row']))}"
        f":{format_addr(int(end['col']), int(end['row']))}"
    )
    edge.unresolved = False
    edge.named = True
    edge.abs_col = bool(start.get("abs_col"))
    edge.abs_row = bool(start.get("abs_row"))
    edge.abs_col_end = bool(end.get("abs_col"))
    edge.abs_row_end = bool(end.get("abs_row"))
