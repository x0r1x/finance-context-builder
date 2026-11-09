"""Blocks for occupied rows that no period axis claimed.

A sheet is still cut by the timeline detector. Rows outside every block's
span are not a second timeline: a wide grid is a matrix, a narrow list is
key_value, and one labelled number is a sheet parameter that stays out of
the report. Existing axes are not rebuilt.
"""

from __future__ import annotations

import re

from finance_context.excel.a1 import index_to_col
from finance_context.layout.cells import _is_number, _text
from finance_context.layout.models import Axis, AxisHeader, Block, LayoutRow, RowCell

# One labelled number is a sheet parameter, not a table. Two such rows are a list.
_PARAMETER_VALUES = 2
# A list uses one or two value columns. More columns are a matrix.
_KEY_VALUE_COLUMNS = 2
# Values further right than this are a cover-page control, not this row's list.
_LABEL_GAP = 3
# Markdown and context stay aligned, so a very wide grid is excerpted.
_COLUMN_CAP = 32
_COLUMN_HEAD = 24
_COLUMN_TAIL = 8
# Names of chart and dashboard sheets. A payment schedule is not one of these.
_CHART_SHEET = re.compile(
    r"^(?:charts?|графики|диаграммы?|dashboards?|дашборды?)(?:\s*\d*)$",
    re.IGNORECASE,
)


def chart_note(sheet: str) -> str | None:
    """A chart sheet is a copy of a calculation. Its rows are still kept."""
    if _CHART_SHEET.fullmatch(sheet.strip()):
        return f"Sheet {sheet} is a chart copy, not a calculation"
    return None


def coverage_blocks(
    sheet: str,
    by_row: dict[int, list[dict]],
    date1904: bool,
    blocks: list[Block],
    header_rows: set[int],
) -> tuple[list[Block], list[str]]:
    """Blocks and notes for rows outside every existing block span."""
    owned = _owned_rows(blocks, header_rows)
    free = [
        row
        for row in sorted(by_row)
        if row not in owned and _row_has_value(by_row[row], date1904)
    ]
    # A caption row has no numbers, so it is not free. Keep the row directly
    # above a free row when it only holds column titles.
    captions = [
        row - 1
        for row in free
        if (row - 1) in by_row
        and (row - 1) not in owned
        and not _row_has_value(by_row[row - 1], date1904)
    ]
    free = sorted(set(free) | set(captions))
    found: list[Block] = []
    notes: list[str] = []
    for band in _bands(free):
        block, note = _band_block(sheet, band, by_row, date1904)
        if block is None:
            continue
        found.append(block)
        if note:
            notes.append(note)
    return found, notes


def _owned_rows(blocks: list[Block], header_rows: set[int]) -> set[int]:
    """Header rows and every row inside an existing block's vertical span.

    A row between the first and last row of a timeline stays with that
    timeline. Coverage only sees rows above, below, or on a sheet with no block.
    """
    owned = set(header_rows)
    for block in blocks:
        rows = [row.row for row in block.rows]
        if not rows:
            continue
        owned.update(range(min(rows), max(rows) + 1))
    return owned


def _bands(rows: list[int]) -> list[list[int]]:
    """Split on two or more missing rows. One empty row stays inside a list."""
    if not rows:
        return []
    bands = [[rows[0]]]
    for prev, nxt in zip(rows, rows[1:], strict=False):
        if nxt - prev >= 3:
            bands.append([nxt])
        else:
            bands[-1].append(nxt)
    return bands


def _row_has_value(cells: list[dict], date1904: bool) -> bool:
    return any(_is_value(cell, date1904) for cell in cells)


def _is_value(cell: dict, date1904: bool) -> bool:
    if cell.get("formula_raw"):
        return True
    text = _text(cell, date1904)
    return bool(text and _is_number(text))


