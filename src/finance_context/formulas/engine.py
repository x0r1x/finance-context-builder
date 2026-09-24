from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from finance_context.excel.a1 import col_to_index, format_addr, index_to_col, parse_addr
from finance_context.formulas.models import Edge, ParsedFormula

_DYNAMIC_FUNCS = {"INDIRECT", "OFFSET"}
_XL_FUNC_PREFIXES = ("_xlfn.", "_xlws.")
_ERRORS = (
    "#GETTING_DATA!",
    "#DIV/0!",
    "#VALUE!",
    "#NULL!",
    "#NAME?",
    "#NUM!",
    "#N/A",
    "#REF!",
)
_CELL_BODY = re.compile(r"(\$?)([A-Za-z]+)(\$?)(\d+)")
_COL_PAIR = re.compile(r"(\$?)([A-Za-z]+):(\$?)([A-Za-z]+)(?![A-Za-z0-9])", re.I)
_ROW_PAIR = re.compile(r"(\$?)(\d+):(\$?)(\d+)(?!\d)")
_UNQUOTED_SHEET = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*!")
_BRACKET_SHEET = re.compile(r"[A-Za-z0-9_.]+")


class FormulaSyntaxError(ValueError):
    pass


@dataclass
class Token:
    kind: str
    value: Any
    pos: int


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


def _shift_point(
    point: dict[str, Any], dc: int, dr: int, *, cols: bool, rows: bool
) -> dict[str, Any]:
    out = dict(point)
    if cols and "col" in out and not out.get("abs_col"):
        out["col"] = max(int(out["col"]) + dc, 1)
    if rows and "row" in out and not out.get("abs_row"):
        out["row"] = max(int(out["row"]) + dr, 1)
    return out


def _shift_ast(node: dict[str, Any], dc: int, dr: int) -> dict[str, Any]:
    op = node.get("op")
    if op == "ref":
        return _shift_point(node, dc, dr, cols=True, rows=True)
    if op == "range":
        out = dict(node)
        move_cols = not node.get("row_only")
        move_rows = not node.get("col_only")
        if isinstance(node.get("start"), dict):
            out["start"] = _shift_point(
                node["start"], dc, dr, cols=move_cols, rows=move_rows
            )
        if isinstance(node.get("end"), dict):
            out["end"] = _shift_point(node["end"], dc, dr, cols=move_cols, rows=move_rows)
        return out
    if op == "func":
        return {**node, "args": [_shift_ast(arg, dc, dr) for arg in node.get("args") or []]}
    if op == "bin":
        return {
            **node,
            "left": _shift_ast(node["left"], dc, dr),
            "right": _shift_ast(node["right"], dc, dr),
        }
    if op in {"unary", "percent"}:
        return {**node, "expr": _shift_ast(node["expr"], dc, dr)}
    return dict(node)


