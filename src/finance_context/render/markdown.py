from __future__ import annotations

from finance_context.context.measure import Measure
from finance_context.excel.a1 import index_to_col
from finance_context.models.context import (
    BlockRow,
    ContextAxis,
    ContextDocument,
    FinancialBlock,
    RowSeries,
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


def _visible_attributes(axis: ContextAxis) -> list[str]:
    periods = axis.periods
    attributes: list[str] = []
    if any(period.start_date for period in periods):
        attributes.append("Start")
    if any(period.end_date for period in periods):
        attributes.append("End")
    if any(period.group_key for period in periods):
        attributes.append("Group")
    if any(period.phase for period in periods):
        attributes.append("Phase")
    if any(period.phase_year is not None for period in periods):
        attributes.append("Phase year")
    if any(period.calendar_year and not _redundant_calendar(period) for period in periods):
        attributes.append("Calendar")
    if any(period.flags for period in periods):
        attributes.append("Flags")
    return attributes


def _axis_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _period_table(axis: ContextAxis) -> list[str]:
    """One column per period; attributes such as phase are rows under the period keys."""
    header = [_cell(axis.id), *[_cell(period.period_key) for period in axis.periods]]
    lines = [_axis_row(header), _axis_row(["---"] * len(header))]
    for attribute in _visible_attributes(axis):
        cells = [attribute, *[_period_cell(attribute, period) for period in axis.periods]]
        lines.append(_axis_row(cells))
    return lines


def _period_cell(attribute: str, period) -> str:
    if attribute == "Start":
        return _cell(period.start_date or "")
    if attribute == "End":
        return _cell(period.end_date or "")
    if attribute == "Group":
        return _cell(period.group_key or "")
    if attribute == "Phase":
        return _cell(period.phase or "")
    if attribute == "Phase year":
        return "" if period.phase_year is None else str(period.phase_year)
    if attribute == "Calendar":
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
    header = [_cell(axis.id), *[_cell(group) for group, _keys in runs]]
    spans = [keys[0] if keys[0] == keys[-1] else f"{keys[0]} .. {keys[-1]}" for _g, keys in runs]
    return [
        _axis_row(header),
        _axis_row(["---"] * len(header)),
        _axis_row(["Periods", *[_cell(span) for span in spans]]),
    ]


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
    if referenced:
        for axis in referenced:
            if len(referenced) > 1:
                lines.extend([f"### `{axis.id}`", ""])
            headers = [
                {
                    "text": period.text,
                    "period_key": period.period_key,
                    "col": period.col,
                }
                for period in axis.periods
            ]
            lines.extend(_value_table(block.rows, headers, axis.id))
    else:
        lines.extend(_value_table(block.rows, block.periods, None))
    if block.relations:
        lines.extend(_relations_section(block.relations))
    return lines


def _value_table(rows: list[BlockRow], periods: list[dict], axis_id: str | None) -> list[str]:
    headers = [_period_label(item) for item in periods]
    cols = [
        "Label",
        "Row",
        "Path",
        "Kind",
        "Disposition",
        "Concept",
        "Unit",
        "Time",
        "Formula",
        "Cells",
        *headers,
    ]
    lines = [
        "| " + " | ".join(_cell(col) for col in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    period_cols = {item.get("col") for item in periods}
    for row in rows:
        series = _series_for(row, axis_id)
        lines.append(_row_line(row, len(periods), series, period_cols))
    lines.append("")
    return lines


def _role_cells_label(row: BlockRow, period_cols: set) -> str:
    """Cells left of the ruler: `L total: -86400`, `G Start: 01.01.2024`.

    Units have their own column.
    """
    parts: list[str] = []
    for item in row.cells:
        if item.role == "unit" or item.col in period_cols or item.cached_value in (None, ""):
            continue
        letter = _column_letter(item.col)
        name = item.header or item.role
        parts.append(f"{letter} {name}: {item.cached_value}")
    return "; ".join(parts)


def _series_for(row: BlockRow, axis_id: str | None) -> RowSeries | None:
    if axis_id is None:
        return None
    return next((item for item in row.series if item.axis_id == axis_id), None)


def _row_line(
    row: BlockRow,
    period_count: int,
    series: RowSeries | None = None,
    period_cols: set | None = None,
) -> str:
    unit = _unit_label(row)
    concept = row.concept_id or ""
    confidence = row.mapping.confidence if row.mapping and row.mapping.confidence else ""
    if concept and confidence:
        concept = f"{concept} ({confidence})"
    source_values = series.values if series is not None else row.values
    source_statuses = series.value_statuses if series is not None else row.value_statuses
    source_normalized = series.normalized_values if series is not None else row.normalized_values
    formula = series.formula if series is not None and series.formula else row.formula
    values = list(source_values)
    statuses = list(source_statuses)
    normalized = list(source_normalized)
    if len(values) < period_count:
        values.extend([None] * (period_count - len(values)))
    cells = [
        _cell(row.label),
        _cell(row.row_key),
        _cell(" / ".join(row.label_path)),
        _cell(row.kind),
        _cell(row.disposition or ""),
        _cell(concept),
        _cell(unit),
        _cell(_time_label(row)),
        _cell(formula or ""),
        _cell(_role_cells_label(row, period_cols or set())),
        *[
            _cell(_series_cell(value, _at(statuses, index), _at(normalized, index)))
            for index, value in enumerate(values[:period_count])
        ],
    ]
    return "| " + " | ".join(cells) + " |"


def _unit_label(row: BlockRow) -> str:
    unit = next((cell.cached_value for cell in row.cells if cell.role == "unit"), None)
    if not unit:
        unit = (
            Measure(
                unit=row.hints.unit,
                currency=row.hints.currency,
                scale=row.hints.scale,
                per=row.hints.unit_per,
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
    if normalized and text and _differs(text, normalized):
        return f"{text} ({normalized})"
    return text


def _differs(text: str, normalized: str) -> bool:
    """Show the base-unit amount only when scale changes it, not when formatting does."""
    try:
        value = float(text.replace(",", ""))
        base = float(normalized.replace(",", ""))
    except ValueError:
        return normalized != text
    return abs(value - base) > 1e-9 * max(1.0, abs(value), abs(base))


def _at(items: list[str | None], index: int) -> str | None:
    if index >= len(items):
        return None
    return items[index]


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
