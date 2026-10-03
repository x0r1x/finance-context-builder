from __future__ import annotations

import re

from finance_context.context.measure import Measure, parse_measure
from finance_context.context.series import (
    normalize_series,
    phase_gate,
    scale_factor_for,
    temporal_profile,
    value_status,
)
from finance_context.layout.models import LayoutRow
from finance_context.layout.periods import display_cell_text
from finance_context.mapping.models import MappedRow, MapSource, RowRelation
from finance_context.mapping.rules import is_noise_label
from finance_context.mapping.statement import statement_for_row
from finance_context.mapping.taxonomy import load_taxonomy
from finance_context.models.context import (
    BlockRow,
    CandidateHit,
    CashSemantics,
    MappingEvidence,
    NumericSummary,
    ReportingRole,
    RoleCell,
    RowHints,
    RowSeries,
    SemanticIdentity,
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
    concepts: dict | None = None,
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
        direction=_concept_direction(concept_id, concepts),
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


def _relations_for_block(relations: list[RowRelation], block_id: str) -> list[dict]:
    out: list[dict] = []
    for rel in relations:
        keys = [rel.source_row_key, rel.target_row_key, *rel.member_row_keys]
        if any(key and key.endswith(f"|{block_id}") for key in keys):
            out.append(rel.model_dump(mode="json"))
    return out


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
    concepts: dict | None = None,
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
    if concept_id == "ops.cpi" or _is_index_unit(unit_text):
        time_semantics = "stock"
        nature = nature or "balance"
    if time_semantics == "flow" and nature == "balance":
        time_semantics = "stock"
    if time_semantics == "flow" and _concept_period_type(concept_id, concepts) == "instant":
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
        direction=_concept_direction(concept_id, concepts),
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


def _concept_direction(concept_id: str | None, concepts: dict | None = None) -> str | None:
    concept = _concept_from(concept_id, concepts)
    return None if concept is None else concept.facets.direction


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


def _percent_formatted(formats: list[str]) -> bool:
    percents = sum(1 for fmt in formats if "%" in fmt)
    return percents > 0 and percents * 2 >= len(formats)


def _is_index_unit(unit_text: str | None) -> bool:
    return (unit_text or "").strip().casefold() == "index"


def _concept_period_type(concept_id: str | None, concepts: dict | None = None) -> str | None:
    concept = _concept_from(concept_id, concepts)
    return None if concept is None else concept.facets.period_type


def _concept_from(concept_id: str | None, concepts: dict | None):
    if not concept_id:
        return None
    if concepts is None:
        for concept in load_taxonomy():
            if concept.id == concept_id:
                return concept
        return None
    return concepts.get(concept_id)
