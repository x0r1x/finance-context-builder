from __future__ import annotations

import math
import sys

from finance_context.excel.a1 import format_addr
from finance_context.layout.periods import display_cell_text

# Fields detection and the sheet overlays read. A dict of these is ~270 bytes;
# slots keep the per-cell record near 150 so a full sheet does not pay both.
_LAYOUT_FIELDS = (
    "sheet",
    "row",
    "col",
    "addr",
    "formula_raw",
    "formula_template",
    "cached_value",
    "hidden",
    "number_format",
    "merge_end_col",
    "style_indent",
    "outline_level",
    "col_outline_level",
)
_LAYOUT_FIELD_SET = frozenset(_LAYOUT_FIELDS)


class LayoutCell:
    """One layout cell. Dict-style reads, slot storage."""

    __slots__ = _LAYOUT_FIELDS

    def __init__(
        self,
        sheet: object,
        row: object,
        col: object,
        addr: object,
        formula_raw: object = None,
        formula_template: object = None,
        cached_value: object = None,
        hidden: object = None,
        number_format: object = None,
        merge_end_col: object = None,
        style_indent: object = None,
        outline_level: object = None,
        col_outline_level: object = None,
    ) -> None:
        # One sheet name for the whole book. A derived address is not stored:
        # ``format_addr(col, row)`` rebuilds it, and the parquet string matched
        # that form. An address that does not match stays on the slot.
        self.sheet = sys.intern(sheet) if isinstance(sheet, str) else sheet
        self.row = row
        self.col = col
        self.addr = _kept_addr(addr, row, col)
        self.formula_raw = formula_raw
        self.formula_template = formula_template
        self.cached_value = cached_value
        self.hidden = hidden
        self.number_format = number_format
        self.merge_end_col = merge_end_col
        self.style_indent = style_indent
        self.outline_level = outline_level
        self.col_outline_level = col_outline_level

    def _value(self, key: str) -> object:
        if key != "addr":
            return getattr(self, key)
        stored = self.addr
        if stored is not None:
            return stored
        row = self.row
        col = self.col
        if row is None or col is None:
            return None
        return format_addr(int(col), int(row))

    def get(self, key: str, default: object = None) -> object:
        if key not in _LAYOUT_FIELD_SET:
            return default
        return self._value(key)

    def __getitem__(self, key: str) -> object:
        if key not in _LAYOUT_FIELD_SET:
            raise KeyError(key)
        return self._value(key)

    def __setitem__(self, key: str, value: object) -> None:
        if key not in _LAYOUT_FIELD_SET:
            raise KeyError(key)
        setattr(self, key, value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LayoutCell):
            return all(self._value(key) == other._value(key) for key in _LAYOUT_FIELDS)
        if isinstance(other, dict):
            return {key: self._value(key) for key in _LAYOUT_FIELDS} == {
                key: other.get(key) for key in _LAYOUT_FIELDS
            }
        return NotImplemented

    def __contains__(self, key: object) -> bool:
        return key in _LAYOUT_FIELD_SET and self._value(key) is not None


def _kept_addr(addr: object, row: object, col: object) -> object:
    """Keep an address only when it is not ``format_addr(col, row)``."""
    if addr is None or row is None or col is None:
        return addr
    derived = format_addr(int(col), int(row))
    if addr == derived:
        return None
    return addr


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
