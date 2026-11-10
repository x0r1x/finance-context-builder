from __future__ import annotations

import re
import unicodedata
from collections import Counter

from finance_context.excel.dates import is_date_format
from finance_context.layout.cells import parse_number
from finance_context.layout.models import Layout, LayoutRow, TimeAxis, is_series_column
from finance_context.layout.normalize import normalize_label
from finance_context.layout.periods import (
    _has_two_short_dates,
    display_cell_text,
    is_calendar_key,
    unparsed_period_shape,
)
from finance_context.layout.resolve import axes_for
from finance_context.models.context import ContextAxis, ContextPeriod

_GENERAL_FORMAT = re.compile(r"^(general|standard|@)$", re.IGNORECASE)
_FORMAT_NOISE = re.compile(r"\[[^\]]*\]|\"[^\"]*\"|\\.|[*_]")


def build_axes(
    layout: Layout,
    cells: list,
    *,
    date1904: bool = False,
    by_addr: dict | None = None,
    period_warnings: bool = True,
) -> tuple[list[ContextAxis], list[str]]:
    """Published axes, their notes, and unparsed period headers.

    ``by_addr`` is the caller's ``(sheet, row, col)`` map when it already has
    one. ``period_warnings`` is false when the caller scans the workbook file
    itself. Layout cells on that path do not carry ``addr``.
    """
    if by_addr is None:
        by_addr = {(c["sheet"], int(c["row"]), int(c["col"])): c for c in cells}
    published = _published_axis_ids(layout)
    timeline_of = _timeline_of(layout)
    axes: list[ContextAxis] = []
    for sheet in layout.sheets:
        for axis in _sheet_axes(sheet):
            if axis.id not in published:
                continue
            flags = _flag_rows(sheet, axis)
            built = _annotate_axis(sheet.name, axis, flags, by_addr, date1904)
            if built is None:
                continue
            built.timeline_id = timeline_of.get(built.id, built.id)
            axes.append(built)
    warnings = _unparsed_period_warnings(cells, date1904) if period_warnings else []
    for sheet in layout.sheets:
        for axis in sheet.axes:
            if axis.id not in published:
                continue
            warnings.extend(axis.notes)
    return axes, warnings


def _unparsed_period_warnings(cells: list, date1904: bool) -> list[str]:
    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    for cell in cells:
        text = _period_warning_text(cell, date1904)
        if not text or not unparsed_period_shape(text):
            continue
        key = (str(cell.get("sheet")), text)
        if key in seen:
            continue
        seen.add(key)
        out.append(f"Unparsed period header {cell.get('sheet')}!{cell.get('addr')}: «{text}»")
    return out


# Punctuation of a cached amount. Anything else can be a period header.
_AMOUNT_CHARS = set(" \xa0\u202f\u2009,.'’+-−–—()%")
# Header normalization deletes these between digits (`1.20 24` → `1.2024`).
# They must not wipe a year that is still being accumulated.
_YEAR_GAP = frozenset(" \xa0\u202f\u2009")


def _might_be_unparsed_period(raw: str) -> bool:
    """False when this cached text cannot become an unparsed-period warning.

    The warning needs a letter, other header punctuation, or a 19xx/20xx year.
    A plain amount has none of those, so the period parser can stay off it.
    A space inside the year stays out of the window and does not clear it.
    ``1 234,56`` still has no 1900–2099 run. Two ``dd.mm.yy`` pieces are not an amount.
    """
    if not raw:
        return False
    window: list[str] = []
    for char in raw:
        if char.isdigit():
            window.append(char)
            if len(window) > 4:
                del window[0]
            if len(window) == 4 and _year_in_window(window):
                return True
            continue
        if char in _YEAR_GAP:
            continue
        window.clear()
        if char not in _AMOUNT_CHARS:
            return True
    return raw.count(".") >= 4 and _has_two_short_dates(raw)


def _year_in_window(window: list[str]) -> bool:
    """True when four digits are a year 1900–2099. ``12024`` still sees ``2024``."""
    if all("0" <= char <= "9" for char in window):
        year = int("".join(window))
    else:
        text = unicodedata.normalize("NFKC", "".join(window))
        if len(text) != 4 or not text.isdigit():
            return False
        year = int(text)
    return 1900 <= year <= 2099


def _period_warning_text(cell: dict, date1904: bool) -> str | None:
    """Text a header warning may use.

    A plain number with a non-date format is judged by what Excel shows.
    ``1.2072`` with ``0.0`` is ``1.2``, not month 1 of 2072. Text such as
    ``Q5 2025`` and ``1.2025`` still uses the cell text.
    """
    return _period_warning_value(
        cell.get("cached_value"),
        cell.get("number_format"),
        date1904,
    )


def _period_warning_value(value: object, number_format: object, date1904: bool) -> str | None:
    if value is None:
        return None
    raw = str(value)
    if not _might_be_unparsed_period(raw):
        return None
    if (
        isinstance(number_format, str)
        and _explicit_numeric_format(number_format)
        and _plain_number(raw)
    ):
        return _displayed_number(raw, number_format)
    text = display_cell_text(
        raw,
        number_format if isinstance(number_format, str) else None,
        date1904=date1904,
    )
    if text is None:
        return raw
    return text


def _plain_number(text: str) -> bool:
    return parse_number(text) is not None


def _explicit_numeric_format(fmt: str) -> bool:
    if is_date_format(fmt):
        return False
    first = fmt.split(";", 1)[0].strip()
    return bool(first) and _GENERAL_FORMAT.fullmatch(first) is None


