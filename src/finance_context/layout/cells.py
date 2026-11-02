from __future__ import annotations

import math

from finance_context.layout.periods import display_cell_text

# Ordinary space, non-breaking space, narrow no-break space.
_IGNORED_SPACE = str.maketrans("", "", " \xa0\u202f")


def _cell_at(row_cells: list[dict], col: int) -> dict | None:
    for cell in row_cells:
        if int(cell["col"]) == col:
            return cell
    return None


def _text(cell: dict, date1904: bool = False) -> str | None:
    return display_cell_text(
        cell.get("cached_value") if cell.get("cached_value") is not None else None,
        cell.get("number_format"),
        date1904=date1904,
    )


def parse_number(text: str) -> float | None:
    """A finite number from cell text. A comma is the decimal mark.

    Spaces, including the non-breaking and narrow ones, are ignored.
    ``inf``, ``nan`` and an underscore are not numbers: ``float`` accepts
    them, and ``int`` of infinity or NaN aborts the whole workbook.
    """
    if "_" in text:
        return None
    cleaned = text.translate(_IGNORED_SPACE).replace(",", ".")
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return value


def parse_int(text: str) -> int | None:
    value = parse_number(text)
    if value is None or value != int(value):
        return None
    return int(value)


def _is_number(text: str) -> bool:
    return parse_number(text) is not None


def _is_int_text(text: str) -> bool:
    return parse_int(text) is not None
