from __future__ import annotations

import re

_MAX_COL = 16_384  # XFD
_MAX_ROW = 1_048_576

_ADDR = re.compile(r"\$?([A-Za-z]+)\$?(\d+)")
# A cell in formula text is not part of a longer token: not after a word character or
# a dot (`Rate_A1`, `1E5`, `_xlfn.LOG10`), not before one or `(` / `[` (`LOG10(`,
# `DEC2HEX`, `Table1[`).
_REF = re.compile(
    r"(?<![\w.])(?P<sheet>(?:'(?:[^']|'')+'|[\w.]+)!)?"
    r"(?P<abscol>\$)?(?P<col>[A-Z]{1,3})(?P<absrow>\$)?(?P<row>\d+)(?![\w.(\[])",
    re.IGNORECASE,
)


def col_to_index(col: str) -> int:
    n = 0
    for ch in col.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def in_grid(col: int, row: int = 1) -> bool:
    """Inside an Excel 2007+ sheet, A1 to XFD1048576. `Rate1` and `XFE1` are names."""
    return 1 <= col <= _MAX_COL and 1 <= row <= _MAX_ROW


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
        if not in_grid(col, row):
            out.append(match.group(0))
            i = match.end()
            continue
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
