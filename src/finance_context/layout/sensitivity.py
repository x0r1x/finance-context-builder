"""Numeric row and column labels around a rectangular sensitivity grid."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from finance_context.layout.cells import _text, parse_number
from finance_context.layout.models import Axis, AxisHeader, Block, LayoutRow, RowCell

_AXIS_FORMAT = re.compile(r'(?:#,##)?(0(?:\.0+)?)\s*(%|"?[xX×]"?|\\[xX×])')
_FORMAT_PADDING = re.compile(r"[_*].")
_MIN_COLUMNS = 3
_MIN_ROWS = 3


def sensitivity_blocks(
    sheet: str, by_row: dict[int, list[dict]], date1904: bool
) -> tuple[list[Block], set[tuple[int, int]]]:
    """Own only the header and body cells of grids with two ordered numeric axes."""
    found: list[Block] = []
    taken: set[tuple[int, int]] = set()
    for header_row, cells in by_row.items():
        below = by_row.get(header_row + 1)
        if below is None or header_row + _MIN_ROWS not in by_row:
            continue
        for cols, headers in _column_axes(cells, date1904):
            label_col = cols[0] - 1
            if (header_row, label_col) in taken:
                continue
            corner = _cell(cells, label_col)
            if _axis_number(_text(corner, date1904)) is not None:
                continue
            start = _grid_start(by_row, header_row, label_col, cols[0], date1904)
            rows = _grid_rows(by_row, start, label_col, cols, date1904)
            if len(rows) < _MIN_ROWS:
                continue
            title = _grid_title(by_row, header_row, label_col, cols[-1], date1904)
            if title:
                for row in rows:
                    row.section_path = [title]
            block_id = f"{sheet}!c{rows[0].row}"
            found.append(Block(
                block_id=block_id,
                label_col=label_col,
                kind="matrix",
                axis=Axis(id=block_id, row=header_row, headers=headers),
                rows=rows,
            ))
            taken.update(
                (row, col)
                for row in [*range(header_row, start), *(item.row for item in rows)]
                for col in [label_col, *cols]
            )
    return found, taken


def _column_axes(cells: list[dict], date1904: bool):
    run: list[tuple[int, float, str]] = []
    for cell in sorted(cells, key=lambda item: int(item["col"])):
        col = int(cell["col"])
        text = _axis_text(cell, date1904)
        number = _axis_number(text)
        if run and (col != run[-1][0] + 1 or number is None):
            yield from _column_axis(run)
            run = []
        if number is not None:
            run.append((col, number, text))
    yield from _column_axis(run)


def _column_axis(run: list[tuple[int, float, str]]):
    if len(run) < _MIN_COLUMNS or run[0][0] < 2:
        return
    nums = [number for _, number, _ in run]
    if not _ordered(nums) or all(n.is_integer() and 1900 <= n <= 2100 for n in nums):
        return
    yield [col for col, _, _ in run], [
        AxisHeader(col=col, text=text, role="value", period_key=f"c{col}")
        for col, _, text in run
    ]


def _grid_start(by_row, header_row, label_col, first_value_col, date1904):
    """A numeric corner and a second percentage caption precede some Excel grids."""
    start = header_row + 1
    first, second = by_row.get(start, []), by_row.get(start + 1, [])
    base = _axis_text(_cell(first, label_col), date1904) or ""
    label = _axis_text(_cell(second, label_col), date1904) or ""
    caption = _axis_text(_cell(first, first_value_col), date1904) or ""
    result = _axis_text(_cell(second, first_value_col), date1904) or ""
    if _axis_number(base) is not None and not base.endswith("%") and (
        label.endswith("%") and caption.endswith("%") and not result.endswith("%")
    ):
        return start + 1
    return start


def _grid_rows(by_row, start, label_col, cols, date1904):
    rows: list[LayoutRow] = []
    previous: float | None = None
    ascending: bool | None = None
    n = start
    while cells := by_row.get(n):
        indexed = {int(cell["col"]): cell for cell in cells}
        left = _text(indexed.get(label_col - 1, {}), date1904)
        if left and parse_number(left) is not None:
            break
        label_cell = indexed.get(label_col, {})
        label = _axis_text(label_cell, date1904)
        value = _axis_number(label)
        if value is None or value == previous:
            break
        if previous is not None:
            increases = value > previous
            if ascending is not None and ascending != increases:
                break
            ascending = increases
        values = [
            RowCell(col=col, role="value") for col in cols
            if _result_cell(indexed.get(col, {}), date1904)
        ]
        if len(values) * 2 < len(cols):
            break
        rows.append(LayoutRow(
            row=n, label=label, label_col=label_col, hidden=bool(label_cell.get("hidden")),
            cells=values,
        ))
        previous = value
        n += 1
    return rows


def _ordered(numbers: list[float]) -> bool:
    return all(a < b for a, b in pairwise(numbers)) or all(a > b for a, b in pairwise(numbers))


def _result_cell(cell: dict, date1904: bool) -> bool:
    return bool(cell.get("formula_raw") or parse_number(_text(cell, date1904) or "") is not None)


def _grid_title(by_row, header, label_col, right, date1904):
    for row in (header - 1, header - 2):
        cells = [
            text for c in by_row.get(row, [])
            if label_col <= int(c["col"]) <= right and (text := _text(c, date1904))
        ]
        if cells:
            return cells[0].strip() if len(cells) == 1 and _axis_number(cells[0]) is None else None
    return None


def _axis_text(cell: dict, date1904: bool) -> str | None:
    text = _text(cell, date1904)
    if text is None:
        return None
    fmt = (cell.get("number_format") or "").split(";")[0]
    parsed = _AXIS_FORMAT.fullmatch(_FORMAT_PADDING.sub("", fmt)) if fmt else None
    if parsed is None or parse_number(text) is None:
        return text.strip()
    pattern, token = parsed.groups()
    decimals = len(pattern.partition(".")[2])
    suffix = token.strip('"\\')
    try:
        value = Decimal(text.strip().replace(",", "."))
        if suffix == "%":
            value *= 100
        return f"{value:.{decimals}f}{suffix}"
    except InvalidOperation:
        return text.strip()


def _axis_number(text: str | None) -> float | None:
    if not text:
        return None
    unit = text[-1].casefold()
    if unit in {"x", "×", "%"}:
        value = parse_number(text[:-1].strip())
        return value / 100 if value is not None and unit == "%" else value
    return parse_number(text)


def _cell(cells: list[dict], col: int) -> dict:
    return next((cell for cell in cells if int(cell["col"]) == col), {})
