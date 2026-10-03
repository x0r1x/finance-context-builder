from __future__ import annotations

from finance_context.excel.a1 import format_addr, parse_addr
from finance_context.formulas.models import Edge, ParsedFormula
from finance_context.formulas.parse import _Parser
from finance_context.formulas.render import _collect_edges, _render, _shift_ast
from finance_context.formulas.scan import FormulaSyntaxError as FormulaSyntaxError
from finance_context.formulas.scan import _tokenize


class FormulaEngine:
    def __init__(self, locale_hint: str | None = None) -> None:
        hint = (locale_hint or "en").lower()
        self.locale = "ru" if hint.startswith("ru") else "en"
        self.decimal = "," if self.locale == "ru" else "."
        self.sep = ";" if self.locale == "ru" else ","

    def parse(self, formula: str, *, sheet: str, addr: str) -> ParsedFormula:
        text = formula[1:] if formula.startswith("=") else formula
        if not text.strip():
            return ParsedFormula(ast=None, template=None, unparsed=True, edges=[])
        try:
            tokens = _tokenize(text, self.decimal, self.sep)
            parser = _Parser(tokens)
            ast = parser.parse_expr()
            if parser.peek() is not None:
                raise FormulaSyntaxError("trailing tokens")
            origin_col, origin_row = parse_addr(addr)
            source = f"{sheet}!{addr}"
            edges: list[Edge] = []
            _collect_edges(ast, sheet, source, edges, suppress=False)
            template = "=" + _render(ast, origin_col, origin_row, self.sep, self.decimal)
            return ParsedFormula(ast=ast, template=template, unparsed=False, edges=edges)
        except (FormulaSyntaxError, ValueError):
            return ParsedFormula(ast=None, template=None, unparsed=True, edges=[])


def shift_parsed(
    parsed: ParsedFormula,
    *,
    sheet: str,
    from_col: int,
    from_row: int,
    to_col: int,
    to_row: int,
    sep: str,
    decimal: str,
) -> ParsedFormula:
    """Move a parsed master onto another cell of the same shared group."""
    if parsed.unparsed or parsed.ast is None:
        return ParsedFormula(ast=None, template=None, unparsed=True, edges=[])
    dc = to_col - from_col
    dr = to_row - from_row
    ast = parsed.ast if dc == 0 and dr == 0 else _shift_ast(parsed.ast, dc, dr)
    source = f"{sheet}!{format_addr(to_col, to_row)}"
    edges: list[Edge] = []
    _collect_edges(ast, sheet, source, edges, suppress=False)
    template = "=" + _render(ast, to_col, to_row, sep, decimal)
    return ParsedFormula(ast=ast, template=template, unparsed=False, edges=edges)


