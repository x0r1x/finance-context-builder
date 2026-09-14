from __future__ import annotations

import re

_ADDR = re.compile(r"\$?([A-Za-z]+)\$?(\d+)")
_REF = re.compile(
    r"(?P<sheet>(?:'(?:[^']|'')+'|[A-Za-z0-9_.]+(?:\.[A-Za-z0-9]+)?)!)?"
    r"(?P<abscol>\$)?(?P<col>[A-Z]+)(?P<absrow>\$)?(?P<row>\d+)",
    re.IGNORECASE,
)


def col_to_index(col: str) -> int:
    n = 0
    for ch in col.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def index_to_col(n: int) -> str:
    n = max(n, 1)
    chars: list[str] = []
    while n:
        n, rem = divmod(n - 1, 26)
        chars.append(chr(65 + rem))
    return "".join(reversed(chars))


def parse_addr(addr: str) -> tuple[int, int]:
    match = _ADDR.fullmatch(addr)
    if match is None:
        raise ValueError(addr)
    return col_to_index(match.group(1)), int(match.group(2))


def format_addr(col: int, row: int) -> str:
    return f"{index_to_col(col)}{row}"


def shift_formula(formula: str, from_col: int, from_row: int, to_col: int, to_row: int) -> str:
    dc = to_col - from_col
    dr = to_row - from_row
    if dc == 0 and dr == 0:
        return formula
    out: list[str] = []
    i = 0
    in_quote = False
    while i < len(formula):
        ch = formula[i]
        if ch == '"':
            in_quote = not in_quote
            out.append(ch)
            i += 1
            continue
        if in_quote:
            out.append(ch)
            i += 1
            continue
        match = _REF.match(formula, i)
        if match is None:
            out.append(ch)
            i += 1
            continue
        col = col_to_index(match.group("col"))
        row = int(match.group("row"))
        if not match.group("abscol"):
            col = max(col + dc, 1)
        if not match.group("absrow"):
            row = max(row + dr, 1)
        sheet = match.group("sheet") or ""
        abscol = "$" if match.group("abscol") else ""
        absrow = "$" if match.group("absrow") else ""
        out.append(f"{sheet}{abscol}{index_to_col(col)}{absrow}{row}")
        i = match.end()
    return "".join(out)


def formula_uses_semicolon(formula: str) -> bool:
    in_quote = False
    for ch in formula:
        if ch == '"':
            in_quote = not in_quote
        elif ch == ";" and not in_quote:
            return True
    return False
