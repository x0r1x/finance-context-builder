from __future__ import annotations

import re
from collections import Counter

from finance_context.context.measure import Measure, parse_measure
from finance_context.context.series import (
    format_number,
    normalize_series,
    phase_gate,
    scale_factor_for,
    temporal_profile,
    value_status,
)
from finance_context.context.timeline import build_axes
from finance_context.excel.a1 import format_addr
from finance_context.layout.models import AxisHeader, Layout, LayoutRow
from finance_context.layout.params import is_scenario_selector_label
from finance_context.layout.periods import display_cell_text
from finance_context.layout.resolve import axes_for
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
    BlockRow,
    CandidateHit,
    CashSemantics,
    ContextAxis,
    ContextDocument,
    ContextPeriod,
    FinancialBlock,
    GraphPointer,
    MappingEvidence,
    MappingStats,
    NumericSummary,
    ReportingRole,
    RoleCell,
    RowHints,
    RowSeries,
    SemanticIdentity,
    WorkbookRaw,
)
from finance_context.vocab import ValueStatus

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
    axes, timeline_warnings = build_axes(
        layout, cells, date1904=bool(workbook_meta.get("date1904"))
    )
    axes_by_id = {axis.id: axis for axis in axes}
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

    roll_time = _roll_forward_time(mapping.relations)
    out_of_phase: set[str] = set()
    blocks: list[FinancialBlock] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            kind = getattr(block, "kind", "timeline")
            views = _block_views(sheet, block, axes_by_id)
            hint_headers = [header for _axis_id, headers, _phases in views for header in headers]
            axis_ids = [axis_id for axis_id, _headers, _phases in views] if kind != "params" else []
            periods = (
                []
                if axis_ids
                else [
                    {
                        "col": header.col,
                        "text": header.text,
                        "role": header.role,
                        "period_key": header.period_key,
                    }
                    for _axis_id, headers, _phases in views
                    for header in headers
                ]
            )
            rows: list[BlockRow] = []
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
                    headers=hint_headers,
                    by_addr=by_addr,
                )
                static = _is_static_row(kind, sheet.name, layout_row, hint_headers, by_addr)
                hints = _hints_for(
                    mapped,
                    layout_row,
                    unit_text,
                    sheet=sheet.name,
                    parent=parent,
                    number_formats=formats,
                    static=static,
                )
                rolled = roll_time.get(row_key)
                if rolled is not None and not static:
                    update = {"time_semantics": rolled}
                    if rolled == "flow":
                        update["nature"] = "flow"
                    hints = hints.model_copy(update=update)
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
                series: list[RowSeries] = []
                for axis_id, headers, phases in views:
                    row_headers = headers
                    if is_scenario_selector_label(layout_row.label):
                        row_headers = [header for header in headers if header.role == "value"]
                    fingerprint, exceptions, summary = _row_formula_and_numbers(
                        sheet_name=sheet.name,
                        row_num=layout_row.row,
                        headers=row_headers,
                        by_addr=by_addr,
                        stub_cols=[item.col for item in layout_row.cells],
                    )
                    values, statuses, normalized = _series_numbers(
                        sheet_name=sheet.name,
                        row_num=layout_row.row,
                        layout_row=layout_row,
                        mapped=mapped,
                        headers=headers,
                        value_headers=row_headers,
                        by_addr=by_addr,
                        date1904=bool(workbook_meta.get("date1904")),
                        period_phases=phases,
                        factor=scale_factor_for(hints.scale),
                    )
                    series.append(
                        RowSeries(
                            axis_id=axis_id,
                            formula=fingerprint,
                            formula_exceptions=exceptions,
                            numeric_summary=summary,
                            values=values,
                            value_statuses=statuses,
                            normalized_values=normalized,
                        )
                    )
                first = series[0] if series else None
                line = _row_for_layout(
                    sheet_name=sheet.name,
                    block_id=block.block_id,
                    layout_row=layout_row,
                    layout_row_parent=parent,
                    mapped=mapped,
                    headers=[],
                    value_headers=[],
                    by_addr=by_addr,
                    date1904=bool(workbook_meta.get("date1904")),
                    label_path=label_path,
                    neighbors=neighbors,
                    formula=first.formula if first else None,
                    formula_exceptions=list(first.formula_exceptions) if first else [],
                    numeric_summary=first.numeric_summary if first else None,
                    candidates=candidates,
                    hints=hints,
                    role_cells=role_cells,
                    series_unit=series_unit,
                    context_role=context_role,
                    secondary_concepts=secondary,
                    semantic_identity=identity,
                    reporting_roles=reporting_roles,
                    cash_semantics=cash,
                    series=series,
                    static=static,
                )
                for (_axis_id, headers, _phases), item in zip(views, series, strict=False):
                    for header, state in zip(headers, item.value_statuses, strict=False):
                        if state == "not_applicable":
                            out_of_phase.add(
                                f"{sheet.name}!{format_addr(header.col, layout_row.row)}"
                            )
                rows.append(line)
            blocks.append(
                FinancialBlock(
                    block_id=block.block_id,
                    sheet=sheet.name,
                    label_col=block.label_col,
                    grain=None,
                    kind=kind,
                    axis_ids=axis_ids,
                    periods=periods,
                    rows=rows,
                    relations=_relations_for_block(mapping.relations, block.block_id),
                )
            )

    document_rows = [row for block in blocks for row in block.rows]
    expected_rows = sum(len(block.rows) for sheet in layout.sheets for block in sheet.blocks)
    if len(document_rows) != expected_rows:
        warnings.append(
            f"Content completeness {len(document_rows)}/{expected_rows} layout rows"
        )
    warnings.extend(timeline_warnings)
    if mapping.questions:
        warnings.append(f"{len(mapping.questions)} row(s) need mapping review")
    in_phase_missing = [addr for addr in missing_addrs if addr not in out_of_phase]
    if in_phase_missing:
        sample = ", ".join(in_phase_missing[:3])
        extra = f" (e.g. {sample})" if sample else ""
        warnings.append(f"{len(in_phase_missing)} formula cell(s) missing cached values{extra}")
    if graph is not None and graph.dangling:
        warnings.append(
            f"Graph: {graph.dangling} unresolved formula targets (see graph.json)"
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
    counts = inventory_coverage_counts(document_rows)
    unmapped_series = sum(
        1
        for row in document_rows
        if row.kind in _SERIES_KINDS and row.disposition == "abstained"
    )
    stats = context_report_metrics(
        layout_rows=expected_rows,
        inventory_rows=len(document_rows),
        mapped=int(counts["mapped"]),
        abstained=int(counts["abstained"]),
        excluded=int(counts["excluded"]),
        abstract=int(counts["abstract"]),
        unmapped_series=unmapped_series,
    )
    stats["mapping_quality"] = assess_mapping_quality(document_rows, blocks)
    return ContextDocument(
        schema_version=SCHEMA_VERSION,
        meta=meta,
        workbook=workbook,
        axes=axes,
        blocks=blocks,
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


def _block_views(
    sheet,
    block,
    axes_by_id: dict[str, ContextAxis],
) -> list[tuple[str, list[AxisHeader], dict[str, str | None]]]:
    kind = getattr(block, "kind", "timeline")
    if kind == "params":
        headers = [
            header
            for header in (block.axis.headers if block.axis is not None else [])
            if header.role in {"value", "scenario"}
        ]
        return [(block.block_id, headers, {})]
    views: list[tuple[str, list[AxisHeader], dict[str, str | None]]] = []
    for axis in axes_for(sheet, block):
        annotated = axes_by_id.get(axis.id)
        headers: list[AxisHeader] = []
        phases: dict[str, str | None] = {}
        source: list[ContextPeriod] | list = (
            annotated.periods if annotated is not None else axis.periods
        )
        for period in source:
            role = period.role
            if role not in _SERIES_ROLES:
                continue
            headers.append(
                AxisHeader(
                    col=period.col,
                    text=period.text,
                    role=role,
                    period_key=period.period_key,
                )
            )
            phase = getattr(period, "phase", None)
            phases[period.period_key] = phase
        views.append((axis.id, headers, phases))
    return views


def _series_numbers(
    *,
    sheet_name: str,
    row_num: int,
    layout_row: LayoutRow,
    mapped: MappedRow | None,
    headers: list,
    value_headers: list,
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
    period_phases: dict[str, str | None],
    factor: int | None,
) -> tuple[list[str | None], list[ValueStatus], list[str | None]]:
    keep_cols = {header.col for header in value_headers}
    concept_id = mapped.concept_id if mapped else None
    gate = phase_gate(mapped.label if mapped else layout_row.label, concept_id)
    values: list[str | None] = []
    statuses: list[ValueStatus] = []
    for header in headers:
        if header.col not in keep_cols:
            values.append(None)
            statuses.append("not_applicable")
            continue
        cell = by_addr.get((sheet_name, row_num, header.col))
        cached = None if cell is None else cell.get("cached_value")
        fmt = None if cell is None else cell.get("number_format")
        displayed = display_cell_text(
            cached if cached is not None else None,
            fmt,
            date1904=date1904,
        )
        if displayed is not None:
            cached = displayed
        text = None if cached in (None, "") else str(cached)
        values.append(text)
        phase = period_phases.get(str(header.period_key))
        outside_phase = bool(gate and phase and gate != phase and text is None)
        structural = layout_row.kind not in _SERIES_KINDS and text is None
        statuses.append(value_status(text, applicable=not outside_phase and not structural))
    return values, statuses, normalize_series(values, factor)


def _row_for_layout(
    *,
    sheet_name: str,
    block_id: str,
    layout_row: LayoutRow,
    layout_row_parent: str | None,
    mapped: MappedRow | None,
    headers: list,
    value_headers: list,
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
    label_path: list[str],
    neighbors: list[str],
    formula: str | None,
    formula_exceptions: list[str],
    numeric_summary: NumericSummary | None,
    candidates: list[CandidateHit],
    hints: RowHints,
    role_cells: list[RoleCell],
    series_unit: str | None,
    period_phases: dict[str, str | None] | None = None,
    context_role: str | None = None,
    secondary_concepts: list[str] | None = None,
    semantic_identity: SemanticIdentity | None = None,
    reporting_roles: list[ReportingRole] | None = None,
    cash_semantics: CashSemantics | None = None,
    series: list[RowSeries] | None = None,
    static: bool = False,
) -> BlockRow:
    row_num = layout_row.row
    keep_cols = {header.col for header in value_headers}
    concept_id = mapped.concept_id if mapped else None
    gate = phase_gate(mapped.label if mapped else layout_row.label, concept_id)
    phases = period_phases or {}
    values: list[str | None] = []
    statuses: list[ValueStatus] = []
    formats: list[str] = []
    for header in headers:
        if header.col not in keep_cols:
            values.append(None)
            statuses.append("not_applicable")
            continue
        cell = by_addr.get((sheet_name, row_num, header.col))
        cached = None if cell is None else cell.get("cached_value")
        fmt = None if cell is None else cell.get("number_format")
        if fmt:
            formats.append(str(fmt))
        displayed = display_cell_text(
            cached if cached is not None else None,
            fmt,
            date1904=date1904,
        )
        if displayed is not None:
            cached = displayed
        text = None if cached in (None, "") else str(cached)
        values.append(text)
        phase = phases.get(str(header.period_key))
        outside_phase = bool(gate and phase and gate != phase and text is None)
        structural = layout_row.kind not in _SERIES_KINDS and text is None
        statuses.append(
            value_status(text, applicable=not outside_phase and not structural)
        )
    unit_from_cell = next(
        (item.cached_value for item in role_cells if item.role == "unit"), None
    )
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
    if hints.unit == "rate":
        measure = Measure(unit="rate", sign=measure.sign)
    unit = measure.unit or hints.unit or series_unit
    hints = hints.model_copy(
        update={
            "unit": unit,
            "currency": hints.currency or measure.currency,
            "scale": hints.scale or measure.scale,
            "sign": hints.sign or measure.sign,
            "unit_per": hints.unit_per or measure.per,
        }
    )
    factor = scale_factor_for(hints.scale)
    position, aggregation = temporal_profile(hints.time_semantics)
    if static:
        position, aggregation = "instant", "none"
    if series:
        series = [
            item.model_copy(
                update={
                    "normalized_values": normalize_series(list(item.values), factor)
                }
            )
            for item in series
        ]
        values = list(series[0].values)
        statuses = list(series[0].value_statuses)
    normalized = normalize_series(values, factor)
    disposition, exclusion_reason = _row_disposition(mapped, layout_row)
    method = _METHOD.get(mapped.source, "unmapped") if mapped else "unmapped"
    evidence = None
    if mapped is not None or layout_row.kind in _SERIES_KINDS:
        evidence = MappingEvidence(
            method=method,  # type: ignore[arg-type]
            score=mapped.score if mapped else None,
            confidence=mapped.confidence if mapped else "low",
            alternatives=list(mapped.alternatives) if mapped else [],
            source=mapped.source if mapped else None,
            evidence=mapped.evidence if mapped else None,
            disposition=disposition or "abstained",
            exclusion_reason=exclusion_reason,
        )
    return BlockRow(
        row_key=mapped.row_key if mapped else f"{sheet_name}|{row_num}|{block_id}",
        sheet=sheet_name,
        row=row_num,
        label=mapped.label if mapped else layout_row.label,
        parent_label=mapped.parent_label if mapped else layout_row_parent,
        concept_id=concept_id,
        article_role=mapped.article_role if mapped else None,
        unit=unit,
        mapping=evidence,
        period_position=position,
        aggregation=aggregation,
        scale_factor=factor,
        values=values,
        value_statuses=statuses,
        normalized_values=normalized,
        series=list(series or []),
        disposition=disposition,
        exclusion_reason=exclusion_reason,
        kind=layout_row.kind,
        indent=layout_row.indent,
        hidden=layout_row.hidden,
        check_row=layout_row.check_row,
        label_path=label_path,
        neighbors=neighbors,
        formula=formula,
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


def _row_disposition(
    mapped: MappedRow | None, layout_row: LayoutRow
) -> tuple[str | None, str | None]:
    noise = layout_row.kind == "fact" and is_noise_label(layout_row.label)
    if noise:
        return "excluded", "noise"
    if layout_row.kind in _SERIES_KINDS:
        if mapped is not None and mapped.disposition == "excluded":
            return "excluded", mapped.exclusion_reason
        if layout_row.kind == "flag":
            reason = mapped.exclusion_reason if mapped and mapped.exclusion_reason else "flag"
            return "excluded", reason
        if mapped is None or mapped.concept_id is None:
            reason = mapped.exclusion_reason if mapped else None
            disposition = mapped.disposition if mapped and mapped.disposition else "abstained"
            if disposition == "excluded":
                return "excluded", reason
            return "abstained", reason
        return mapped.disposition, mapped.exclusion_reason
    return _inventory_disposition(mapped, layout_row), (
        mapped.exclusion_reason
        if mapped
        else ("flag" if layout_row.kind == "flag" else None)
    )


def _roll_forward_time(relations: list[RowRelation]) -> dict[str, str]:
    """b/f is the opening, c/f the closing; the lines between them and their aliases move."""
    found: dict[str, str] = {}
    for rel in relations:
        if rel.kind != "roll_forward" or not rel.source_row_key or not rel.target_row_key:
            continue
        opening = _split_row_key(rel.source_row_key)
        closing = _split_row_key(rel.target_row_key)
        if opening is None or closing is None:
            continue
        if opening[0] != closing[0] or opening[2] != closing[2]:
            continue
        sheet, first, block_id = opening
        last = closing[1]
        found[rel.source_row_key] = "bop"
        found[rel.target_row_key] = "eop"
        for row in range(first + 1, last):
            found.setdefault(f"{sheet}|{row}|{block_id}", "flow")
    for rel in relations:
        if rel.kind != "alias" or not rel.source_row_key or not rel.target_row_key:
            continue
        if found.get(rel.source_row_key) == "flow":
            found.setdefault(rel.target_row_key, "flow")
    return found


def _split_row_key(row_key: str) -> tuple[str, int, str] | None:
    parts = row_key.split("|")
    if len(parts) != 3 or not parts[1].isdigit():
        return None
    return parts[0], int(parts[1]), parts[2]


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
    static: bool = False,
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
    if time_semantics == "flow" and _concept_period_type(concept_id) == "instant":
        time_semantics = "instant" if static else "stock"
    elif static and time_semantics in {None, "flow", "stock"}:
        time_semantics = "instant"
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
    if measure.unit == "money" and _percent_formatted(number_formats or []):
        measure = Measure(unit="rate", sign=measure.sign)
    if measure.unit == "rate" and time_semantics in {None, "flow", "instant"}:
        time_semantics = "rate"
    return RowHints(
        nature=nature,
        time_semantics=time_semantics,
        statement=statement,
        unit=measure.unit,
        unit_per=measure.per,
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
    stub_cols: list[int] | None = None,
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
    if fingerprint is None:
        for col in stub_cols or []:
            cell = by_addr.get((sheet_name, row_num, col))
            template = None if cell is None else cell.get("formula_template")
            if template:
                fingerprint = str(template)
                break
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
            minimum=format_number(min(numbers)) if numbers else None,
            maximum=format_number(max(numbers)) if numbers else None,
            constant=len(set(numbers)) <= 1 if numbers else False,
            n=len(numbers),
        )
    return fingerprint, exceptions[:8], summary


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
                header=item.header,
            )
        )
    return out


def _is_static_row(
    kind: str,
    sheet: str,
    layout_row: LayoutRow,
    headers: list,
    by_addr: dict[tuple[str, int, int], dict],
) -> bool:
    """A params line or a scalar left of the ruler is a point value, not a period series."""
    if layout_row.kind not in {"fact", "helper", "flag"}:
        return False
    if kind == "params":
        return True
    if not any(item.role == "value" for item in layout_row.cells):
        return False
    return not any(
        (by_addr.get((sheet, layout_row.row, header.col)) or {}).get("cached_value")
        not in (None, "")
        for header in headers
    )


def _percent_formatted(formats: list[str]) -> bool:
    percents = sum(1 for fmt in formats if "%" in fmt)
    return percents > 0 and percents * 2 >= len(formats)


def _concept_period_type(concept_id: str | None) -> str | None:
    if not concept_id:
        return None
    for concept in load_taxonomy():
        if concept.id == concept_id:
            return concept.facets.period_type
    return None