def _band_block(
    sheet: str,
    band: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> tuple[Block | None, str | None]:
    label_col = _label_column(band, by_row, date1904)
    if label_col is None:
        return None, None
    value_cols = _value_columns(band, by_row, date1904, label_col)
    if not value_cols or min(value_cols) - label_col > _LABEL_GAP:
        return None, None
    titles = _column_titles(band, by_row, date1904, value_cols)
    shown, omitted = _cap_columns(value_cols)
    rows: list[LayoutRow] = []
    for row_n in band:
        if row_n in titles:
            continue
        label = _label_text(by_row[row_n], label_col, date1904)
        if not label:
            continue
        count = _value_count(by_row[row_n], date1904, shown)
        if count == 0:
            continue
        rows.append(
            LayoutRow(
                row=row_n,
                label=label,
                label_col=label_col,
                kind="fact",
                cells=[
                    RowCell(col=col, role="value", header=_caption(by_row, titles, col, date1904))
                    for col in shown
                    if _is_value(_cell(by_row[row_n], col) or {}, date1904)
                ],
            )
        )
    if not rows:
        return None, None
    richest = max(len(row.cells) for row in rows)
    if len(rows) == 1 and richest <= _PARAMETER_VALUES and len(value_cols) <= _PARAMETER_VALUES:
        return None, None
    # Two bare numbers beside a label are a rejected date header, not a list.
    # A list has one value on each row, or a caption row above several columns.
    if len(value_cols) <= _KEY_VALUE_COLUMNS and any(len(row.cells) > 1 for row in rows):
        return None, None
    kind = "key_value" if len(value_cols) <= _KEY_VALUE_COLUMNS else "matrix"
    headers = [
        AxisHeader(
            col=col,
            text=_caption(by_row, titles, col, date1904) or index_to_col(col),
            role="value",
            period_key=f"c{col}",
        )
        for col in shown
    ]
    first = rows[0].row
    block_id = f"{sheet}!c{first}"
    block = Block(
        block_id=block_id,
        label_col=label_col,
        axis=Axis(id=block_id, row=first, headers=headers),
        rows=rows,
        kind=kind,
    )
    note = f"Sheet {sheet}: {len(rows)} rows included without a period axis ({kind}"
    if omitted:
        note += f", {len(shown)} of {len(value_cols)} value columns"
    note += ")"
    return block, note


def _label_column(
    band: list[int], by_row: dict[int, list[dict]], date1904: bool
) -> int | None:
    """Leftmost column that carries a non-numeric caption on any band row."""
    found: list[int] = []
    for row_n in band:
        for cell in by_row[row_n]:
            text = _text(cell, date1904)
            if text and not _is_number(text):
                found.append(int(cell["col"]))
    return min(found) if found else None


def _value_columns(
    band: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
    label_col: int,
) -> list[int]:
    cols: set[int] = set()
    for row_n in band:
        for cell in by_row[row_n]:
            col = int(cell["col"])
            if col == label_col or not _is_value(cell, date1904):
                continue
            cols.add(col)
    return sorted(cols)


def _column_titles(
    band: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
    value_cols: list[int],
) -> set[int]:
    """A row of captions above the numbers. It is not itself a value row."""
    titles: set[int] = set()
    for row_n in band:
        cells = by_row[row_n]
        if _value_count(cells, date1904, value_cols):
            continue
        if any(_text(_cell(cells, col) or {}, date1904) for col in value_cols):
            titles.add(row_n)
    return titles


def _caption(
    by_row: dict[int, list[dict]],
    titles: set[int],
    col: int,
    date1904: bool,
) -> str | None:
    for row_n in sorted(titles):
        text = _text(_cell(by_row[row_n], col) or {}, date1904)
        if text and not _is_number(text):
            return text.strip()
    return None


def _cap_columns(cols: list[int]) -> tuple[list[int], int]:
    if len(cols) <= _COLUMN_CAP:
        return cols, 0
    shown = cols[:_COLUMN_HEAD] + cols[-_COLUMN_TAIL:]
    return shown, len(cols) - len(shown)


def _label_text(cells: list[dict], label_col: int, date1904: bool) -> str | None:
    text = _text(_cell(cells, label_col) or {}, date1904)
    if not text:
        return None
    stripped = text.strip()
    return stripped or None


def _value_count(cells: list[dict], date1904: bool, cols: list[int]) -> int:
    wanted = set(cols)
    return sum(1 for cell in cells if int(cell["col"]) in wanted and _is_value(cell, date1904))


def _cell(cells: list[dict], col: int) -> dict | None:
    for cell in cells:
        if int(cell["col"]) == col:
            return cell
    return None