def _tokenize(text: str, decimal: str, sep: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            value, i = _scan_string(text, i)
            tokens.append(Token("STRING", value, i))
            continue
        if ch == "#":
            err = next((e for e in _ERRORS if text.startswith(e, i)), None)
            if err is None:
                raise FormulaSyntaxError("bad error token")
            tokens.append(Token("ERROR", err, i))
            i += len(err)
            continue
        if ch.isdigit() or (
            ch == decimal and i + 1 < n and text[i + 1].isdigit()
        ):
            value, i = _scan_number(text, i, decimal)
            tokens.append(Token("NUMBER", value, i))
            continue
        if ch in "'[$" or ch.isalpha() or ch == "_":
            scanned = _scan_reference(text, i)
            if scanned is not None:
                node, j = scanned
                tokens.append(Token("REF", node, i))
                i = j
                continue
            if ch.isalpha() or ch == "_":
                j = i + 1
                while j < n and (text[j].isalnum() or text[j] in "_."):
                    j += 1
                tokens.append(Token("NAME", text[i:j], i))
                i = j
                continue
            raise FormulaSyntaxError("unexpected character")
        if ch == sep:
            tokens.append(Token("SEP", ch, i))
            i += 1
            continue
        if text.startswith("<>", i) or text.startswith("<=", i) or text.startswith(">=", i):
            tokens.append(Token("OP", text[i : i + 2], i))
            i += 2
            continue
        if ch in "+-*/^&=<>%":
            tokens.append(Token("OP", ch, i))
            i += 1
            continue
        if ch == ":":
            tokens.append(Token("COLON", ch, i))
            i += 1
            continue
        if ch == "(":
            tokens.append(Token("LPAREN", ch, i))
            i += 1
            continue
        if ch == ")":
            tokens.append(Token("RPAREN", ch, i))
            i += 1
            continue
        if ch == "{":
            tokens.append(Token("LBRACE", ch, i))
            i += 1
            continue
        if ch == "}":
            tokens.append(Token("RBRACE", ch, i))
            i += 1
            continue
        raise FormulaSyntaxError(f"unexpected {ch!r}")
    return tokens


def _scan_string(text: str, i: int) -> tuple[str, int]:
    j = i + 1
    out: list[str] = []
    while j < len(text):
        if text[j] == '"':
            if j + 1 < len(text) and text[j + 1] == '"':
                out.append('"')
                j += 2
                continue
            return "".join(out), j + 1
        out.append(text[j])
        j += 1
    raise FormulaSyntaxError("unterminated string")


def _scan_number(text: str, i: int, decimal: str) -> tuple[float, int]:
    start = i
    n = len(text)
    while i < n and text[i].isdigit():
        i += 1
    if i < n and text[i] == decimal and i + 1 < n and text[i + 1].isdigit():
        i += 1
        while i < n and text[i].isdigit():
            i += 1
    elif decimal != "." and i < n and text[i] == "." and i + 1 < n and text[i + 1].isdigit():
        i += 1
        while i < n and text[i].isdigit():
            i += 1
    if i < n and text[i] in "eE":
        k = i + 1
        if k < n and text[k] in "+-":
            k += 1
        if k < n and text[k].isdigit():
            i = k
            while i < n and text[i].isdigit():
                i += 1
    raw = text[start:i].replace(decimal, ".")
    return float(raw), i


def _scan_reference(text: str, i: int) -> tuple[dict[str, Any], int] | None:
    n = len(text)
    if i < n and text[i] == "'":
        return _scan_quoted_ref(text, i)
    if i < n and text[i] == "[":
        return _scan_bracket_ref(text, i)
    sheet_match = _UNQUOTED_SHEET.match(text, i)
    if sheet_match:
        body = _scan_range_body(text, sheet_match.end())
        if body is None:
            return None
        node, j = body
        node["sheet"] = sheet_match.group(0)[:-1]
        return node, j
    return _scan_range_body(text, i)


def _scan_quoted_ref(text: str, i: int) -> tuple[dict[str, Any], int] | None:
    j = i + 1
    buf: list[str] = []
    while j < len(text):
        if text[j] == "'" and j + 1 < len(text) and text[j + 1] == "'":
            buf.append("'")
            j += 2
            continue
        if text[j] == "'":
            j += 1
            break
        buf.append(text[j])
        j += 1
    else:
        return None
    if j >= len(text) or text[j] != "!":
        return None
    j += 1
    quoted = "".join(buf)
    body = _scan_range_body(text, j)
    if body is None:
        return None
    node, k = body
    if quoted.startswith("[") and "]" in quoted:
        close = quoted.index("]")
        node["external"] = quoted[1:close]
        rest = quoted[close + 1 :]
        if rest:
            node["sheet"] = rest
    else:
        node["sheet"] = quoted
    return node, k


def _scan_bracket_ref(text: str, i: int) -> tuple[dict[str, Any], int] | None:
    close = text.find("]", i)
    if close < 0:
        return None
    external = text[i + 1 : close]
    j = close + 1
    sheet = None
    sheet_match = _BRACKET_SHEET.match(text, j)
    if sheet_match and (sheet_match.end() < len(text) and text[sheet_match.end()] == "!"):
        sheet = sheet_match.group(0)
        j = sheet_match.end()
    if j < len(text) and text[j] == "!":
        j += 1
    body = _scan_range_body(text, j)
    if body is None:
        return None
    node, k = body
    node["external"] = external
    if sheet:
        node["sheet"] = sheet
    return node, k


def _cell_node(match: re.Match[str]) -> dict[str, Any]:
    return {
        "op": "ref",
        "col": col_to_index(match.group(2)),
        "row": int(match.group(4)),
        "abs_col": match.group(1) == "$",
        "abs_row": match.group(3) == "$",
    }


def _scan_range_body(text: str, i: int) -> tuple[dict[str, Any], int] | None:
    cell = _CELL_BODY.match(text, i)
    if cell:
        start = _cell_node(cell)
        j = cell.end()
        if j < len(text) and text[j] == ":":
            k = _skip_sheet_qualifier(text, j + 1)
            cell2 = _CELL_BODY.match(text, k)
            if cell2:
                return (
                    {"op": "range", "start": start, "end": _cell_node(cell2)},
                    cell2.end(),
                )
        return start, j
    cols = _COL_PAIR.match(text, i)
    if cols:
        return (
            {
                "op": "range",
                "col_only": True,
                "start": {"col": col_to_index(cols.group(2)), "abs_col": cols.group(1) == "$"},
                "end": {"col": col_to_index(cols.group(4)), "abs_col": cols.group(3) == "$"},
            },
            cols.end(),
        )
    rows = _ROW_PAIR.match(text, i)
    if rows:
        return (
            {
                "op": "range",
                "row_only": True,
                "start": {"row": int(rows.group(2)), "abs_row": rows.group(1) == "$"},
                "end": {"row": int(rows.group(4)), "abs_row": rows.group(3) == "$"},
            },
            rows.end(),
        )
    return None


def _skip_sheet_qualifier(text: str, i: int) -> int:
    if i < len(text) and text[i] == "'":
        j = i + 1
        while j < len(text):
            if text[j] == "'" and j + 1 < len(text) and text[j + 1] == "'":
                j += 2
                continue
            if text[j] == "'":
                j += 1
                break
            j += 1
        else:
            return i
        if j < len(text) and text[j] == "!":
            return j + 1
        return i
    match = _UNQUOTED_SHEET.match(text, i)
    if match:
        return match.end()
    return i


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.i = 0

    def peek(self) -> Token | None:
        if self.i >= len(self.tokens):
            return None
        return self.tokens[self.i]

    def eat(self, kind: str | None = None) -> Token:
        tok = self.peek()
        if tok is None or (kind is not None and tok.kind != kind):
            raise FormulaSyntaxError("unexpected token")
        self.i += 1
        return tok

    def parse_expr(self) -> dict[str, Any]:
        return self._parse_concat()

    def _parse_concat(self) -> dict[str, Any]:
        node = self._parse_compare()
        while self._op("&"):
            self.eat("OP")
            node = {"op": "bin", "kind": "&", "left": node, "right": self._parse_compare()}
        return node

    def _parse_compare(self) -> dict[str, Any]:
        node = self._parse_add()
        while True:
            tok = self.peek()
            compares = {"=", "<>", "<", ">", "<=", ">="}
            if tok is None or tok.kind != "OP" or tok.value not in compares:
                return node
            op = self.eat("OP").value
            node = {"op": "bin", "kind": op, "left": node, "right": self._parse_add()}

    def _parse_add(self) -> dict[str, Any]:
        node = self._parse_mul()
        while self._op("+") or self._op("-"):
            op = self.eat("OP").value
            node = {"op": "bin", "kind": op, "left": node, "right": self._parse_mul()}
        return node

    def _parse_mul(self) -> dict[str, Any]:
        node = self._parse_pow()
        while self._op("*") or self._op("/"):
            op = self.eat("OP").value
            node = {"op": "bin", "kind": op, "left": node, "right": self._parse_pow()}
        return node

    def _parse_pow(self) -> dict[str, Any]:
        node = self._parse_unary()
        if self._op("^"):
            self.eat("OP")
            node = {"op": "bin", "kind": "^", "left": node, "right": self._parse_pow()}
        return node

    def _parse_unary(self) -> dict[str, Any]:
        if self._op("+") or self._op("-"):
            op = self.eat("OP").value
            return {"op": "unary", "kind": op, "expr": self._parse_unary()}
        return self._parse_postfix()

    def _parse_postfix(self) -> dict[str, Any]:
        node = self._parse_primary()
        if self._op("%"):
            self.eat("OP")
            node = {"op": "percent", "expr": node}
        return node

    def _parse_primary(self) -> dict[str, Any]:
        tok = self.peek()
        if tok is None:
            raise FormulaSyntaxError("expected value")
        if tok.kind == "NUMBER":
            self.eat()
            return {"op": "num", "value": tok.value}
        if tok.kind == "STRING":
            self.eat()
            return {"op": "str", "value": tok.value}
        if tok.kind == "ERROR":
            self.eat()
            return {"op": "err", "value": tok.value}
        if tok.kind == "REF":
            self.eat()
            return tok.value
        if tok.kind == "NAME":
            self.eat()
            if self.peek() is not None and self.peek().kind == "LPAREN":
                return self._parse_call(tok.value)
            if self.peek() is not None and self.peek().kind == "COLON":
                return self._parse_named_range(tok.value)
            return {"op": "name", "value": tok.value}
        if tok.kind == "LPAREN":
            self.eat()
            node = self.parse_expr()
            self.eat("RPAREN")
            return node
        raise FormulaSyntaxError("expected value")

    def _parse_call(self, name: str) -> dict[str, Any]:
        self.eat("LPAREN")
        args: list[dict[str, Any]] = []
        if self.peek() is not None and self.peek().kind != "RPAREN":
            args.append(self.parse_expr())
            while self.peek() is not None and self.peek().kind == "SEP":
                self.eat("SEP")
                args.append(self.parse_expr())
        self.eat("RPAREN")
        return {"op": "func", "name": _canonical_func_name(name), "args": args}

    def _parse_named_range(self, start: str) -> dict[str, Any]:
        self.eat("COLON")
        tok = self.peek()
        if tok is None or tok.kind != "NAME":
            raise FormulaSyntaxError("expected name after :")
        end = self.eat("NAME").value
        return {"op": "range", "named": True, "start_name": start, "end_name": end}

    def _op(self, value: str) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "OP" and tok.value == value


def _canonical_func_name(name: str) -> str:
    text = name
    while True:
        lowered = text.lower()
        matched = next((p for p in _XL_FUNC_PREFIXES if lowered.startswith(p)), None)
        if matched is None:
            return text
        text = text[len(matched) :]


def _collect_edges(
    node: dict[str, Any],
    current_sheet: str,
    source: str,
    edges: list[Edge],
    *,
    suppress: bool,
) -> None:
    op = node["op"]
    if op == "func":
        dynamic = _canonical_func_name(node["name"]).upper() in _DYNAMIC_FUNCS
        if dynamic and not suppress:
            edges.append(Edge(kind="dynamic", source=source, target=None, unresolved=True))
        for arg in node["args"]:
            _collect_edges(arg, current_sheet, source, edges, suppress=suppress or dynamic)
        return
    if op == "bin":
        _collect_edges(node["left"], current_sheet, source, edges, suppress=suppress)
        _collect_edges(node["right"], current_sheet, source, edges, suppress=suppress)
        return
    if op in {"unary", "percent"}:
        _collect_edges(node["expr"], current_sheet, source, edges, suppress=suppress)
        return
    if suppress:
        return
    if op == "ref":
        edges.append(_ref_edge(node, current_sheet, source))
        return
    if op == "range":
        if node.get("named"):
            target = f"{node['start_name']}:{node['end_name']}"
            edges.append(
                Edge(kind="range", source=source, target=target, unresolved=True, named=True)
            )
            return
        edges.append(_range_edge(node, current_sheet, source))


def _ref_edge(node: dict[str, Any], current_sheet: str, source: str) -> Edge:
    sheet = node.get("sheet") or current_sheet
    target = f"{sheet}!{index_to_col(node['col'])}{node['row']}"
    abs_col = bool(node.get("abs_col"))
    abs_row = bool(node.get("abs_row"))
    if node.get("external"):
        spec = node["external"]
        return Edge(
            kind="external",
            source=source,
            target=f"[{spec}]{target}",
            abs_col=abs_col,
            abs_row=abs_row,
        )
    kind: str = "cross_sheet" if node.get("sheet") and node["sheet"] != current_sheet else "ref"
    return Edge(
        kind=kind,  # type: ignore[arg-type]
        source=source,
        target=target,
        abs_col=abs_col,
        abs_row=abs_row,
    )


def _range_target(node: dict[str, Any], sheet: str) -> str:
    if node.get("col_only"):
        c1 = index_to_col(node["start"]["col"])
        c2 = index_to_col(node["end"]["col"])
        return f"{sheet}!{c1}:{c2}"
    if node.get("row_only"):
        return f"{sheet}!{node['start']['row']}:{node['end']['row']}"
    a = f"{index_to_col(node['start']['col'])}{node['start']['row']}"
    b = f"{index_to_col(node['end']['col'])}{node['end']['row']}"
    return f"{sheet}!{a}:{b}"


def _range_edge(node: dict[str, Any], current_sheet: str, source: str) -> Edge:
    sheet = node.get("sheet") or current_sheet
    target = _range_target(node, sheet)
    start = node.get("start") or {}
    end = node.get("end") or {}
    anchors = {
        "abs_col": start.get("abs_col"),
        "abs_row": start.get("abs_row"),
        "abs_col_end": end.get("abs_col"),
        "abs_row_end": end.get("abs_row"),
    }
    if node.get("external"):
        return Edge(
            kind="external",
            source=source,
            target=f"[{node['external']}]{target}",
            **anchors,
        )
    return Edge(kind="range", source=source, target=target, **anchors)


def _r1c1(col: int, row: int, abs_col: bool, abs_row: bool, ocol: int, orow: int) -> str:
    r = f"R{row}" if abs_row else f"R[{row - orow}]"
    c = f"C{col}" if abs_col else f"C[{col - ocol}]"
    return r + c


def _prec(node: dict[str, Any]) -> int:
    op = node.get("op")
    if op == "bin":
        return {
            "^": 5,
            "*": 4,
            "/": 4,
            "+": 3,
            "-": 3,
            "=": 2,
            "<>": 2,
            "<": 2,
            ">": 2,
            "<=": 2,
            ">=": 2,
            "&": 1,
        }.get(node.get("kind", ""), 0)
    if op == "unary":
        return 6
    if op == "percent":
        return 7
    return 8


def _paren(child: dict[str, Any], text: str, parent_prec: int, *, right: bool, op: str) -> str:
    child_prec = _prec(child)
    if child_prec < parent_prec:
        return f"({text})"
    if right and child_prec == parent_prec and op in {"-", "/", "*"}:
        return f"({text})"
    return text


def _render(node: dict[str, Any], ocol: int, orow: int, sep: str, decimal: str) -> str:
    op = node["op"]
    if op == "num":
        value = node["value"]
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        text = str(value)
        if decimal == ",":
            return text.replace(".", ",")
        return text
    if op == "str":
        inner = node["value"].replace('"', '""')
        return f'"{inner}"'
    if op == "err":
        return node["value"]
    if op == "name":
        return node["value"]
    if op == "ref":
        return _render_ref(node, ocol, orow)
    if op == "range":
        if node.get("named"):
            return f"{node['start_name']}:{node['end_name']}"
        return _render_range(node, ocol, orow)
    if op == "unary":
        inner = _render(node["expr"], ocol, orow, sep, decimal)
        if _prec(node["expr"]) < _prec(node):
            inner = f"({inner})"
        return node["kind"] + inner
    if op == "percent":
        return _render(node["expr"], ocol, orow, sep, decimal) + "%"
    if op == "bin":
        left = _render(node["left"], ocol, orow, sep, decimal)
        right = _render(node["right"], ocol, orow, sep, decimal)
        prec = _prec(node)
        left = _paren(node["left"], left, prec, right=False, op=node["kind"])
        right = _paren(node["right"], right, prec, right=True, op=node["kind"])
        return left + node["kind"] + right
    if op == "func":
        args = sep.join(_render(a, ocol, orow, sep, decimal) for a in node["args"])
        return f"{node['name']}({args})"
    raise FormulaSyntaxError("cannot render")


def _sheet_prefix(node: dict[str, Any]) -> str:
    extra = ""
    if node.get("external"):
        extra = f"[{node['external']}]"
    sheet = node.get("sheet")
    if sheet:
        return f"{extra}{sheet}!"
    return extra


def _render_ref(node: dict[str, Any], ocol: int, orow: int) -> str:
    body = _r1c1(
        node["col"],
        node["row"],
        node.get("abs_col", False),
        node.get("abs_row", False),
        ocol,
        orow,
    )
    return _sheet_prefix(node) + body


def _render_range(node: dict[str, Any], ocol: int, orow: int) -> str:
    prefix = _sheet_prefix(node)
    if node.get("col_only"):
        c1 = _r1c1(node["start"]["col"], 1, node["start"].get("abs_col", False), False, ocol, orow)
        c2 = _r1c1(node["end"]["col"], 1, node["end"].get("abs_col", False), False, ocol, orow)
        return prefix + c1[c1.index("C") :] + ":" + c2[c2.index("C") :]
    if node.get("row_only"):
        r1 = _r1c1(1, node["start"]["row"], False, node["start"].get("abs_row", False), ocol, orow)
        r2 = _r1c1(1, node["end"]["row"], False, node["end"].get("abs_row", False), ocol, orow)
        return prefix + r1[: r1.index("C")] + ":" + r2[: r2.index("C")]
    a = _render_ref({**node["start"], "sheet": None}, ocol, orow)
    b = _render_ref({**node["end"], "sheet": None}, ocol, orow)
    return prefix + a + ":" + b
