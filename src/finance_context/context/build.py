from __future__ import annotations

from collections import Counter

from finance_context.excel.a1 import format_addr
from finance_context.layout.models import Layout, LayoutRow
from finance_context.layout.periods import display_cell_text, infer_grain
from finance_context.mapping.graph import row_adjacency
from finance_context.mapping.models import MappedRow, MappingDocument, MapSource, RowRelation
from finance_context.mapping.rules import is_noise_label
from finance_context.models.context import (
    SCHEMA_VERSION,
    ArtifactMeta,
    CandidateHit,
    ContextDocument,
    FinancialBlock,
    InventoryRow,
    MappingEvidence,
    MetricSeries,
    NumericSummary,
    PeriodValue,
    RowHints,
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

_SERIES_KINDS = {"fact", "flag", "helper"}


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
    edges: list[dict] | None = None,
) -> ContextDocument:
    by_addr = {(c["sheet"], int(c["row"]), int(c["col"])): c for c in cells}
    mapped_by_key = {row.row_key: row for row in mapping.rows}
    precedents, dependents = row_adjacency(edges or [])
    warnings: list[str] = []
    formula_count = 0
    missing_cached = 0
    unparsed = 0
    missing_addrs: list[str] = []
    for cell in cells:
        if cell.get("formula_raw"):
            formula_count += 1
            if cell.get("cached_value") is None:
                missing_cached += 1
                missing_addrs.append(f"{cell['sheet']}!{cell['addr']}")
            if cell.get("unparsed"):
                unparsed += 1
                warnings.append(f"Unparsed formula at {cell['sheet']}!{cell['addr']}")

    blocks: list[FinancialBlock] = []
    unmapped: list[MetricSeries] = []
    excluded: list[MetricSeries] = []
    inventory: list[InventoryRow] = []
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
            labeled = [r.label for r in block.rows if r.label]
            for index, layout_row in enumerate(block.rows):
                row_key = f"{sheet.name}|{layout_row.row}|{block.block_id}"
                mapped = mapped_by_key.get(row_key)
                parent = None
                if layout_row.parent_row:
                    parent = parent_by_row.get(layout_row.parent_row)
                neighbors = _neighbors(labeled, index)
                label_path = list(layout_row.section_path)
                if parent and parent not in label_path:
                    label_path = [*label_path, parent]
                fingerprint, exceptions, summary = _row_formula_and_numbers(
                    sheet_name=sheet.name,
                    row_num=layout_row.row,
                    headers=block.axis.headers,
                    by_addr=by_addr,
                )
                loc = (sheet.name, layout_row.row)
                hints = _hints_for(mapped, layout_row)
                series_unit = None
                candidates = _candidates_for(mapped)
                if layout_row.kind in _SERIES_KINDS and not (
                    layout_row.kind == "fact" and is_noise_label(layout_row.label)
                ):
                    series = _series_for_row(
                        sheet_name=sheet.name,
                        block_id=block.block_id,
                        label_col=layout_row.label_col or block.label_col,
                        layout_row=layout_row,
                        layout_row_parent=parent,
                        mapped=mapped,
                        headers=block.axis.headers,
                        by_addr=by_addr,
                        date1904=bool(workbook_meta.get("date1904")),
                        label_path=label_path,
                        neighbors=neighbors,
                        formula_fingerprint=fingerprint,
                        formula_exceptions=exceptions,
                        numeric_summary=summary,
                        precedents_rows=list(precedents.get(loc, [])),
                        dependents_rows=list(dependents.get(loc, [])),
                        candidates=candidates,
                        hints=hints,
                    )
                    series_unit = series.unit
                    if mapped is not None and mapped.disposition == "excluded":
                        excluded.append(series)
                    elif mapped is None or mapped.concept_id is None:
                        unmapped.append(series)
                    else:
                        metrics.append(series)
                inventory.append(
                    InventoryRow(
                        row_key=row_key,
                        sheet=sheet.name,
                        row=layout_row.row,
                        kind=layout_row.kind,
                        label=layout_row.label,
                        parent_label=mapped.parent_label if mapped else parent,
                        label_path=label_path,
                        indent=layout_row.indent,
                        hidden=layout_row.hidden,
                        check_row=layout_row.check_row,
                        neighbors=neighbors,
                        concept_id=mapped.concept_id if mapped else None,
                        disposition=(
                            mapped.disposition
                            if mapped
                            else ("excluded" if is_noise_label(layout_row.label) else None)
                        ),
                        exclusion_reason=(
                            mapped.exclusion_reason
                            if mapped
                            else ("noise" if is_noise_label(layout_row.label) else None)
                        ),
                        unit=series_unit,
                        formula_fingerprint=fingerprint,
                        formula_exceptions=exceptions,
                        numeric_summary=summary,
                        precedents_rows=list(precedents.get(loc, [])),
                        dependents_rows=list(dependents.get(loc, [])),
                        candidates=candidates,
                        hints=hints,
                    )
                )
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

    expected_rows = sum(len(block.rows) for sheet in layout.sheets for block in sheet.blocks)
    if not inventory_completeness(layout, inventory):
        warnings.append(
            f"Content completeness {len(inventory)}/{expected_rows} layout rows"
        )
    if mapping.questions:
        warnings.append(f"{len(mapping.questions)} row(s) need mapping review")
    if missing_cached:
        sample = ", ".join(missing_addrs[:3])
        extra = f" (e.g. {sample})" if sample else ""
        warnings.append(f"{missing_cached} formula cell(s) missing cached values{extra}")

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
        inventory=inventory,
        warnings=warnings[:50],
    )


