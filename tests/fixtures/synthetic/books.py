"""Synthetic workbooks used for the perf and memory measurements.

Each function writes one shape. ``rows`` is the number of data rows under the
header. Seeds are fixed, so two calls with the same arguments write the same
bytes. The shapes:

- ``rel`` — ``rows`` × 60 periods of ``=1+1`` cached as 2. ``rel20k`` is
  20,000 × 60 = 1,200,000 formulas.
- ``series`` — relative formulas across 24 periods (the ad-hoc ``gen_rel.py``
  grid). The fourth formula negates the cell three rows up, so each SUM
  repeats instead of doubling out of the float range. ``series10k`` is
  10,000 × 24.
- ``hops`` — one vertical hop chain under a single year. One period is a
  params block, so layout does not walk timeline parents.
- ``hops_tl`` — the same chain on two year columns. Two periods are a
  timeline. 100,000 data rows have 199,998 formula edges (two chains, each
  seeded by a value).
- ``dense`` — ``rows`` × 200 numeric values. ``dense2m`` is 10,000 × 200.
- ``outline`` — indented labels, row outlines, and unit-text rows.
- ``sumif`` — copied-down ``SUMIF(A:A,…)`` plus ``SUM($B$2:B{n})`` (the
  ad-hoc ``gen2.py`` sheet).
- ``text`` — unique shared strings (the ad-hoc ``gen_sst.py`` sheet).
  ``s50k`` / ``s100k`` / ``s200k`` and ``text200k`` are row counts.

The short window-SUM grid and the calc-column SUMIF sheet from the other
ad-hoc scripts are the same idea as ``sumif`` at a different width. The
benchmark book is this ``sumif``.
"""

from __future__ import annotations

import random
from pathlib import Path

from tests.fixtures.synthetic.ooxml import Book, col_letter

REL_PERIODS = 60
SERIES_PERIODS = 24
DENSE_COLUMNS = 200
TEXT_STRLEN = 64
# gen_sst.py seed. The word list is copied from that script.
TEXT_SEED = 1
_TEXT_WORDS = (
    "выручка себестоимость прибыль расходы налог амортизация капитал долг "
    "кредит процент запасы аренда персонал маркетинг логистика revenue cost "
    "margin opex capex debt equity"
).split()

_OUTLINE_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<cellXfs count="3">'
    '<xf numFmtId="0"/>'
    '<xf numFmtId="0"><alignment indent="1"/></xf>'
    '<xf numFmtId="0"><alignment indent="2"/></xf>'
    "</cellXfs></styleSheet>"
)


def rel(path: Path, rows: int, *, periods: int = REL_PERIODS) -> Path:
    """``rows`` line items by ``periods`` columns of ``=1+1`` cached as 2."""
    _require_positive(rows, periods)
    with Book(path, "Model") as book:
        with book.row(1) as header:
            header.string(1, "Line item")
            for offset in range(periods):
                header.string(offset + 2, f"FY{2000 + offset}")
        for index in range(rows):
            excel_row = index + 2
            with book.row(excel_row) as line:
                line.string(1, f"Item {excel_row}")
                for col in range(2, periods + 2):
                    line.formula(col, "=1+1", cached=2)
    return path


def series(path: Path, rows: int, *, periods: int = SERIES_PERIODS) -> Path:
    """Relative formula grid. The first rows are values; later rows copy, subtract, and sum.

    Cached values are the formula results, so the book does not take the
    missing-cache path. Every cache fits in a finite float: the row after a
    difference negates the cell three rows up, and the next SUM repeats.
    """
    _require_positive(rows, periods)
    grid: dict[tuple[int, int], int] = {}
    with Book(path, "Model") as book:
        with book.row(1) as header:
            header.string(1, "Line item")
            for offset in range(periods):
                header.string(offset + 2, f"FY{2000 + offset}")
        for excel_row in range(2, rows + 2):
            with book.row(excel_row) as line:
                line.string(1, f"Item {excel_row}")
                kind = excel_row % 4
                for col in range(2, periods + 2):
                    formula, value = _series_cell(excel_row, col, kind, grid)
                    grid[(excel_row, col)] = value
                    if formula is None:
                        line.number(col, value)
                    else:
                        line.formula(col, formula, cached=value)
    return path


def hops(path: Path, rows: int) -> Path:
    """One hop chain under a single year header. Layout treats that as params."""
    _require_positive(rows)
    _hop_sheet(path, rows, periods=1)
    return path


def hops_tl(path: Path, rows: int) -> Path:
    """Two hop chains under two year headers. Layout treats that as a timeline."""
    _require_positive(rows)
    _hop_sheet(path, rows, periods=2)
    return path


def dense(path: Path, rows: int, *, columns: int = DENSE_COLUMNS) -> Path:
    """``rows`` × ``columns`` numeric values under year headers 1900…"""
    _require_positive(rows, columns)
    if columns > 200:
        raise ValueError("dense year headers run from 1900 through 2099 (200 columns)")
    with Book(path, "Model") as book:
        with book.row(1) as header:
            header.string(1, "Line item")
            for offset in range(columns):
                header.string(offset + 2, str(1900 + offset))
        for index in range(rows):
            excel_row = index + 2
            with book.row(excel_row) as line:
                line.string(1, f"Item {excel_row}")
                for col in range(2, columns + 2):
                    # A constant outside the year range, so a body row is not a second header.
                    line.number(col, 1)
    return path


def outline(path: Path, *, cycles: int = 1) -> Path:
    """Indented sections, outline levels, and unit-text rows on a two-year timeline.

    ``cycles`` repeats the section. The checked-in shape uses one cycle.
    """
    _require_positive(cycles)
    with Book(path, "Model", styles_xml=_OUTLINE_STYLES, summary_below=True) as book:
        with book.row(1) as header:
            header.string(1, "Item")
            header.string(2, "2020")
            header.string(3, "2021")
        excel_row = 2
        for cycle in range(cycles):
            excel_row = _outline_cycle(book, excel_row, cycle)
    return path


