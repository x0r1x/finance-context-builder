from __future__ import annotations

import re
from collections import Counter

from finance_context.context.measure import parse_measure
from finance_context.context.timeline import annotate_block_periods, build_timeline
from finance_context.excel.a1 import format_addr
from finance_context.layout.models import Layout, LayoutRow
from finance_context.layout.params import is_scenario_selector_label
from finance_context.layout.periods import display_cell_text, infer_grain
from finance_context.mapping.eval import (
    assess_mapping_quality,
    context_report_metrics,
    inventory_coverage_counts,
)
from finance_context.mapping.graph import cell_ref_row, row_adjacency, row_key_ref
from finance_context.mapping.models import (
    MappedRow,
    MappingDocument,
    MapSource,
    RowContext,
    RowRelation,
)
from finance_context.mapping.rowroles import infer_row_roles
from finance_context.mapping.rules import is_noise_label
from finance_context.mapping.semantics import classify_semantics
from finance_context.mapping.statement import statement_for_row
from finance_context.mapping.taxonomy import load_taxonomy
from finance_context.models.context import (
    SCHEMA_VERSION,
    ArtifactMeta,
    CandidateHit,
    CashSemantics,
    ContextDocument,
    FinancialBlock,
    GraphPointer,
    InventoryRow,
    MappingEvidence,
    MappingStats,
    MetricSeries,
    NumericSummary,
    PeriodValue,
    ReportingRole,
    RoleCell,
    RowHints,
    SemanticIdentity,
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
_SERIES_ROLES = {
    "historical",
    "forecast",
    "stub",
    "relative",
    "value",
    "scenario",
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
    graph: GraphPointer | None = None,
    edges: list[dict] | None = None,
) -> ContextDocument:
    by_addr = {(c["sheet"], int(c["row"]), int(c["col"])): c for c in cells}
    mapped_by_key = {row.row_key: row for row in mapping.rows}
    _precedents, dependents = row_adjacency(edges or [])
    concept_by_ref = {
        row_key_ref(row.sheet, row.row): row.concept_id
        for row in mapping.rows
        if row.concept_id
    }
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
            value_headers = [h for h in block.axis.headers if h.role in _SERIES_ROLES]
            if getattr(block, "kind", "timeline") == "params":
                grain = None
                value_headers = [h for h in block.axis.headers if h.role in {"value", "scenario"}]
            periods = [
                {
                    "col": header.col,
                    "text": header.text,
                    "role": header.role,
                    "period_key": header.period_key,
                }
                for header in value_headers
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
                row_headers = value_headers
                if is_scenario_selector_label(layout_row.label):
                    row_headers = [h for h in value_headers if h.role == "value"]
                fingerprint, exceptions, summary = _row_formula_and_numbers(
                    sheet_name=sheet.name,
                    row_num=layout_row.row,
                    headers=row_headers,
                    by_addr=by_addr,
                )
                role_cells = _role_cells(
                    sheet.name, layout_row, by_addr, bool(workbook_meta.get("date1904"))
                )
                unit_text = next(
                    (
                        item.cached_value
                        for item in role_cells
                        if item.role == "unit" and item.cached_value
                    ),
                    None,
                )
                formats = _row_number_formats(
                    sheet_name=sheet.name,
                    row_num=layout_row.row,
                    headers=row_headers,
                    by_addr=by_addr,
                )
                hints = _hints_for(
                    mapped,
                    layout_row,
                    unit_text,
                    sheet=sheet.name,
                    parent=parent,
                    number_formats=formats,
                )
                series_unit = hints.unit
                role_ctx = RowContext(
                    row_key=row_key,
                    sheet=sheet.name,
                    row=layout_row.row,
                    block_id=block.block_id,
                    label=layout_row.label,
                    parent_label=parent,
                    section_path=list(layout_row.section_path),
                    kind=layout_row.kind,
                    article_role=mapped.article_role if mapped else "database_like",
                )
                feeds_cfads = _row_feeds_cfads(
                    sheet.name, layout_row.row, dependents, concept_by_ref
                )
                context_role, secondary = infer_row_roles(
                    role_ctx,
                    mapped.concept_id if mapped else None,
                    feeds_cfads=feeds_cfads,
                )
                if mapped is not None:
                    secondary = list(
                        dict.fromkeys([*mapped.secondary_concepts, *secondary])
                    )
                identity, reporting_roles, cash = classify_semantics(
                    label=layout_row.label,
                    concept_id=mapped.concept_id if mapped else None,
                    score=mapped.score if mapped else None,
                    alternatives=list(mapped.alternatives) if mapped else [],
                    context_role=context_role,
                    secondary_concepts=secondary,
                )
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
                        headers=row_headers,
                        by_addr=by_addr,
                        date1904=bool(workbook_meta.get("date1904")),
                        label_path=label_path,
                        neighbors=neighbors,
                        formula_fingerprint=fingerprint,
                        formula_exceptions=exceptions,
                        numeric_summary=summary,
                        candidates=candidates,
                        hints=hints,
                        role_cells=role_cells,
                        context_role=context_role,
                        secondary_concepts=secondary,
                        semantic_identity=identity,
                        reporting_roles=reporting_roles,
                        cash_semantics=cash,
                    )
                    series_unit = series_unit or series.unit
                    if mapped is not None and mapped.disposition == "excluded":
                        excluded.append(series)
                    elif layout_row.kind == "flag":
                        excluded.append(
                            series.model_copy(
                                update={
                                    "disposition": "excluded",
                                    "exclusion_reason": series.exclusion_reason or "flag",
                                }
                            )
                        )
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
                        disposition=_inventory_disposition(mapped, layout_row),
                        exclusion_reason=(
                            mapped.exclusion_reason
                            if mapped
                            else (
                                "noise"
                                if is_noise_label(layout_row.label)
                                else ("flag" if layout_row.kind == "flag" else None)
                            )
                        ),
                        unit=series_unit,
                        formula_fingerprint=fingerprint,
                        formula_exceptions=exceptions,
                        numeric_summary=summary,
                        candidates=candidates,
                        hints=hints,
                        cells=role_cells,
                        context_role=context_role,
                        secondary_concepts=secondary,
                        semantic_identity=identity,
                        reporting_roles=reporting_roles,
                        cash_semantics=cash,
                    )
                )
            blocks.append(
                FinancialBlock(
                    block_id=block.block_id,
                    sheet=sheet.name,
                    label_col=block.label_col,
                    grain=grain,
                    kind=getattr(block, "kind", "timeline"),
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
    timeline, timeline_warnings = build_timeline(
        layout, cells, date1904=bool(workbook_meta.get("date1904"))
    )
    warnings.extend(timeline_warnings)
    if timeline is not None:
        for block in blocks:
            block.periods = annotate_block_periods(block.periods, timeline)
    if mapping.questions:
        warnings.append(f"{len(mapping.questions)} row(s) need mapping review")
    if missing_cached:
        sample = ", ".join(missing_addrs[:3])
        extra = f" (e.g. {sample})" if sample else ""
        warnings.append(f"{missing_cached} formula cell(s) missing cached values{extra}")
    if graph is not None and graph.dangling:
        warnings.append(
            f"Graph: {graph.dangling} unresolved formula targets (see graph-dangling.json)"
        )

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
        iterate=bool(workbook_meta.get("iterate")),
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
    counts = inventory_coverage_counts(inventory)
    stats = context_report_metrics(
        layout_rows=expected_rows,
        inventory_rows=len(inventory),
        mapped=int(counts["mapped"]),
        abstained=int(counts["abstained"]),
        excluded=int(counts["excluded"]),
        abstract=int(counts["abstract"]),
        unmapped_series=len(unmapped),
    )
    stats["mapping_quality"] = assess_mapping_quality(inventory, blocks)
    return ContextDocument(
        schema_version=SCHEMA_VERSION,
        meta=meta,
        workbook=workbook,
        timeline=timeline,
        blocks=blocks,
        unmapped=unmapped,
        excluded=excluded,
        inventory=inventory,
        mapping_stats=MappingStats.model_validate(stats),
        graph=graph or GraphPointer(),
        warnings=warnings[:50],
    )


def _inventory_disposition(mapped: MappedRow | None, layout_row: LayoutRow) -> str | None:
    if mapped is not None:
        return mapped.disposition
    if is_noise_label(layout_row.label):
        return "excluded"
    if layout_row.kind == "abstract":
        return "header"
    if layout_row.kind == "flag":
        return "excluded"
    return None


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
    candidates: list[CandidateHit],
    hints: RowHints,
    role_cells: list[RoleCell],
    context_role: str | None = None,
    secondary_concepts: list[str] | None = None,
    semantic_identity: SemanticIdentity | None = None,
    reporting_roles: list[ReportingRole] | None = None,
    cash_semantics: CashSemantics | None = None,
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
                has_formula=bool(formula),
                number_format=(cell or {}).get("number_format"),
                source=SourceRef(sheet=sheet_name, addr=addr, row=row_num, col=header.col),
                missing_cached_value=bool(formula) and cached in (None, ""),
                formula=str(formula) if formula else None,
            )
        )
    unit_from_cell = next(
        (item.cached_value for item in role_cells if item.role == "unit"), None
    )
    formats = [item.number_format for item in values if item.number_format]
    concept_id = mapped.concept_id if mapped else None
    measure = parse_measure(
        unit_from_cell,
        layout_row.label,
        formats,
        concept_id=concept_id,
        statement=hints.statement,
        nature=hints.nature,
        time_semantics=hints.time_semantics,
        direction=_concept_direction(concept_id),
    )
    unit = measure.unit or hints.unit
    hints = hints.model_copy(
        update={
            "unit": unit,
            "currency": hints.currency or measure.currency,
            "scale": hints.scale or measure.scale,
            "sign": hints.sign or measure.sign,
        }
    )
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
        candidates=candidates,
        hints=hints,
        cells=role_cells,
        context_role=context_role,
        secondary_concepts=list(secondary_concepts or []),
        semantic_identity=semantic_identity,
        reporting_roles=list(reporting_roles or []),
        cash_semantics=cash_semantics,
    )


