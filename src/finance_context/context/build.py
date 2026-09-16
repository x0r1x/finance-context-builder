from __future__ import annotations

from finance_context.excel.a1 import format_addr
from finance_context.layout.models import Layout
from finance_context.layout.periods import infer_grain
from finance_context.mapping.models import MappedRow, MappingDocument, MapSource, RowRelation
from finance_context.mapping.rules import is_noise_label
from finance_context.models.context import (
    SCHEMA_VERSION,
    ArtifactMeta,
    ContextDocument,
    FinancialBlock,
    MappingEvidence,
    MetricSeries,
    PeriodValue,
    SourceRef,
    WorkbookRaw,
)

_METHOD: dict[MapSource, str] = {
    "glossary": "rule",
    "rule": "rule",
    "lexical": "rule",
    "structure": "structure",
    "embed": "embed",
    "chat": "llm",
    "question": "unmapped",
}


def build_context(
    *,
    job_id: str,
    workbook_meta: dict,
    cells: list[dict],
    layout: Layout,
    mapping: MappingDocument,
    source_filename: str | None = None,
    content_sha256: str | None = None,
    status: str = "succeeded",
    stage: str = "done",
) -> ContextDocument:
    by_addr = {(c["sheet"], int(c["row"]), int(c["col"])): c for c in cells}
    mapped_by_key = {row.row_key: row for row in mapping.rows}
    warnings: list[str] = []
    formula_count = 0
    missing_cached = 0
    unparsed = 0
    for cell in cells:
        if cell.get("formula_raw"):
            formula_count += 1
            if not cell.get("cached_value"):
                missing_cached += 1
                warnings.append(
                    f"Formula without cached value at {cell['sheet']}!{cell['addr']}"
                )
            if cell.get("unparsed"):
                unparsed += 1
                warnings.append(f"Unparsed formula at {cell['sheet']}!{cell['addr']}")

    blocks: list[FinancialBlock] = []
    unmapped: list[MetricSeries] = []
    excluded: list[MetricSeries] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            grain = infer_grain([h.period_key for h in block.axis.headers])
            periods = [
                {
                    "col": header.col,
                    "text": header.text,
                    "role": header.role,
                    "period_key": header.period_key,
                }
                for header in block.axis.headers
            ]
            metrics: list[MetricSeries] = []
            parent_by_row = {r.row: r.label for r in block.rows}
            for layout_row in block.rows:
                if layout_row.kind != "fact":
                    continue
                if is_noise_label(layout_row.label):
                    continue
                row_key = f"{sheet.name}|{layout_row.row}|{block.block_id}"
                mapped = mapped_by_key.get(row_key)
                parent = None
                if layout_row.parent_row:
                    parent = parent_by_row.get(layout_row.parent_row)
                series = _series_for_row(
                    sheet_name=sheet.name,
                    block_id=block.block_id,
                    label_col=layout_row.label_col or block.label_col,
                    layout_row_label=layout_row.label,
                    layout_row_parent=parent,
                    mapped=mapped,
                    headers=block.axis.headers,
                    by_addr=by_addr,
                    row_num=layout_row.row,
                )
                if mapped is not None and mapped.disposition == "excluded":
                    excluded.append(series)
                elif mapped is None or mapped.concept_id is None:
                    unmapped.append(series)
                else:
                    metrics.append(series)
            blocks.append(
                FinancialBlock(
                    block_id=block.block_id,
                    sheet=sheet.name,
                    label_col=block.label_col,
                    grain=grain,
                    periods=periods,
                    metrics=metrics,
                    relations=_relations_for_block(mapping.relations, block.block_id),
                )
            )

    if mapping.questions:
        warnings.append(f"{len(mapping.questions)} row(s) need mapping review")
    if missing_cached:
        warnings.append(f"{missing_cached} formula cell(s) missing cached values")

    sheets = [
        s["name"] if isinstance(s, dict) else getattr(s, "name", str(s))
        for s in workbook_meta.get("sheets", [])
    ]
    workbook = WorkbookRaw(
        sheets=sheets,
        sheet_count=len(sheets),
        cell_count=len(cells),
        has_vba=bool(workbook_meta.get("has_vba")),
        has_xlm=bool(workbook_meta.get("has_xlm")),
        externals=list(workbook_meta.get("externals") or []),
        locale_hint=workbook_meta.get("locale_hint"),
        date1904=bool(workbook_meta.get("date1904")),
        defined_names=list(workbook_meta.get("defined_names") or []),
        formula_count=formula_count,
        missing_cached_values=missing_cached,
        unparsed_formulas=unparsed,
    )
    meta = ArtifactMeta(
        schema_version=SCHEMA_VERSION,
        job_id=job_id,
        status=status,  # type: ignore[arg-type]
        stage=stage,
        source_filename=source_filename,
        content_sha256=content_sha256,
        warnings=warnings[:50],
        questions=[q.model_dump(mode="json") for q in mapping.questions],
    )
    return ContextDocument(
        schema_version=SCHEMA_VERSION,
        meta=meta,
        workbook=workbook,
        blocks=blocks,
        unmapped=unmapped,
        excluded=excluded,
        warnings=warnings[:50],
    )


