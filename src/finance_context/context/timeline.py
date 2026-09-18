from __future__ import annotations

from finance_context.layout.models import Block, Layout, LayoutRow
from finance_context.layout.periods import display_cell_text, infer_grain, is_calendar_key
from finance_context.mapping.normalize import normalize_label
from finance_context.models.context import ModelPeriod, TimelinePhase, WorkbookTimeline

_SERIES_ROLES = {
    "historical",
    "forecast",
    "stub",
    "relative",
}
_BEGIN_END = ("beginning", "end of", "start date", "end date")


def build_timeline(
    layout: Layout,
    cells: list[dict],
    *,
    date1904: bool = False,
) -> tuple[WorkbookTimeline | None, list[str]]:
    by_addr = {(c["sheet"], int(c["row"]), int(c["col"])): c for c in cells}
    master, grain, flags = _master_axis(layout)
    warnings: list[str] = []
    if master is None:
        return None, warnings
    sheet = _sheet_name(layout, master)
    headers = [
        header
        for header in master.axis.headers
        if header.role in _SERIES_ROLES and header.period_key not in {"actual", "plan", "total", "stub"}
    ]
    if not headers:
        return None, warnings
    periods: list[ModelPeriod] = []
    last_phase: TimelinePhase | None = None
    run = 0
    for index, header in enumerate(headers, start=1):
        flag_map: dict[str, bool] = {}
        construction = False
        operation = False
        for row in flags:
            on = _flag_on(by_addr, sheet, row.row, header.col, date1904)
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
            ModelPeriod(
                period_id=header.period_key,
                index=index,
                phase=phase,
                phase_year=run if phase else None,
                calendar_year=calendar,
                flags=flag_map,
            )
        )
    warnings.extend(_duration_warnings(layout, periods, by_addr, date1904))
    return (
        WorkbookTimeline(
            grain=grain,
            source_block_id=master.block_id,
            periods=periods,
        ),
        warnings,
    )


def annotate_block_periods(
    periods: list[dict],
    timeline: WorkbookTimeline | None,
) -> list[dict]:
    if timeline is None:
        return periods
    by_id = {item.period_id: item for item in timeline.periods}
    out: list[dict] = []
    for item in periods:
        key = str(item.get("period_key") or "")
        hit = by_id.get(key)
        extra = dict(item)
        if hit is not None:
            extra["phase"] = hit.phase
            extra["phase_year"] = hit.phase_year
        out.append(extra)
    return out


def _master_axis(layout: Layout) -> tuple[Block | None, str | None, list[LayoutRow]]:
    scored: list[tuple[int, int, Block, str | None, list[LayoutRow]]] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            if getattr(block, "kind", "timeline") != "timeline":
                continue
            grain = infer_grain([h.period_key for h in block.axis.headers])
            flag_rows = [row for row in block.rows if row.kind == "flag"]
            n_periods = sum(
                1
                for header in block.axis.headers
                if header.role in _SERIES_ROLES
            )
            if grain and str(grain).startswith("model_") and flag_rows:
                scored.append((len(flag_rows), n_periods, block, grain, flag_rows))
            elif grain and not str(grain).startswith("model_"):
                scored.append((len(flag_rows), n_periods, block, grain, flag_rows))
    if not scored:
        return None, None, []
    flagged = [item for item in scored if item[0] > 0 and item[3] and str(item[3]).startswith("model_")]
    pool = flagged or scored
    pool.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _n_flags, _n_per, block, grain, flags = pool[0]
    return block, grain, flags


def _sheet_name(layout: Layout, block: Block) -> str:
    for sheet in layout.sheets:
        if block in sheet.blocks or any(item.block_id == block.block_id for item in sheet.blocks):
            return sheet.name
    return ""


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
    periods: list[ModelPeriod],
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
) -> list[str]:
    construction_n = sum(1 for item in periods if item.phase == "construction")
    operation_n = sum(1 for item in periods if item.phase == "operation")
    out: list[str] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            if getattr(block, "kind", "timeline") != "params":
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
                if "construction" in n and "duration" in n and construction_n and assumed != construction_n:
                    out.append(
                        f"Construction Duration {assumed:g} does not match construction timeline length {construction_n}"
                    )
                if "operation" in n and "duration" in n and operation_n and assumed != operation_n:
                    out.append(
                        f"Operations Duration {assumed:g} does not match operation timeline length {operation_n}"
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
