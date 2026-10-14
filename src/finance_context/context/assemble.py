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
from finance_context.layout.exclusion import exclusion_reason
from finance_context.layout.models import LayoutRow
from finance_context.layout.periods import display_cell_text
from finance_context.layout.relations import RowRelation
from finance_context.models.context import (
    BlockRow,
    NumericSummary,
    RoleCell,
    RowHints,
    RowSeries,
)
from finance_context.vocab import ValueStatus

_SERIES_KINDS = {"fact", "flag", "helper"}


class _ExclusionView:
    def __init__(self, kind: str, article_role: str, label: str) -> None:
        self.kind = kind
        self.article_role = article_role
        self.label = label


def _published_disposition(layout_row: LayoutRow, role: str) -> tuple[str | None, str | None]:
    reason = exclusion_reason(_ExclusionView(layout_row.kind, role, layout_row.label))
    if reason is None:
        return None, None
    return "excluded", reason


def _row_for_layout(
    *,
    sheet_name: str,
    block_id: str,
    layout_row: LayoutRow,
    layout_row_parent: str | None,
    article_role: str | None,
    headers: list,
    value_headers: list,
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
    label_path: list[str],
    neighbors: list[str],
    formula: str | None,
    formula_exceptions: list[str],
    numeric_summary: NumericSummary | None,
    hints: RowHints,
    role_cells: list[RoleCell],
    series_unit: str | None,
    period_phases: dict[str, str | None] | None = None,
    series: list[RowSeries] | None = None,
    static: bool = False,
) -> BlockRow:
    row_num = layout_row.row
    keep_cols = {header.col for header in value_headers}
    gate = phase_gate(layout_row.label)
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
        nature=hints.nature,
        time_semantics=hints.time_semantics,
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
    disposition, reason = _published_disposition(layout_row, article_role or "")
    return BlockRow(
        row_key=f"{sheet_name}|{row_num}|{block_id}",
        sheet=sheet_name,
        row=row_num,
        label=layout_row.label,
        parent_label=layout_row_parent,
        article_role=article_role,
        unit=unit,
        period_position=position,
        aggregation=aggregation,
        scale_factor=factor,
        values=values,
        value_statuses=statuses,
        normalized_values=normalized,
        series=list(series or []),
        disposition=disposition,
        exclusion_reason=reason,
        kind=layout_row.kind,
        indent=layout_row.indent,
        hidden=layout_row.hidden,
        check_row=layout_row.check_row,
        label_path=label_path,
        neighbors=neighbors,
        formula=formula,
        formula_exceptions=formula_exceptions,
        numeric_summary=numeric_summary,
        hints=hints,
        cells=role_cells,
    )


def _relations_for_block(relations: list[RowRelation], block_id: str) -> list[dict]:
    out: list[dict] = []
    for rel in relations:
        keys = [rel.source_row_key, rel.target_row_key, *rel.member_row_keys]
        if any(key and key.endswith(f"|{block_id}") for key in keys):
            out.append(rel.model_dump(mode="json"))
    return out


def _hints_for(
    layout_row: LayoutRow,
    unit_text: str | None = None,
    *,
    number_formats: list[str] | None = None,
    static: bool = False,
) -> RowHints:
    blob, tokens = _hint_blob_and_tokens(layout_row.label or "")
    nature = None
    time_semantics = None
    if tokens & {"opening", "closing", "balance", "beg", "ending"}:
        nature = "balance"
    if "opening" in tokens or "b/f" in blob or "brought forward" in blob:
        time_semantics = "bop"
        nature = "balance"
    elif "closing" in tokens or "c/f" in blob or "carried forward" in blob:
        time_semantics = "eop"
        nature = "balance"
    if time_semantics is None and any(token in tokens for token in ("rate", "ratio", "%")):
        time_semantics = "rate"
    elif layout_row.kind == "fact" and time_semantics is None:
        time_semantics = "flow"
    if _is_index_unit(unit_text):
        time_semantics = "stock"
        nature = nature or "balance"
    if time_semantics == "flow" and nature == "balance":
        time_semantics = "stock"
    elif static and time_semantics in {None, "flow", "stock"}:
        time_semantics = "instant"
    measure = parse_measure(
        unit_text,
        layout_row.label,
        number_formats,
        nature=nature,
        time_semantics=time_semantics,
    )
    if measure.unit == "money" and _percent_formatted(number_formats or []):
        measure = Measure(unit="rate", sign=measure.sign)
    if measure.unit == "rate" and time_semantics in {None, "flow", "instant"}:
        time_semantics = "rate"
    return RowHints(
        nature=nature,
        time_semantics=time_semantics,
        unit=measure.unit,
        unit_per=measure.per,
        currency=measure.currency,
        scale=measure.scale,
        sign=measure.sign,
    )


def _hint_blob_and_tokens(label: str) -> tuple[str, set[str]]:
    spaced = re.sub(r"[()\[\]{}/,&\-]+", " ", label or "")
    blob = re.sub(r"\s+", " ", spaced).strip().casefold()
    return blob, set(blob.split())


def _percent_formatted(formats: list[str]) -> bool:
    percents = sum(1 for fmt in formats if "%" in fmt)
    return percents > 0 and percents * 2 >= len(formats)


def _is_index_unit(unit_text: str | None) -> bool:
    return (unit_text or "").strip().casefold() == "index"
