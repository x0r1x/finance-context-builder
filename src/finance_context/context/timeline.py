from __future__ import annotations

import re
from collections import Counter

from finance_context.excel.dates import is_date_format
from finance_context.layout.lexicon import searcher
from finance_context.layout.models import Layout, LayoutRow, TimeAxis, is_series_column
from finance_context.layout.normalize import normalize_label
from finance_context.layout.periods import (
    display_cell_text,
    is_calendar_key,
    unparsed_period_shape,
)
from finance_context.layout.resolve import axes_for
from finance_context.models.context import ContextAxis, ContextPeriod, TimelinePhase

_GENERAL_FORMAT = re.compile(r"^(general|standard|@)$", re.IGNORECASE)
_FORMAT_NOISE = re.compile(r"\[[^\]]*\]|\"[^\"]*\"|\\.|[*_]")


def build_axes(
    layout: Layout,
    cells: list[dict],
    *,
    date1904: bool = False,
) -> tuple[list[ContextAxis], list[str]]:
    by_addr = {(c["sheet"], int(c["row"]), int(c["col"])): c for c in cells}
    published = _published_axis_ids(layout)
    timeline_of = _timeline_of(layout)
    axes: list[ContextAxis] = []
    phased: list[ContextAxis] = []
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
            if any(item.phase for item in built.periods):
                phased.append(built)
    warnings: list[str] = []
    warnings.extend(_flag_phase_warnings(layout))
    if phased:
        master = max(phased, key=lambda item: sum(1 for period in item.periods if period.phase))
        warnings.extend(_duration_warnings(layout, master.periods, by_addr, date1904))
    warnings.extend(_unparsed_period_warnings(cells, date1904))
    return axes, warnings


def _flag_phase_warnings(layout: Layout) -> list[str]:
    """A flag-shaped row with no phase stays phase-less and says so."""
    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    for sheet in layout.sheets:
        for block in sheet.blocks:
            if getattr(block, "kind", "timeline") != "timeline":
                continue
            for row in block.rows:
                if row.kind != "flag" or _phase_kind(row.label) is not None:
                    continue
                folded = normalize_label(row.label)
                if searcher("phase_events").search(folded) or searcher("operation_duration").search(
                    folded
                ):
                    continue
                if searcher("structural", "flag").search(folded) is None:
                    continue
                key = (sheet.name, row.label)
                if key in seen:
                    continue
                seen.add(key)
                out.append(f"Flag row has no phase on {sheet.name}: «{row.label}»")
    return out


def _unparsed_period_warnings(cells: list[dict], date1904: bool) -> list[str]:
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


def _period_warning_text(cell: dict, date1904: bool) -> str | None:
    """Text a header warning may use.

    A plain number with a non-date format is judged by what Excel shows.
    ``1.2072`` with ``0.0`` is ``1.2``, not month 1 of 2072. Text such as
    ``Q5 2025`` and ``1.2025`` still uses the cell text.
    """
    value = cell.get("cached_value")
    if value is None:
        return None
    raw = str(value)
    fmt = cell.get("number_format")
    if isinstance(fmt, str) and _explicit_numeric_format(fmt) and _plain_number(raw):
        return _displayed_number(raw, fmt)
    text = display_cell_text(raw, fmt if isinstance(fmt, str) else None, date1904=date1904)
    if text is None:
        return raw
    return text


def _plain_number(text: str) -> bool:
    try:
        float(text.replace(" ", "").replace(",", "."))
    except ValueError:
        return False
    return True


def _explicit_numeric_format(fmt: str) -> bool:
    if is_date_format(fmt):
        return False
    first = fmt.split(";", 1)[0].strip()
    return bool(first) and _GENERAL_FORMAT.fullmatch(first) is None


