from __future__ import annotations

from finance_context.models.context import ContextDocument, FinancialBlock, MetricSeries

_MD_ESCAPE = str.maketrans({"|": "\\|", "\n": " "})


def render_markdown(
    doc: ContextDocument,
    *,
    max_columns: int = 16,
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
    by_block = _unmapped_by_block(doc.unmapped)
    for block in doc.blocks:
        extra = by_block.pop(block.block_id, [])
        lines.extend(
            _block_section(block, unmapped=extra, max_columns=max_columns, max_rows=max_rows)
        )
    for block_id, leftover in by_block.items():
        lines.extend(
            _orphan_unmapped_section(
                block_id, leftover, max_columns=max_columns, max_rows=max_rows
            )
        )
    return "\n".join(lines).rstrip() + "\n"


def _unmapped_by_block(rows: list[MetricSeries]) -> dict[str, list[MetricSeries]]:
    grouped: dict[str, list[MetricSeries]] = {}
    for series in rows:
        block_id = series.row_key.rsplit("|", 1)[-1] if "|" in series.row_key else series.row_key
        grouped.setdefault(block_id, []).append(series)
    return grouped


def _block_section(
    block: FinancialBlock,
    *,
    unmapped: list[MetricSeries],
    max_columns: int,
    max_rows: int,
) -> list[str]:
    headers = block.periods
    if max_columns > 0 and len(headers) > max_columns:
        headers = block.periods[:max_columns]
    grain = f" grain={block.grain}" if block.grain else ""
    lines = [
        f"## {block.sheet} / `{block.block_id}`",
        "",
        (
            f"Periods:{grain} {len(block.periods)}. "
            f"Metrics: {len(block.metrics)}. Unmapped: {len(unmapped)}."
        ),
        "",
    ]
    if not headers:
        return lines
    truncated = (max_columns > 0 and len(block.periods) > max_columns) or (
        len(block.metrics) > max_rows
    ) or (len(unmapped) > max_rows)
    lines.extend(
        _period_table(block.metrics, headers, max_rows=max_rows)
    )
    if unmapped:
        lines.extend(["### Unmapped", ""])
        lines.extend(_period_table(unmapped, headers, max_rows=max_rows))
    if truncated:
        lines.append("_Truncated in Markdown; full series remain in JSON._")
        lines.append("")
    return lines


def _orphan_unmapped_section(
    block_id: str,
    series: list[MetricSeries],
    *,
    max_columns: int,
    max_rows: int,
) -> list[str]:
    if not series:
        return []
    periods = _periods_from_series(series[0])
    headers = periods[:max_columns] if max_columns > 0 else periods
    sheet = series[0].source.sheet
    lines = [
        f"## {sheet} / `{block_id}`",
        "",
        f"Unmapped: {len(series)}.",
        "",
        "### Unmapped",
        "",
    ]
    if not headers:
        return lines
    truncated = (max_columns > 0 and len(periods) > max_columns) or len(series) > max_rows
    lines.extend(_period_table(series, headers, max_rows=max_rows))
    if truncated:
        lines.append("_Truncated in Markdown; full series remain in JSON._")
        lines.append("")
    return lines


def _periods_from_series(series: MetricSeries) -> list[dict]:
    return [
        {
            "col": value.source.col,
            "text": value.header_text,
            "role": value.role,
            "period_key": value.period_key,
        }
        for value in series.values
    ]


def _period_table(
    metrics: list[MetricSeries],
    headers: list[dict],
    *,
    max_rows: int,
) -> list[str]:
    if not metrics:
        return []
    period_labels = [_period_label(item) for item in headers]
    cols = ["Label", "Concept", "Ref", *period_labels]
    lines = [
        "| " + " | ".join(_cell(c) for c in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    for series in metrics[:max_rows]:
        lines.append(_metric_row(series, headers))
    lines.append("")
    return lines


def _metric_row(series: MetricSeries, headers: list[dict]) -> str:
    by_col = {v.source.col: v for v in series.values}
    conf = series.mapping.confidence or ""
    concept = series.concept_id or "unknown"
    if series.concept_id and conf:
        concept = f"{series.concept_id} ({conf})"
    cells = [
        _cell(series.label),
        _cell(concept),
        _cell(series.source.cell_ref),
    ]
    for header in headers:
        col = header.get("col")
        value = by_col.get(int(col)) if col is not None else None
        if value is None:
            cells.append("")
            continue
        shown = _format_value(value.cached_value)
        mark = ""
        if value.formula:
            mark = "*"
        if value.missing_cached_value:
            mark = "?"
        cells.append(_cell(f"{shown}{mark} `{value.source.cell_ref}`".strip()))
    return "| " + " | ".join(cells) + " |"


def _period_label(header: dict) -> str:
    text = str(header.get("text") or "").strip()
    key = str(header.get("period_key") or "").strip()
    return text or key


def _format_value(raw: str | None) -> str:
    if raw in (None, ""):
        return ""
    text = str(raw).strip()
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        return text
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.4f}".rstrip("0").rstrip(".")


def _cell(value: object) -> str:
    return str(value).translate(_MD_ESCAPE).strip()
