from __future__ import annotations

from finance_context.models.context import ContextDocument, FinancialBlock, MetricSeries

_MD_ESCAPE = str.maketrans({"|": "\\|", "\n": " "})


def render_markdown(
    doc: ContextDocument,
    *,
    max_columns: int = 12,
    max_rows: int = 80,
) -> str:
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
        "",
    ]
    if doc.warnings:
        lines.extend(["## Warnings", ""])
        for warning in doc.warnings[:20]:
            lines.append(f"- {_cell(warning)}")
        lines.append("")
    for block in doc.blocks:
        lines.extend(_block_section(block, max_columns=max_columns, max_rows=max_rows))
    if doc.unmapped:
        lines.extend(["## Unmapped rows", ""])
        for series in doc.unmapped[:max_rows]:
            conf = series.mapping.confidence or "low"
            lines.append(
                f"- {_cell(series.label)} (`{series.source.cell_ref}`, confidence={conf})"
            )
        if len(doc.unmapped) > max_rows:
            lines.append(f"- … {len(doc.unmapped) - max_rows} more in JSON")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _block_section(block: FinancialBlock, *, max_columns: int, max_rows: int) -> list[str]:
    headers = block.periods[:max_columns]
    title = f"## {block.sheet} / `{block.block_id}`"
    grain = f" grain={block.grain}" if block.grain else ""
    lines = [title, "", f"Periods:{grain} {len(block.periods)}. Metrics: {len(block.metrics)}.", ""]
    if not block.metrics or not headers:
        return lines
    period_keys = [str(p.get("period_key") or p.get("text")) for p in headers]
    cols = ["Label", "Concept", "Ref", *period_keys]
    lines.append("| " + " | ".join(_cell(c) for c in cols) + " |")
    lines.append("| " + " | ".join("---" for _ in cols) + " |")
    for series in block.metrics[:max_rows]:
        lines.append(_metric_row(series, headers))
    if len(block.metrics) > max_rows or len(block.periods) > max_columns:
        lines.append("")
        lines.append("_Truncated in Markdown; full series remain in JSON._")
    lines.append("")
    return lines


def _metric_row(series: MetricSeries, headers: list[dict]) -> str:
    by_key = {v.period_key: v for v in series.values}
    cells = [
        _cell(series.label),
        _cell(series.concept_id or ""),
        _cell(series.source.cell_ref),
    ]
    for header in headers:
        value = by_key.get(str(header.get("period_key")))
        if value is None:
            cells.append("")
            continue
        shown = value.cached_value if value.cached_value not in (None, "") else ""
        mark = ""
        if value.formula:
            mark = "*"
        if value.missing_cached_value:
            mark = "?"
        conf = series.mapping.confidence or ""
        cells.append(_cell(f"{shown}{mark} `{value.source.cell_ref}` {conf}".strip()))
    return "| " + " | ".join(cells) + " |"


def _cell(value: object) -> str:
    return str(value).translate(_MD_ESCAPE).strip()
