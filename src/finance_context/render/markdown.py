from __future__ import annotations

from finance_context.context.measure import Measure
from finance_context.excel.a1 import index_to_col
from finance_context.models.context import (
    BlockRow,
    ContextAxis,
    ContextDocument,
    FinancialBlock,
    RowSeries,
    SeriesPoint,
)

_MD_ESCAPE = str.maketrans({"|": "\\|", "\n": " "})


def render_markdown(doc: ContextDocument) -> str:
    """Markdown with the same blocks, rows, and values as context.json."""
    lines: list[str] = [
        "# Financial context",
        "",
        f"- Schema: `{doc.schema_version}`",
        f"- Job: `{doc.meta.job_id}`",
        f"- Status: `{doc.meta.status}`",
        f"- Source: `{doc.meta.source_filename or 'n/a'}`",
        f"- Sheets: {doc.workbook.sheet_count}",
        f"- Cells: {doc.workbook.cell_count}",
        f"- Formulas: {doc.workbook.formula_count}",
        *_coverage_lines(doc),
        "",
    ]
    if doc.axes:
        lines.extend(_axes_section(doc.axes))
    if doc.warnings:
        lines.extend(["## Warnings", ""])
        for warning in doc.warnings:
            lines.append(f"- {_cell(warning)}")
        lines.append("")
    axes_by_id = {axis.id: axis for axis in doc.axes}
    for block in doc.blocks:
        lines.extend(_block_section(block, axes_by_id))
    return "\n".join(lines).rstrip() + "\n"


def _coverage_lines(doc: ContextDocument) -> list[str]:
    stats = doc.mapping_stats
    annotatable = stats.mapped + stats.abstained
    return [
        (
            f"- Content completeness: {stats.content_completeness:.2f} "
            f"({stats.inventory_rows}/{stats.inventory_rows} layout rows)"
        ),
        (
            f"- Concept coverage: {stats.concept_coverage:.2f} "
            f"({stats.mapped}/{annotatable} annotatable)"
        ),
        *_quality_lines(doc),
    ]


def _quality_lines(doc: ContextDocument) -> list[str]:
    quality = doc.mapping_stats.mapping_quality
    passed = "true" if quality.confidence_threshold_passed else "false"
    return [
        f"- Label coverage: {quality.label_coverage:.2f}",
        f"- Semantic coverage: {quality.semantic_coverage:.2f}",
        f"- Unit coverage: {quality.unit_coverage:.2f}",
        f"- Temporal coverage: {quality.temporal_coverage:.2f}",
        f"- Formula coverage: {quality.formula_coverage:.2f}",
        f"- Confidence threshold passed: {passed}",
    ]


def _axes_section(axes: list[ContextAxis]) -> list[str]:
    lines = ["## Axes", ""]
    for axis in axes:
        lines.extend(
            [
                f"### `{axis.id}`",
                "",
                (
                    f"Grain: `{axis.grain or 'n/a'}`. "
                    f"Sheet: `{axis.sheet}`. "
                    f"Periods: {len(axis.periods)}."
                ),
                "",
            ]
        )
        if _month_runs(axis) is not None:
            lines.extend(_month_summary(axis))
        else:
            lines.extend(_period_table(axis))
        lines.append("")
    return lines


def _redundant_calendar(period) -> bool:
    calendar = period.calendar_year
    if not calendar:
        return True
    key = period.period_key
    return calendar == key or calendar == key[:4]


def _visible_columns(axis: ContextAxis) -> list[str]:
    periods = axis.periods
    columns = ["Period"]
    if any(period.group_key for period in periods):
        columns.append("Group")
    if any(period.phase for period in periods):
        columns.append("Phase")
    if any(period.phase_year is not None for period in periods):
        columns.append("Phase year")
    if any(period.calendar_year and not _redundant_calendar(period) for period in periods):
        columns.append("Calendar")
    if any(period.flags for period in periods):
        columns.append("Flags")
    return columns


def _period_table(axis: ContextAxis) -> list[str]:
    columns = _visible_columns(axis)
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for period in axis.periods:
        cells = [_period_cell(column, period) for column in columns]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _period_cell(column: str, period) -> str:
    if column == "Period":
        return _cell(period.period_key)
    if column == "Group":
        return _cell(period.group_key or "")
    if column == "Phase":
        return _cell(period.phase or "")
    if column == "Phase year":
        return "" if period.phase_year is None else str(period.phase_year)
    if column == "Calendar":
        if _redundant_calendar(period):
            return ""
        return _cell(period.calendar_year or "")
    flags = ", ".join(name for name, on in period.flags.items() if on)
    return _cell(flags)