def _displayed_number(value: str, fmt: str) -> str | None:
    """Excel's shown digits for a simple ``0`` / ``0.0`` / ``#,##0.00`` format."""
    number = parse_number(value)
    if number is None:
        return None
    token = _FORMAT_NOISE.sub("", fmt.split(";", 1)[0])
    percent = "%" in token
    token = token.replace("%", "").replace(",", "")
    if not re.search(r"[0#]", token):
        return None
    if percent:
        number *= 100
    decimals = 0
    if "." in token:
        decimals = sum(1 for char in token.split(".", 1)[1] if char in "0#")
    return f"{number:.{decimals}f}"


def _published_axis_ids(layout: Layout) -> set[str]:
    """Every local axis stays in the document. Sharing does not drop its id."""
    ids: set[str] = set()
    for sheet in layout.sheets:
        for block in sheet.blocks:
            if block.axis_ids:
                ids.update(block.axis_ids)
            elif block.axis is not None:
                ids.add(block.axis.id)
    return ids


def _timeline_of(layout: Layout) -> dict[str, str]:
    links: dict[str, str] = {}
    for sheet in layout.sheets:
        for block in sheet.blocks:
            local = list(block.axis_ids)
            shared = list(block.timeline_ids)
            if len(shared) != len(local):
                shared = local
            for axis_id, timeline_id in zip(local, shared, strict=False):
                links[axis_id] = timeline_id
            if not local and block.axis is not None:
                links.setdefault(block.axis.id, block.axis.id)
    return links


def _sheet_axes(sheet) -> list[TimeAxis]:
    if sheet.axes:
        return list(sheet.axes)
    found: list[TimeAxis] = []
    seen: set[str] = set()
    for block in sheet.blocks:
        for axis in axes_for(sheet, block):
            if axis.id in seen:
                continue
            seen.add(axis.id)
            found.append(axis)
    return found


def _flag_keys(flags: list[LayoutRow]) -> list[str]:
    """One key per flag row. A repeated label keeps the nearest preceding flag."""
    bases = [normalize_label(row.label) or f"row {row.row}" for row in flags]
    counts = Counter(bases)
    used: set[str] = set()
    keys: list[str] = []
    for index, row in enumerate(flags):
        base = bases[index]
        if counts[base] == 1:
            key = base
        else:
            qualifier = _preceding_flag_key(bases, keys, index)
            key = f"{base} ({qualifier})" if qualifier else f"{base} {row.row}"
        key = _unused_flag_key(key, used, row.row)
        used.add(key)
        keys.append(key)
    return keys


def _preceding_flag_key(bases: list[str], keys: list[str], index: int) -> str | None:
    own = bases[index]
    for earlier in range(index - 1, -1, -1):
        if bases[earlier] != own:
            return keys[earlier]
    return None


def _unused_flag_key(key: str, used: set[str], row: int) -> str:
    if key not in used:
        return key
    candidate = f"{key} {row}"
    suffix = 2
    while candidate in used:
        candidate = f"{key} {row} {suffix}"
        suffix += 1
    return candidate


def _flag_rows(sheet, axis: TimeAxis) -> list[LayoutRow]:
    rows: list[LayoutRow] = []
    for block in sheet.blocks:
        if getattr(block, "kind", "timeline") != "timeline":
            continue
        ids = set(block.axis_ids)
        if axis.id not in ids and not (block.axis is not None and block.axis.id == axis.id):
            continue
        rows.extend(row for row in block.rows if row.kind == "flag")
    return rows


def _annotate_axis(
    sheet_name: str,
    axis: TimeAxis,
    flags: list[LayoutRow],
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
) -> ContextAxis | None:
    periods_in = [
        period
        for period in axis.periods
        if is_series_column(period.kind, period.period_key)
    ]
    if not periods_in:
        return None
    periods: list[ContextPeriod] = []
    grain = axis.grain
    flag_keys = _flag_keys(flags)
    for index, header in enumerate(periods_in, start=1):
        flag_map: dict[str, bool] = {}
        for row, key in zip(flags, flag_keys, strict=True):
            flag_map[key] = _flag_on(by_addr, sheet_name, row.row, header.col, date1904)
        calendar = header.period_key if is_calendar_key(header.period_key) else None
        if calendar and len(calendar) >= 4 and calendar[:4].isdigit():
            calendar = calendar[:4]
        elif grain and not str(grain).startswith("model_"):
            calendar = header.period_key
        else:
            calendar = None
        periods.append(
            ContextPeriod(
                col=header.col,
                text=header.text,
                period_key=header.period_key,
                group_key=header.group_key,
                index=index,
                calendar_year=calendar,
                start_date=getattr(header, "start_date", None),
                end_date=getattr(header, "end_date", None),
                flags=flag_map,
                hidden=bool(getattr(header, "hidden", False)),
                row=header.row,
            )
        )
    live = {name for period in periods for name, on in period.flags.items() if on}
    for period in periods:
        period.flags = {name: on for name, on in period.flags.items() if name in live}
    return ContextAxis(
        id=axis.id,
        sheet=sheet_name,
        grain=grain,
        header_row=axis.header_row,
        transposed=bool(axis.transposed),
        periods=periods,
    )


def _flag_on(
    by_addr: dict[tuple[str, int, int], dict],
    sheet: str,
    row: int,
    col: int,
    date1904: bool,
) -> bool:
    cell = by_addr.get((sheet, row, col))
    if cell is None:
        return False
    text = display_cell_text(
        cell.get("cached_value") if cell.get("cached_value") is not None else None,
        cell.get("number_format"),
        date1904=date1904,
    )
    if text is None:
        raw = cell.get("cached_value")
        text = str(raw) if raw is not None else None
    blob = (text or "").strip().replace(",", ".")
    if blob in {"1", "1.0", "true", "yes"}:
        return True
    number = parse_number(blob)
    return number is not None and abs(number - 1.0) < 1e-9

