from __future__ import annotations

from finance_context.context.measure import Measure
from finance_context.excel.a1 import index_to_col
from finance_context.models.context import (
    BlockRow,
    ContextDocument,
    FinancialBlock,
    WorkbookTimeline,
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
    if doc.timeline and doc.timeline.periods:
        lines.extend(_timeline_section(doc.timeline))
    if doc.warnings:
        lines.extend(["## Warnings", ""])
        for warning in doc.warnings:
            lines.append(f"- {_cell(warning)}")
        lines.append("")
    for block in doc.blocks:
        lines.extend(_block_section(block))
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


def _timeline_section(timeline: WorkbookTimeline) -> list[str]:
    lines = [
        "## Timeline",
        "",
        (
            f"Grain: `{timeline.grain or 'n/a'}`. "
            f"Source: `{timeline.source_block_id or 'n/a'}`. "
            f"Periods: {len(timeline.periods)}."
        ),
        "",
        "| Period | Phase | Phase year | Calendar | Flags |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in timeline.periods:
        flags = ", ".join(name for name, on in item.flags.items() if on)
        lines.append(
            "| "
            f"{_cell(item.period_id)} | "
            f"{_cell(item.phase or '')} | "
            f"{item.phase_year if item.phase_year is not None else ''} | "
            f"{_cell(item.calendar_year or '')} | "
            f"{_cell(flags)} |"
        )
    lines.append("")
    return lines


def _block_section(block: FinancialBlock) -> list[str]:
    grain = f" grain={block.grain}" if block.grain else ""
    title = (
        f"## Parameters / {block.sheet}"
        if block.kind == "params"
        else f"## {block.sheet} / `{block.block_id}`"
    )
    lines = [
        title,
        "",
        (
            f"Block: `{block.block_id}`. Kind: `{block.kind}`.{grain} "
            f"Periods: {len(block.periods)}. Rows: {len(block.rows)}."
        ),
        "",
    ]
    if not block.rows:
        return lines
    headers = [_period_label(item) for item in block.periods]
    cols = ["Label", "Row", "Kind", "Disposition", "Concept", "Unit", "Time", "Formula", *headers]
    lines.append("| " + " | ".join(_cell(col) for col in cols) + " |")
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for row in block.rows:
        lines.append(_row_line(row, len(block.periods)))
    lines.append("")
    if block.relations:
        lines.extend(_relations_section(block.relations))
    return lines


def _row_line(row: BlockRow, period_count: int) -> str:
    unit = _unit_label(row)
    concept = row.concept_id or ""
    confidence = row.mapping.confidence if row.mapping and row.mapping.confidence else ""
    if concept and confidence:
        concept = f"{concept} ({confidence})"
    values = list(row.values)
    statuses = list(row.value_statuses)
    normalized = list(row.normalized_values)
    if len(values) < period_count:
        values.extend([None] * (period_count - len(values)))
    cells = [
        _cell(row.label),
        _cell(row.row_key),
        _cell(row.kind),
        _cell(row.disposition or ""),
        _cell(concept),
        _cell(unit),
        _cell(_time_label(row)),
        _cell(row.formula or ""),
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