def _displayed_number(value: str, fmt: str) -> str | None:
    """Excel's shown digits for a simple ``0`` / ``0.0`` / ``#,##0.00`` format."""
    try:
        number = float(value.replace(" ", "").replace(",", "."))
    except ValueError:
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
    last_phase: TimelinePhase | None = None
    run = 0
    grain = axis.grain
    flag_keys = _flag_keys(flags)
    for index, header in enumerate(periods_in, start=1):
        flag_map: dict[str, bool] = {}
        construction = False
        operation = False
        for row, key in zip(flags, flag_keys, strict=True):
            on = _flag_on(by_addr, sheet_name, row.row, header.col, date1904)
            flag_map[key] = on
            kind = _phase_kind(row.label)
            if kind == "construction" and on:
                construction = True
            elif kind == "operation" and on:
                operation = True
        phase: TimelinePhase | None = None
        if construction:
            phase = "construction"
        elif operation:
            phase = "operation"
        if phase == last_phase and phase is not None:
            run += 1
        elif phase is not None:
            run = 1
            last_phase = phase
        else:
            run = 0
            last_phase = None
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
                phase=phase,
                phase_year=run if phase else None,
                calendar_year=calendar,
                start_date=getattr(header, "start_date", None),
                end_date=getattr(header, "end_date", None),
                flags=flag_map,
                hidden=bool(getattr(header, "hidden", False)),
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
        periods=periods,
    )


def _phase_kind(label: str) -> TimelinePhase | None:
    """Phase a 0/1 flag row marks: "Construction", "Флаг строительства", "Операционный период".

    A start/end/commissioning event flag ("Дата начала строительства", "Ввод в
    эксплуатацию") is one period, not a phase. Words come from the layout lexicon.
    """
    n = normalize_label(label)

    def has(*path: str) -> bool:
        return searcher(*path).search(n) is not None

    if has("phase_events"):
        return None
    # "Срок строительства" and "Длительность эксплуатации" name a length, not a phase.
    if has("operation_duration"):
        return None
    if has("phases", "construction"):
        return "construction"
    if has("phases", "operation"):
        return "operation"
    return None


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
    try:
        return abs(float(blob) - 1.0) < 1e-9
    except ValueError:
        return False


def _duration_warnings(
    layout: Layout,
    periods: list[ContextPeriod],
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
) -> list[str]:
    construction_n = sum(1 for item in periods if item.phase == "construction")
    operation_n = sum(1 for item in periods if item.phase == "operation")
    out: list[str] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            if getattr(block, "kind", "timeline") != "params" or block.axis is None:
                continue
            value_cols = [h.col for h in block.axis.headers if h.role == "value"] or [
                h.col for h in block.axis.headers if h.role in {"value", "scenario"}
            ]
            if not value_cols:
                continue
            col = value_cols[0]
            for row in block.rows:
                if row.kind != "fact":
                    continue
                n = normalize_label(row.label)
                assumed = _numeric_cell(by_addr, sheet.name, row.row, col, date1904)
                if assumed is None:
                    continue
                if (
                    "construction" in n
                    and "duration" in n
                    and construction_n
                    and assumed != construction_n
                ):
                    out.append(
                        "Construction Duration "
                        f"{assumed:g} does not match construction timeline length {construction_n}"
                    )
                if (
                    "operation" in n
                    and "duration" in n
                    and operation_n
                    and assumed != operation_n
                ):
                    out.append(
                        "Operations Duration "
                        f"{assumed:g} does not match operation timeline length {operation_n}"
                    )
                if (
                    "concession" in n
                    and "duration" in n
                    and construction_n
                    and operation_n
                    and assumed != construction_n + operation_n
                ):
                    out.append(
                        "Concession Duration "
                        f"{assumed:g} does not match construction+operation "
                        f"timeline length {construction_n + operation_n}"
                    )
    return out


def _numeric_cell(
    by_addr: dict[tuple[str, int, int], dict],
    sheet: str,
    row: int,
    col: int,
    date1904: bool,
) -> float | None:
    cell = by_addr.get((sheet, row, col))
    if cell is None:
        return None
    text = display_cell_text(
        cell.get("cached_value") if cell.get("cached_value") is not None else None,
        cell.get("number_format"),
        date1904=date1904,
    )
    blob = (text or str(cell.get("cached_value") or "")).strip().replace(" ", "").replace(",", ".")
    try:
        return float(blob)
    except ValueError:
        return None