def _month_key(period_key: str) -> tuple[int, int] | None:
    if len(period_key) == 7 and period_key[4] == "-" and period_key[:4].isdigit():
        month = period_key[5:7]
        if month.isdigit():
            return int(period_key[:4]), int(month)
    return None


def _month_runs(axis: ContextAxis) -> list[tuple[str, list[str]]] | None:
    """Year lines for a month axis with no phases and a stable group inside each year."""
    if any(period.phase or period.flags for period in axis.periods):
        return None
    parsed: list[tuple[str, str]] = []
    for period in axis.periods:
        if _month_key(period.period_key) is None:
            return None
        parsed.append((period.group_key or period.period_key[:4], period.period_key))
    runs: list[tuple[str, list[str]]] = []
    groups_in_year: dict[str, set[str | None]] = {}
    for period in axis.periods:
        year = period.period_key[:4]
        groups_in_year.setdefault(year, set()).add(period.group_key)
    if any(len(groups) > 1 for groups in groups_in_year.values()):
        return None
    for group, key in parsed:
        if runs and runs[-1][0] == group:
            runs[-1][1].append(key)
        else:
            runs.append((group, [key]))
    return runs


def _month_summary(axis: ContextAxis) -> list[str]:
    runs = _month_runs(axis) or []
    lines = ["| Group | Periods |", "| --- | --- |"]
    for group, keys in runs:
        span = keys[0] if keys[0] == keys[-1] else f"{keys[0]} .. {keys[-1]}"
        lines.append(f"| {_cell(group)} | {_cell(span)} |")
    return lines


def _block_section(block: FinancialBlock, axes_by_id: dict[str, ContextAxis]) -> list[str]:
    referenced = [axes_by_id[axis_id] for axis_id in block.axis_ids if axis_id in axes_by_id]
    grain = f" grain={block.grain}" if block.grain else ""
    title = (
        f"## Parameters / {block.sheet}"
        if block.kind == "params"
        else f"## {block.sheet} / `{block.block_id}`"
    )
    if referenced:
        names = ", ".join(f"`{axis.id}`" for axis in referenced)
        summary = (
            f"Block: `{block.block_id}`. Kind: `{block.kind}`. "
            f"Axes: {names}. Rows: {len(block.rows)}."
        )
    else:
        summary = (
            f"Block: `{block.block_id}`. Kind: `{block.kind}`.{grain} "
            f"Periods: {len(block.periods)}. Rows: {len(block.rows)}."
        )
    lines = [title, "", summary, ""]
    if not block.rows:
        return lines
    if block.kind == "params":
        lines.extend(_value_table(block.rows, block.periods, None))
    elif referenced:
        for axis in referenced:
            if len(referenced) > 1:
                lines.extend([f"### `{axis.id}`", ""])
            headers = [_header_dict(period) for period in axis.periods]
            lines.extend(_timeline_rows(block.rows, headers, axis.id))
    else:
        lines.extend(_timeline_rows(block.rows, block.periods, None))
    if block.relations:
        lines.extend(_relations_section(block.relations))
    return lines


def _header_dict(period) -> dict:
    return {
        "text": period.text,
        "period_key": period.period_key,
        "col": period.col,
        "phase": period.phase,
    }


def _timeline_rows(rows: list[BlockRow], periods: list[dict], axis_id: str | None) -> list[str]:
    show_phase = any(item.get("phase") for item in periods)
    lines: list[str] = []
    for row in rows:
        series = _series_for(row, axis_id)
        points = _points_for(row, series, axis_id)
        formula = series.formula if series is not None and series.formula else row.formula
        lines.extend(_series_heading(row, formula))
        lines.extend(_period_value_table(periods, points, show_phase))
    return lines


def _series_heading(row: BlockRow, formula: str | None) -> list[str]:
    concept = row.concept_id or ""
    confidence = row.mapping.confidence if row.mapping and row.mapping.confidence else ""
    if concept and confidence:
        concept = f"{concept} ({confidence})"
    identity = [f"Kind: {_cell(row.kind)}"]
    if row.disposition:
        identity.append(f"Disposition: {_cell(row.disposition)}")
    if concept:
        identity.append(f"Concept: {_cell(concept)}")
    lines = [
        f"#### {_cell(row.label)}",
        "",
        f"- Row: `{row.row_key}`",
        "- " + " · ".join(identity),
    ]
    unit = _unit_label(row)
    if unit:
        lines.append(f"- Unit: {_cell(unit)}")
    time_label = _time_label(row)
    if time_label:
        lines.append(f"- Time: {_cell(time_label)}")
    if formula:
        lines.append(f"- Formula: `{_cell(formula)}`")
    lines.append("")
    return lines