def _relations_for_block(relations: list[RowRelation], block_id: str) -> list[dict]:
    out: list[dict] = []
    for rel in relations:
        keys = [rel.source_row_key, rel.target_row_key, *rel.member_row_keys]
        if any(key and key.endswith(f"|{block_id}") for key in keys):
            out.append(rel.model_dump(mode="json"))
    return out


def _row_number_formats(
    *,
    sheet_name: str,
    row_num: int,
    headers: list,
    by_addr: dict[tuple[str, int, int], dict],
) -> list[str]:
    out: list[str] = []
    for header in headers:
        cell = by_addr.get((sheet_name, row_num, header.col))
        fmt = None if cell is None else cell.get("number_format")
        if fmt:
            out.append(str(fmt))
    return out


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


def _hints_for(
    mapped: MappedRow | None,
    layout_row: LayoutRow,
    unit_text: str | None = None,
    *,
    sheet: str = "",
    parent: str | None = None,
    number_formats: list[str] | None = None,
) -> RowHints:
    concept_id = mapped.concept_id if mapped else None
    blob, tokens = _hint_blob_and_tokens(layout_row.label or "")
    nature = None
    time_semantics = None
    statement = statement_for_row(
        concept_id=concept_id,
        sheet=sheet,
        section_path=list(layout_row.section_path),
        parent=parent,
        label=layout_row.label,
    )
    if tokens & {"opening", "closing", "balance", "beg", "ending"}:
        nature = "balance"
    if "opening" in tokens or "b/f" in blob or "brought forward" in blob:
        time_semantics = "bop"
        nature = "balance"
    elif "closing" in tokens or "c/f" in blob or "carried forward" in blob:
        time_semantics = "eop"
        nature = "balance"
    if statement == "bs" or (concept_id and concept_id.startswith("bs.")):
        nature = nature or "balance"
    elif statement in {"pnl", "cf"}:
        nature = nature or "flow"
    if time_semantics is None and (
        any(token in tokens for token in ("rate", "ratio", "%"))
    ):
        time_semantics = "rate"
    elif layout_row.kind == "fact" and time_semantics is None:
        time_semantics = "flow"
    if time_semantics == "flow" and nature == "balance":
        time_semantics = "stock"
    measure = parse_measure(
        unit_text,
        layout_row.label,
        number_formats,
        concept_id=concept_id,
        statement=statement,
        nature=nature,
        time_semantics=time_semantics,
        direction=_concept_direction(concept_id),
    )
    if measure.unit == "rate" and time_semantics in {None, "flow"}:
        time_semantics = "rate"
    return RowHints(
        nature=nature,
        time_semantics=time_semantics,
        statement=statement,
        unit=measure.unit,
        currency=measure.currency,
        scale=measure.scale,
        sign=measure.sign,
        segment=_segment_hint(blob, tokens),
        escalation=_escalation_hint(blob, tokens),
    )


