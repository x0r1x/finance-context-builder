from __future__ import annotations

import re

_MAX_COL = 16_384  # XFD
_MAX_ROW = 1_048_576

_ADDR = re.compile(r"\$?([A-Za-z]+)\$?(\d+)")
# A cell in formula text is not part of a longer token: not after a word character or
# a dot (`Rate_A1`, `1E5`, `_xlfn.LOG10`), not before one or `(` / `[` (`LOG10(`,
# `DEC2HEX`, `Table1[`).
_REF = re.compile(
    r"(?<![\w.])(?P<sheet>[\w.]+!)?"
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


# A sheet repeats a handful of columns on every row. The alphabet walk is not.
_COL_LETTERS: dict[int, str] = {}


def index_to_col(n: int) -> str:
    n = max(n, 1)
    found = _COL_LETTERS.get(n)
    if found is not None:
        return found
    chars: list[str] = []
    value = n
    while value:
        value, rem = divmod(value - 1, 26)
        chars.append(chr(65 + rem))
    found = "".join(reversed(chars))
    _COL_LETTERS[n] = found
    return found


def parse_addr(addr: str) -> tuple[int, int]:
    match = _ADDR.fullmatch(addr)
    if match is None:
        raise ValueError(addr)
    return col_to_index(match.group(1)), int(match.group(2))


def format_addr(col: int, row: int) -> str:
    return f"{index_to_col(col)}{row}"


def value_coord(
    value_col: int | None,
    period_row: int | None,
    period_col: int,
    line_row: int,
) -> tuple[int, int]:
    """Column and row of one period's value.

    A transposed line reads ``(value_col, period_row)``. A horizontal line
    reads ``(period_col, line_row)``.
    """
    if value_col and period_row:
        return int(value_col), int(period_row)
    return int(period_col), int(line_row)


def is_derived_addr(addr: str, col: int, row: int) -> bool:
    """True when ``addr`` is exactly ``format_addr(col, row)``."""
    letters = index_to_col(col)
    width = len(letters)
    if len(addr) <= width or not addr.startswith(letters):
        return False
    tail = addr[width:]
    if not tail.isdigit() or (len(tail) > 1 and tail[0] == "0"):
        return False
    return int(tail) == row


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
        if ch in "['":
            # A table column or book index (`Table1[Q1]`, `[1]`) and a quoted sheet name
            # (`'Q1 2025'!`) are copied as is; only the cell after them moves.
            end = _segment_end(formula, i)
            out.append(formula[i:end])
            i = end
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


def _segment_end(formula: str, i: int) -> int:
    """End of the `[...]` or `'...'` segment at `i`. `''` inside quotes is one quote."""
    if formula[i] == "[":
        close = formula.find("]", i + 1)
        return len(formula) if close < 0 else close + 1
    j = i + 1
    while j < len(formula):
        if formula[j] == "'":
            if formula[j + 1 : j + 2] == "'":
                j += 2
                continue
            return j + 1
        j += 1
    return len(formula)