def _period_value_table(
    periods: list[dict], points: list[SeriesPoint], show_phase: bool
) -> list[str]:
    columns = ["Period", "Value"] if not show_phase else ["Period", "Phase", "Value"]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for index, header in enumerate(periods):
        point = points[index] if index < len(points) else None
        value = (
            "empty"
            if point is None
            else _series_cell(point.value, point.value_status, point.normalized_value)
        )
        cells = [_cell(_period_label(header))]
        if show_phase:
            cells.append(_cell(header.get("phase") or ""))
        cells.append(_cell(value))
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return lines


def _value_table(rows: list[BlockRow], periods: list[dict], axis_id: str | None) -> list[str]:
    headers = [_period_label(item) for item in periods]
    cols = ["Label", "Row", "Kind", "Disposition", "Concept", "Unit", "Time", "Formula", *headers]
    lines = [
        "| " + " | ".join(_cell(col) for col in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for row in rows:
        series = _series_for(row, axis_id)
        lines.append(_row_line(row, periods, series, axis_id))
    lines.append("")
    return lines


def _series_for(row: BlockRow, axis_id: str | None) -> RowSeries | None:
    if axis_id is None:
        return row.series[0] if row.series else None
    return next((item for item in row.series if item.axis_id == axis_id), None)


def _points_for(row: BlockRow, series: RowSeries | None, axis_id: str | None) -> list[SeriesPoint]:
    if series is not None and (axis_id is not None or not row.points):
        return list(series.points)
    return list(row.points)


def _row_line(
    row: BlockRow, periods: list[dict], series: RowSeries | None, axis_id: str | None
) -> str:
    unit = _unit_label(row)
    concept = row.concept_id or ""
    confidence = row.mapping.confidence if row.mapping and row.mapping.confidence else ""
    if concept and confidence:
        concept = f"{concept} ({confidence})"
    points = _points_for(row, series, axis_id)
    formula = series.formula if series is not None and series.formula else row.formula
    cells = [
        _cell(row.label),
        _cell(row.row_key),
        _cell(row.kind),
        _cell(row.disposition or ""),
        _cell(concept),
        _cell(unit),
        _cell(_time_label(row)),
        _cell(formula or ""),
    ]
    for index, _header in enumerate(periods):
        point = points[index] if index < len(points) else None
        if point is None:
            cells.append("empty")
        else:
            cells.append(
                _cell(_series_cell(point.value, point.value_status, point.normalized_value))
            )
    return "| " + " | ".join(cells) + " |"


def _unit_label(row: BlockRow) -> str:
    unit = next((cell.cached_value for cell in row.cells if cell.role == "unit"), None)
    if not unit:
        unit = (
            Measure(
                unit=row.hints.unit,
                currency=row.hints.currency,
                scale=row.hints.scale,
            ).display()
            or row.unit
            or row.hints.unit
            or ""
        )
    if row.scale_factor not in (None, 1):
        base = unit or "unit"
        return f"{base} ×{row.scale_factor}"
    return unit or ""


def _time_label(row: BlockRow) -> str:
    semantics = row.hints.time_semantics
    if not any((semantics, row.period_position, row.aggregation)):
        return ""
    return "/".join(part or "-" for part in (semantics, row.period_position, row.aggregation))


def _series_cell(value: str | None, status: str | None, normalized: str | None) -> str:
    if status == "not_applicable":
        return "n/a"
    if status == "empty" or (status is None and value in (None, "")):
        return "empty"
    text = "" if value is None else str(value)
    if status == "zero_explicit":
        return text or "0"
    if normalized and text and normalized != text:
        return f"{text} ({normalized})"
    return text


def _relations_section(relations: list[dict]) -> list[str]:
    lines = [
        "Relations:",
        "",
        "| Kind | Source | Target | Members |",
        "| --- | --- | --- | --- |",
    ]
    for rel in relations:
        members = rel.get("member_row_keys") or []
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(rel.get("kind") or ""),
                    _cell(rel.get("source_row_key") or ""),
                    _cell(rel.get("target_row_key") or ""),
                    _cell(", ".join(str(key) for key in members)),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _period_label(header: dict) -> str:
    text = str(header.get("text") or "").strip()
    key = str(header.get("period_key") or "").strip()
    name = key if _prefers_period_key(text, key) else (text or key)
    letter = _column_letter(header.get("col"))
    if name and letter:
        return f"{name} ({letter})"
    return name or letter


def _prefers_period_key(text: str, key: str) -> bool:
    if not key:
        return False
    if not text or text == key:
        return True
    if key[0] in "YQMP" and key[1:].isdigit():
        return True
    return key[:4].isdigit()


def _column_letter(col: object) -> str:
    if isinstance(col, int) and col > 0:
        return index_to_col(col)
    return ""


def _cell(value: object) -> str:
    return str(value).translate(_MD_ESCAPE).strip()