def _series_for_row(
    *,
    sheet_name: str,
    block_id: str,
    label_col: int,
    layout_row_label: str,
    layout_row_parent: str | None,
    mapped: MappedRow | None,
    headers: list,
    by_addr: dict[tuple[str, int, int], dict],
    row_num: int,
) -> MetricSeries:
    label_addr = format_addr(label_col, row_num)
    source = SourceRef(sheet=sheet_name, addr=label_addr, row=row_num, col=label_col)
    method = _METHOD.get(mapped.source, "unmapped") if mapped else "unmapped"
    evidence = MappingEvidence(
        method=method,  # type: ignore[arg-type]
        score=mapped.score if mapped else None,
        confidence=mapped.confidence if mapped else "low",
        alternatives=list(mapped.alternatives) if mapped else [],
        source=mapped.source if mapped else None,
        evidence=mapped.evidence if mapped else None,
        disposition=mapped.disposition if mapped else "abstained",
        exclusion_reason=mapped.exclusion_reason if mapped else None,
    )
    values: list[PeriodValue] = []
    for header in headers:
        cell = by_addr.get((sheet_name, row_num, header.col))
        addr = format_addr(header.col, row_num)
        formula = (cell or {}).get("formula_raw")
        cached = (cell or {}).get("cached_value")
        values.append(
            PeriodValue(
                period_key=header.period_key,
                header_text=header.text,
                role=header.role,
                cached_value=cached,
                formula=formula,
                formula_template=(cell or {}).get("formula_template"),
                unparsed=bool((cell or {}).get("unparsed")),
                number_format=(cell or {}).get("number_format"),
                source=SourceRef(sheet=sheet_name, addr=addr, row=row_num, col=header.col),
                missing_cached_value=bool(formula) and cached in (None, ""),
            )
        )
    return MetricSeries(
        row_key=mapped.row_key if mapped else f"{sheet_name}|{row_num}|{block_id}",
        label=mapped.label if mapped else layout_row_label,
        parent_label=mapped.parent_label if mapped else layout_row_parent,
        concept_id=mapped.concept_id if mapped else None,
        article_role=mapped.article_role if mapped else "database_like",
        unit=_unit_from_values(values),
        mapping=evidence,
        values=values,
        source=source,
        disposition=mapped.disposition if mapped else "abstained",
        exclusion_reason=mapped.exclusion_reason if mapped else None,
    )


def _relations_for_block(relations: list[RowRelation], block_id: str) -> list[dict]:
    out: list[dict] = []
    for rel in relations:
        keys = [rel.source_row_key, rel.target_row_key, *rel.member_row_keys]
        if any(key and key.endswith(f"|{block_id}") for key in keys):
            out.append(rel.model_dump(mode="json"))
    return out


def _unit_from_values(values: list[PeriodValue]) -> str | None:
    for item in values:
        fmt = item.number_format
        if not fmt:
            continue
        if "%" in fmt:
            return "percent"
        if "$" in fmt or "USD" in fmt.upper():
            return "currency"
        if "₽" in fmt or "RUB" in fmt.upper():
            return "currency"
    return None