def _concept_direction(concept_id: str | None) -> str | None:
    if not concept_id:
        return None
    for concept in load_taxonomy():
        if concept.id == concept_id:
            return concept.facets.direction
    return None


def _hint_blob_and_tokens(label: str) -> tuple[str, set[str]]:
    spaced = re.sub(r"[()\[\]{}/,&\-]+", " ", label or "")
    blob = re.sub(r"\s+", " ", spaced).strip().casefold()
    return blob, set(blob.split())


def _segment_hint(blob: str, tokens: set[str]) -> str | None:
    vehicle = bool(tokens & {"traffic", "toll", "vehicle", "vehicule", "car", "passenger"})
    if "pc" in tokens or "passenger" in tokens:
        return "pc"
    if "hv" in tokens:
        return "hv"
    if "heavy maintenance" in blob:
        return None
    if "heavy" in tokens and vehicle:
        return "hv"
    return None


def _escalation_hint(blob: str, tokens: set[str]) -> str | None:
    if "inflation" not in tokens and "escalation" not in tokens:
        return None
    if tokens & {"cost", "costs"}:
        return "cost"
    return "revenue"


def _row_feeds_cfads(
    sheet: str,
    row: int,
    dependents: dict[tuple[str, int], list[str]],
    concept_by_ref: dict[str, str | None],
) -> bool:
    for ref in dependents.get((sheet, row), []):
        if concept_by_ref.get(ref) == "cf.cfads":
            return True
        parsed = cell_ref_row(ref)
        if parsed and concept_by_ref.get(row_key_ref(*parsed)) == "cf.cfads":
            return True
    return False


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


def _role_cells(
    sheet: str,
    layout_row: LayoutRow,
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
) -> list[RoleCell]:
    out: list[RoleCell] = []
    for item in layout_row.cells:
        cell = by_addr.get((sheet, layout_row.row, item.col))
        cached = None if cell is None else cell.get("cached_value")
        fmt = None if cell is None else cell.get("number_format")
        displayed = display_cell_text(
            cached if cached is not None else None, fmt, date1904=date1904
        )
        out.append(
            RoleCell(
                addr=format_addr(item.col, layout_row.row),
                col=item.col,
                role=item.role,
                cached_value=displayed if displayed is not None else cached,
            )
        )
    return out
