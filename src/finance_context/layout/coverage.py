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
# Markdown and context stay aligned. Extra columns past this stay out;
# the kept columns are the ones next to the label, not the far tail.
_COLUMN_CAP = 32
# A sheet of calculation keys is not a report table. Stop before it is copied
# into layout, context, and the graph.
_ROW_CAP = 2000
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
    hidden_cols: set[int] | None = None,
) -> tuple[list[Block], list[str]]:
    """Blocks and notes for rows outside every existing block span."""
    hidden = hidden_cols or set()
    spans = _owned_spans(blocks)
    free = [
        row
        for row in sorted(by_row)
        if not _is_owned(row, header_rows, spans)
        and _row_has_value(by_row[row], date1904, hidden)
    ]
    # A caption row has no numbers, so it is not free. Keep the row directly
    # above a free row when it only holds column titles.
    captions = [
        row - 1
        for row in free
        if (row - 1) in by_row
        and not _is_owned(row - 1, header_rows, spans)
        and not _row_has_value(by_row[row - 1], date1904, hidden)
    ]
    free = sorted(set(free) | set(captions))
    found: list[Block] = []
    notes: list[str] = []
    for band in _bands(free):
        block, note = _band_block(sheet, band, by_row, date1904, hidden)
        if block is None:
            continue
        found.append(block)
        if note:
            notes.append(note)
    return found, notes


def _owned_spans(blocks: list[Block]) -> list[tuple[int, int]]:
    """Vertical spans of blocks that already exist. Not a filled set of every row."""
    spans: list[tuple[int, int]] = []
    for block in blocks:
        rows = [row.row for row in block.rows]
        if rows:
            spans.append((min(rows), max(rows)))
    return spans


def _is_owned(row: int, header_rows: set[int], spans: list[tuple[int, int]]) -> bool:
    if row in header_rows:
        return True
    return any(start <= row <= end for start, end in spans)


def _bands(rows: list[int]) -> list[list[int]]:
    """One missing row splits two lists. They are not one table."""
    if not rows:
        return []
    bands = [[rows[0]]]
    for prev, nxt in zip(rows, rows[1:], strict=False):
        if nxt - prev >= 2:
            bands.append([nxt])
        else:
            bands[-1].append(nxt)
    return bands


def _row_has_value(cells: list[dict], date1904: bool, hidden_cols: set[int]) -> bool:
    return any(
        int(cell["col"]) not in hidden_cols and _is_value(cell, date1904) for cell in cells
    )


def _is_year_text(text: str | None) -> bool:
    """A bare year is a period token, not a list value and not a row label."""
    if not text or len(text) != 4 or not text.isascii() or not text.isdigit():
        return False
    year = int(text)
    return 1900 <= year <= 2100


def _is_value(cell: dict, date1904: bool) -> bool:
    text = _text(cell, date1904)
    if _is_year_text(text):
        return False
    return bool(text and _is_number(text))


def _band_block(
    sheet: str,
    band: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
    hidden_cols: set[int],
) -> tuple[Block | None, str | None]:
    value_cols = _value_columns(band, by_row, date1904, hidden_cols)
    label_col = _label_column(band, by_row, date1904, value_cols)
    if label_col is None:
        return None, None
    value_cols = [col for col in value_cols if col > label_col]
    value_cols = _contiguous(value_cols)
    if not value_cols or value_cols[0] - label_col > _LABEL_GAP:
        return None, None
    titles = _column_titles(band, by_row, date1904, value_cols)
    shown, omitted = _cap_columns(value_cols)
    rows: list[LayoutRow] = []
    for row_n in band:
        if row_n in titles:
            continue
        label = _label_text(by_row[row_n], label_col, date1904)
        if not label or _is_number(label):
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
    truncated = len(rows) > _ROW_CAP
    if truncated:
        rows = rows[:_ROW_CAP]
    richest = max(len(row.cells) for row in rows)
    if len(rows) == 1 and richest <= _PARAMETER_VALUES and len(shown) <= _PARAMETER_VALUES:
        return None, None
    # Bare numbers in one or two columns, with no caption row, are a rejected
    # date header. Amount | Share under a caption row is a list.
    if (
        len(shown) <= _KEY_VALUE_COLUMNS
        and not titles
        and any(len(row.cells) > 1 for row in rows)
    ):
        return None, None
    kind = "key_value" if len(shown) <= _KEY_VALUE_COLUMNS else "matrix"
    headers = [
        AxisHeader(
            col=col,
            text=_caption(by_row, titles, col, date1904) or index_to_col(col),
            role="unknown" if _period_caption(by_row, titles, col, date1904) else "value",
            period_key=f"c{col}",
        )
        for col in shown
    ]
    first = rows[0].row
    block_id = f"{sheet}!c{first}"
    block = Block(
        block_id=block_id,
        label_col=label_col,
        axis=Axis(id=block_id, row=titles and min(titles) or first, headers=headers),
        rows=rows,
        kind=kind,
    )
    note = None
    if omitted:
        note = (
            f"Sheet {sheet}: coverage kept {len(shown)} of {len(value_cols)} value columns"
        )
    if truncated:
        extra = f"Sheet {sheet}: coverage kept {_ROW_CAP} rows"
        note = f"{note}; {extra}" if note else extra
    return block, note


def _label_column(
    band: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
    value_cols: list[int],
) -> int | None:
    """Rightmost text column that sits just left of a value.

    A header word above a numeric body (``key``, ``ID``) is not a label.
    The names beside the numbers are.
    """
    best: int | None = None
    values = set(value_cols)
    columns = {
        int(cell["col"])
        for row_n in band
        for cell in by_row[row_n]
    }
    for col in sorted(columns):
        texts = numbers = 0
        for row_n in band:
            text = _text(_cell(by_row[row_n], col) or {}, date1904)
            if not text or _is_year_text(text):
                continue
            if _is_number(text):
                numbers += 1
            else:
                texts += 1
        if texts == 0 or texts <= numbers:
            continue
        if any(col < value <= col + _LABEL_GAP for value in values):
            best = col
    return best


def _contiguous(cols: list[int]) -> list[int]:
    if not cols:
        return []
    run = [cols[0]]
    for col in cols[1:]:
        if col - run[-1] > _LABEL_GAP:
            break
        run.append(col)
    return run


def _value_columns(
    band: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
    hidden_cols: set[int],
) -> list[int]:
    """The run of value columns that starts at the first one.

    A page number many columns to the right is not part of the list.
    """
    cols: set[int] = set()
    for row_n in band:
        for cell in by_row[row_n]:
            col = int(cell["col"])
            if col in hidden_cols or not _is_value(cell, date1904):
                continue
            cols.add(col)
    ordered = sorted(cols)
    if not ordered:
        return []
    run = [ordered[0]]
    for col in ordered[1:]:
        if col - run[-1] > _LABEL_GAP:
            break
        run.append(col)
    return run


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
        if not text:
            continue
        if _is_number(text) and not _is_year_text(text):
            continue
        return text.strip()
    return None


def _period_caption(
    by_row: dict[int, list[dict]],
    titles: set[int],
    col: int,
    date1904: bool,
) -> bool:
    return _is_year_text(_caption(by_row, titles, col, date1904))


def _cap_columns(cols: list[int]) -> tuple[list[int], int]:
    if len(cols) <= _COLUMN_CAP:
        return cols, 0
    return cols[:_COLUMN_CAP], len(cols) - _COLUMN_CAP


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