def _series_for_row(
    *,
    sheet_name: str,
    block_id: str,
    label_col: int,
    layout_row: LayoutRow,
    layout_row_parent: str | None,
    mapped: MappedRow | None,
    headers: list,
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
    label_path: list[str],
    neighbors: list[str],
    formula_fingerprint: str | None,
    formula_exceptions: list[str],
    numeric_summary: NumericSummary | None,
    precedents_rows: list[str],
    dependents_rows: list[str],
    candidates: list[CandidateHit],
    hints: RowHints,
) -> MetricSeries:
    row_num = layout_row.row
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
        fmt = (cell or {}).get("number_format")
        displayed = display_cell_text(
            cached if cached is not None else None,
            fmt,
            date1904=date1904,
        )
        if displayed is not None:
            cached = displayed
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
    unit = _unit_from_values(values)
    if hints.unit is None:
        hints = hints.model_copy(update={"unit": unit})
    return MetricSeries(
        row_key=mapped.row_key if mapped else f"{sheet_name}|{row_num}|{block_id}",
        label=mapped.label if mapped else layout_row.label,
        parent_label=mapped.parent_label if mapped else layout_row_parent,
        concept_id=mapped.concept_id if mapped else None,
        article_role=mapped.article_role if mapped else "database_like",
        unit=unit,
        mapping=evidence,
        values=values,
        source=source,
        disposition=mapped.disposition if mapped else "abstained",
        exclusion_reason=mapped.exclusion_reason if mapped else None,
        kind=layout_row.kind,
        indent=layout_row.indent,
        hidden=layout_row.hidden,
        check_row=layout_row.check_row,
        label_path=label_path,
        neighbors=neighbors,
        formula_fingerprint=formula_fingerprint,
        formula_exceptions=formula_exceptions,
        numeric_summary=numeric_summary,
        precedents_rows=precedents_rows,
        dependents_rows=dependents_rows,
        candidates=candidates,
        hints=hints,
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


def _neighbors(labels: list[str], index: int, span: int = 2) -> list[str]:
    start = max(0, index - span)
    end = min(len(labels), index + span + 1)
    return [label for i, label in enumerate(labels[start:end], start=start) if i != index]


def _candidates_for(mapped: MappedRow | None) -> list[CandidateHit]:
    if mapped is None:
        return []
    hits: list[CandidateHit] = []
    for concept_id, score in mapped.alternatives[:3]:
        hits.append(
            CandidateHit(
                concept_id=concept_id,
                score=score,
                evidence=mapped.evidence if concept_id == mapped.concept_id else None,
            )
        )
    return hits


def _hints_for(mapped: MappedRow | None, layout_row: LayoutRow) -> RowHints:
    concept_id = mapped.concept_id if mapped else None
    statement = concept_id.split(".", 1)[0] if concept_id else None
    label = (layout_row.label or "").casefold()
    tokens = set(label.replace("/", " ").replace("-", " ").split())
    nature = None
    time_semantics = None
    if tokens & {"opening", "closing", "balance", "beg", "ending"}:
        nature = "balance"
    if "opening" in tokens or "b/f" in label or "brought forward" in label:
        time_semantics = "bop"
        nature = "balance"
    elif "closing" in tokens or "c/f" in label or "carried forward" in label:
        time_semantics = "eop"
        nature = "balance"
    elif any(token in tokens for token in ("rate", "ratio", "%")):
        time_semantics = "rate"
    elif layout_row.kind == "fact":
        time_semantics = "flow"
    if statement in {"bs"}:
        nature = nature or "balance"
    elif statement in {"pnl", "cf"}:
        nature = nature or "flow"
    return RowHints(nature=nature, time_semantics=time_semantics, statement=statement)


def _row_formula_and_numbers(
    *,
    sheet_name: str,
    row_num: int,
    headers: list,
    by_addr: dict[tuple[str, int, int], dict],
) -> tuple[str | None, list[str], NumericSummary | None]:
    templates: list[str] = []
    exceptions: list[str] = []
    numbers: list[float] = []
    raw_first: str | None = None
    raw_last: str | None = None
    for header in headers:
        cell = by_addr.get((sheet_name, row_num, header.col))
        if cell is None:
            continue
        template = cell.get("formula_template")
        if template:
            templates.append(str(template))
        cached = cell.get("cached_value")
        if cached in (None, ""):
            continue
        text = str(cached)
        if raw_first is None:
            raw_first = text
        raw_last = text
        try:
            numbers.append(float(text.replace(",", "")))
        except ValueError:
            continue
    fingerprint = Counter(templates).most_common(1)[0][0] if templates else None
    if fingerprint:
        for header in headers:
            cell = by_addr.get((sheet_name, row_num, header.col))
            if cell is None:
                continue
            template = cell.get("formula_template")
            if template and str(template) != fingerprint:
                exceptions.append(f"{sheet_name}!{format_addr(header.col, row_num)}")
    summary = None
    if raw_first is not None or numbers:
        summary = NumericSummary(
            first=raw_first,
            last=raw_last,
            minimum=str(min(numbers)) if numbers else None,
            maximum=str(max(numbers)) if numbers else None,
            constant=len(set(numbers)) <= 1 if numbers else False,
            n=len(numbers),
        )
    return fingerprint, exceptions[:8], summary


def inventory_completeness(layout: Layout, inventory: list[InventoryRow]) -> bool:
    expected = sum(len(block.rows) for sheet in layout.sheets for block in sheet.blocks)
    return len(inventory) == expected
