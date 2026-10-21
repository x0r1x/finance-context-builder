"""A total column is the sum of the period columns. The word is only a hint.

``Итого`` or ``Total`` does not become ``period_key`` ``total`` by itself.
The column's formula has to be ``SUM`` of the period span, or its cached
values have to equal that sum. One agreeing row is enough when the header
is a total word. Without the word, the values have to agree on most rows.
"""

from __future__ import annotations

import re

from finance_context.excel.a1 import col_to_index
from finance_context.layout.cells import _cell_at, _text
from finance_context.layout.format_words import is_total_word
from finance_context.layout.models import AxisPeriod

_SUM = re.compile(
    r"^=?\s*SUM\(\s*\$?([A-Z]{1,3})\$?(\d+)\s*:\s*\$?([A-Z]{1,3})\$?(\d+)\s*\)$",
    re.IGNORECASE,
)
_SHARE = 0.6
_ABS_TOL = 1e-6
_REL_TOL = 1e-4


def apply_total_columns(
    periods: list[AxisPeriod],
    *,
    header_row: int,
    by_row: dict[int, list[dict]],
    date1904: bool,
    band_rows: list[int] | None = None,
    body_end: int | None = None,
) -> list[AxisPeriod]:
    """Append a confirmed total column sitting beside this period run."""
    if len(periods) < 2:
        return periods
    taken = {period.col for period in periods}
    lo, hi = min(taken), max(taken)
    rows = band_rows or [header_row]
    candidates = set(range(max(1, lo - 2), hi + 3))
    for row_n in rows:
        for cell in by_row.get(row_n, []):
            if is_total_word(_text(cell, date1904)):
                candidates.add(int(cell["col"]))
    added: list[AxisPeriod] = []
    for col in sorted(candidates):
        if col in taken:
            continue
        text, hinted = _band_hint(col, rows, by_row, date1904)
        # A sum sitting left of the run, with no total word, is a row stub
        # (a lifetime total beside the ruler), not a period column.
        if not hinted and col < lo:
            continue
        if not hinted and not _has_sum_formula(col, by_row, header_row, body_end):
            continue
        if not _confirms(
            col, taken, by_row, header_row, date1904, hinted=hinted, body_end=body_end
        ):
            continue
        added.append(
            AxisPeriod(
                col=col,
                text=text,
                kind="total",
                period_key="total",
                explicit_role=True,
            )
        )
    if not added:
        return periods
    return sorted([*periods, *added], key=lambda period: period.col)


def _band_hint(
    col: int,
    rows: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> tuple[str, bool]:
    parts: list[str] = []
    hinted = False
    for row_n in rows:
        cell = _cell_at(by_row.get(row_n, []), col)
        text = _text(cell, date1904) if cell else None
        if not text:
            continue
        parts.append(text)
        hinted = hinted or is_total_word(text)
    joined = " ".join(parts)
    return joined, hinted or is_total_word(joined)


def _has_sum_formula(
    col: int,
    by_row: dict[int, list[dict]],
    header_row: int,
    body_end: int | None,
) -> bool:
    for row_n in sorted(by_row):
        if row_n <= header_row:
            continue
        if body_end is not None and row_n >= body_end:
            break
        cell = _row_cells(by_row[row_n]).get(col)
        if cell and _SUM.match(str(cell.get("formula_raw") or "")):
            return True
    return False


def _confirms(
    col: int,
    period_cols: set[int],
    by_row: dict[int, list[dict]],
    header_row: int,
    date1904: bool,
    *,
    hinted: bool,
    body_end: int | None = None,
) -> bool:
    formula_hits = 0
    formula_rows = 0
    value_hits = 0
    value_rows = 0
    for row_n in sorted(by_row):
        if row_n <= header_row:
            continue
        if body_end is not None and row_n >= body_end:
            break
        cells = _row_cells(by_row[row_n])
        cell = cells.get(col)
        if cell is None:
            continue
        formula = str(cell.get("formula_raw") or "")
        if formula:
            formula_rows += 1
            if _formula_sums(formula, period_cols, row_n):
                formula_hits += 1
                continue
        expected = _period_sum(cells, period_cols, date1904)
        actual = _number(_text(cell, date1904))
        if expected is None or actual is None:
            continue
        value_rows += 1
        if _close(actual, expected):
            value_hits += 1
    if formula_rows and formula_hits / formula_rows >= _SHARE:
        return True
    if hinted and (formula_hits or value_hits):
        return True
    return value_rows >= 2 and value_hits / value_rows >= _SHARE


def _formula_sums(formula: str, period_cols: set[int], row: int) -> bool:
    match = _SUM.match(formula.strip())
    if match is None:
        return False
    left, left_row, right, right_row = match.groups()
    if int(left_row) != row or int(right_row) != row:
        return False
    start, end = sorted((col_to_index(left), col_to_index(right)))
    return set(range(start, end + 1)) == period_cols


def _row_cells(cells: list[dict]) -> dict[int, dict]:
    mapped: dict[int, dict] = {}
    for cell in cells:
        mapped.setdefault(int(cell["col"]), cell)
    return mapped


def _period_sum(cells: dict[int, dict], cols: set[int], date1904: bool) -> float | None:
    total = 0.0
    seen = 0
    for col in cols:
        cell = cells.get(col)
        number = _number(_text(cell, date1904) if cell else None)
        if number is None:
            continue
        total += number
        seen += 1
    if seen < 2:
        return None
    return total


def _number(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(text.replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _close(left: float, right: float) -> bool:
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) <= max(_ABS_TOL, _REL_TOL * scale)