def sumif(path: Path, rows: int) -> Path:
    """Copied-down ``SUMIF(A:A,…)`` and a running ``SUM($B$2:B{n})``.

    Keys repeat every 100 rows. Formula cells carry no cache, matching the
    ad-hoc sheet.
    """
    _require_positive(rows)
    with Book(path, "Data") as book:
        with book.row(1) as header:
            header.string(1, "key")
            header.string(2, "val")
            header.string(3, "sumif")
            header.string(4, "running")
        for excel_row in range(2, rows + 2):
            with book.row(excel_row) as line:
                line.number(1, excel_row % 100)
                line.number(2, excel_row * 3)
                line.formula(3, f"=SUMIF(A:A,A{excel_row},B:B)")
                line.formula(4, f"=SUM($B$2:B{excel_row})")
    return path


def text(path: Path, rows: int, *, strlen: int = TEXT_STRLEN, seed: int = TEXT_SEED) -> Path:
    """``rows`` labels and notes, each a unique shared string, plus three year columns.

    ``s50k`` is 50,000 rows, ``s200k`` / ``text200k`` is 200,000. ``strlen`` is
    the character budget of each string, filled from ``seed``.
    """
    _require_positive(rows, strlen)
    rng = random.Random(seed)
    with Book(path, "Model") as book:
        with book.row(1) as header:
            header.string(1, "Статья", unique=True)
            header.string(2, "Комментарий", unique=True)
            header.number(3, 2022)
            header.number(4, 2023)
            header.number(5, 2024)
        for excel_row in range(2, rows + 2):
            with book.row(excel_row) as line:
                line.string(1, _note(rng, excel_row, "Статья", strlen), unique=True)
                line.string(2, _note(rng, excel_row, "Прим", strlen), unique=True)
                line.number(3, excel_row)
                line.number(4, excel_row + 1)
                line.number(5, excel_row + 2)
    return path


def _hop_sheet(path: Path, rows: int, *, periods: int) -> None:
    """Column B (and C, when present) copies the cell above. Row 2 is the seed value 1."""
    years = ("2020", "2021")
    with Book(path, "Model") as book:
        with book.row(1) as header:
            # "Parameter" is a label header, so the year under it stays a column
            # caption. "Line" is not, and the header row would be published as data.
            header.string(1, "Parameter" if periods == 1 else "Line")
            for period in range(periods):
                header.string(period + 2, years[period])
        for excel_row in range(2, rows + 2):
            with book.row(excel_row) as line:
                # "Item 2" is a header phrase (the word item plus a qualifier),
                # so every row would be a header and the sheet would not be a
                # params block. A plain "Line N" stays a data label.
                line.string(1, f"Line {excel_row}")
                for col in range(2, periods + 2):
                    if excel_row == 2:
                        line.number(col, 1)
                    else:
                        letter = col_letter(col)
                        line.formula(col, f"={letter}{excel_row - 1}", cached=1)


def _series_cell(
    row: int,
    col: int,
    kind: int,
    grid: dict[tuple[int, int], int],
) -> tuple[str | None, int]:
    letter = col_letter(col)
    if row < 6:
        return None, 100 + row + col
    if kind == 0:
        return (
            f"=SUM({letter}{row - 4}:{letter}{row - 1})",
            sum(grid[(earlier, col)] for earlier in range(row - 4, row)),
        )
    if kind == 1:
        return f"={letter}{row - 1}", grid[(row - 1, col)]
    if kind == 2:
        return (
            f"={letter}{row - 2}-{letter}{row - 1}",
            grid[(row - 2, col)] - grid[(row - 1, col)],
        )
    # The ad-hoc grid planted a 1 here, then added the left neighbour. Column B
    # then sums to ``2 * previous + 1`` every four rows, and by row 4076 the
    # cache is a 309-digit string that ``parse_number`` rejects. Negating the
    # previous SUM keeps the next one equal to it.
    return f"=-{letter}{row - 3}", -grid[(row - 3, col)]


def _outline_cycle(book: Book, start: int, cycle: int) -> int:
    """One statement block. Returns the next free row."""
    suffix = f" {cycle}" if cycle else ""
    specs: tuple[tuple[str, int, int, tuple[int, int] | None], ...] = (
        (f"Balance Sheet{suffix}", 0, 0, None),
        (f"Current{suffix}", 1, 1, None),
        (f"Cash{suffix}", 2, 2, (10, 12)),
        ("k£", 0, 2, None),
        (f"Deposits{suffix}", 2, 2, (40, 44)),
        (f"Total{suffix}", 1, 1, (50, 56)),
        (f"Income Statement{suffix}", 0, 0, None),
        (f"Revenue{suffix}", 0, 1, (90, 95)),
        ("RURm", 0, 1, None),
        (f"Costs{suffix}", 1, 2, (30, 33)),
        (f"Profit{suffix}", 0, 1, (60, 62)),
    )
    row_number = start
    for label, style, level, values in specs:
        with book.row(row_number, outline=level) as line:
            line.string(1, label, style=style or None)
            if values is not None:
                line.number(2, values[0])
                line.number(3, values[1])
        row_number += 1
    return row_number


def _note(rng: random.Random, row: int, kind: str, strlen: int) -> str:
    text = f"{kind} {row} "
    while len(text) < strlen:
        text += rng.choice(_TEXT_WORDS) + " "
    return text[:strlen].strip()


def _require_positive(*values: int) -> None:
    for value in values:
        if value < 1:
            raise ValueError("sizes start at 1")

