from __future__ import annotations

from finance_context.layout.models import Layout, LayoutRow, TimeAxis
from finance_context.layout.periods import display_cell_text, is_calendar_key
from finance_context.layout.resolve import axes_for
from finance_context.mapping.normalize import normalize_label
from finance_context.models.context import ContextAxis, ContextPeriod, TimelinePhase

_SERIES_ROLES = {
    "historical",
    "forecast",
    "stub",
    "relative",
}
_BEGIN_END = ("beginning", "end of", "start date", "end date")


def build_axes(
    layout: Layout,
    cells: list[dict],
    *,
    date1904: bool = False,
) -> tuple[list[ContextAxis], list[str]]:
    by_addr = {(c["sheet"], int(c["row"]), int(c["col"])): c for c in cells}
    published = _published_axis_ids(layout)
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
            axes.append(built)
            if any(item.phase for item in built.periods):
                phased.append(built)
    warnings: list[str] = []
    if phased:
        master = max(phased, key=lambda item: sum(1 for period in item.periods if period.phase))
        warnings.extend(_duration_warnings(layout, master.periods, by_addr, date1904))
    return axes, warnings


def _published_axis_ids(layout: Layout) -> set[str]:
    ids: set[str] = set()
    for sheet in layout.sheets:
        for block in sheet.blocks:
            chosen = block.timeline_ids or block.axis_ids
            if chosen:
                ids.update(chosen)
            elif block.axis is not None:
                ids.add(block.axis.id)
    return ids


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
        if period.role in _SERIES_ROLES
        and period.period_key not in {"actual", "plan", "total", "stub"}
    ]
    if not periods_in:
        return None
    periods: list[ContextPeriod] = []
    last_phase: TimelinePhase | None = None
    run = 0
    grain = axis.grain
    for index, header in enumerate(periods_in, start=1):
        flag_map: dict[str, bool] = {}
        construction = False
        operation = False
        for row in flags:
            on = _flag_on(by_addr, sheet_name, row.row, header.col, date1904)
            key = normalize_label(row.label)
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
                role=header.role,
                period_key=header.period_key,
                group_key=header.group_key,
                index=index,
                phase=phase,
                phase_year=run if phase else None,
                calendar_year=calendar,
                start_date=getattr(header, "start_date", None),
                end_date=getattr(header, "end_date", None),
                flags=flag_map,
            )
        )
    live = {name for period in periods for name, on in period.flags.items() if on}
    for period in periods:
        period.flags = {name: on for name, on in period.flags.items() if name in live}
    _promote_forecast_roles(periods, periods_in)
    return ContextAxis(
        id=axis.id,
        sheet=sheet_name,
        grain=grain,
        header_row=axis.header_row,
        periods=periods,
    )


def _promote_forecast_roles(periods: list[ContextPeriod], headers) -> None:
    """A construction/operation axis is a forecast, unless the header says actual.

    Bare years and end-dates default to historical. On a project timeline that
    already has a phase, that default is the model forecast, not reported actuals.
    An explicit `2023A` / `факт` header keeps `historical`.
    """
    if not any(period.phase for period in periods):
        return
    for period, header in zip(periods, headers, strict=True):
        if period.role == "historical" and not getattr(header, "explicit_role", False):
            period.role = "forecast"


def _phase_kind(label: str) -> TimelinePhase | None:
    n = normalize_label(label)
    if any(token in n for token in _BEGIN_END):
        return None
    tokens = set(n.split())
    if "construction" in tokens:
        return "construction"
    if tokens & {"operation", "operations", "operating"} and "duration" not in tokens:
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
