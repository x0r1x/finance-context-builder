from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from finance_context.excel.a1 import col_to_index

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
