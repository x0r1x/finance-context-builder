"""Observation slice. Joins a row, one period, and the matching formula link."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from finance_context.excel.a1 import format_addr
from finance_context.graph.models import FormulaLink
from finance_context.labels import own_label_contains
from finance_context.models.context import BlockRow, ContextDocument, ContextPeriod, FinancialBlock
from finance_context.models.observation import (
    Observation,
    ObservationDimensions,
    ObservationDocument,
    ObservationFormula,
    ObservationPrecedent,
    ObservationSource,
    ObservationTimeline,
    ObservationUnit,
)
from finance_context.vocab import ValueStatus

PrecedentLookup = Callable[[str, int], Sequence[ObservationPrecedent]]
_VALUE_ROLES = frozenset({"value", "scenario"})


def build_observations(
    context: ContextDocument,
    links: Sequence[FormulaLink],
    *,
    row_keys: Sequence[str] | None = None,
    concept_ids: Sequence[str] | None = None,
    q: str | None = None,
    period_ids: Sequence[str] | None = None,
    phases: Sequence[str] | None = None,
    precedent_depth: int = 0,
    limit: int = 24,
    precedents: PrecedentLookup | None = None,
) -> ObservationDocument:
    """One observation per selected row × period. Raises ValueError without a selector."""
    needle = (q or "").strip()
    keys = set(row_keys or [])
    concepts = set(concept_ids or [])
    if not keys and not concepts and not needle:
        raise ValueError("selector_required")
    periods = set(period_ids or [])
    phase_set = set(phases or [])
    by_pair = _links_by_pair(links)
    axes = {axis.id: axis for axis in context.axes}
    found: list[Observation] = []
    for block in context.blocks:
        for row in block.rows:
            if not _selected(row, keys, concepts, needle):
                continue
            if block.kind == "params":
                found.extend(
                    _params_row(
                        block,
                        row,
                        by_pair,
                        periods=periods,
                        phases=phase_set,
                    )
                )
            else:
                found.extend(
                    _timeline_row(
                        block,
                        row,
                        axes,
                        by_pair,
                        periods=periods,
                        phases=phase_set,
                    )
                )
    kept = found[:limit]
    if precedent_depth > 0 and precedents is not None:
        labels = _row_labels(context)
        for item in kept:
            origin = f"{item.source.sheet}!{item.source.cell}"
            walked = [
                node for node in precedents(origin, precedent_depth) if node.depth >= 1
            ]
            item.formula.precedents_total = len(walked)
            item.formula.precedents = [_with_label(node, labels) for node in walked]
    return ObservationDocument(truncated=len(found) > limit, observations=kept)


def _row_labels(context: ContextDocument) -> dict[str, str]:
    labels: dict[str, str] = {}
    for block in context.blocks:
        for row in block.rows:
            labels.setdefault(row.row_key, row.label)
    return labels


def _with_label(
    precedent: ObservationPrecedent, labels: dict[str, str]
) -> ObservationPrecedent:
    key = precedent.row_key or ""
    label = labels.get(key) if key else None
    if precedent.label == label:
        return precedent
    return precedent.model_copy(update={"label": label})


def _selected(row: BlockRow, keys: set[str], concepts: set[str], needle: str) -> bool:
    if keys and row.row_key not in keys:
        return False
    if concepts and row.concept_id not in concepts:
        return False
    if needle and not own_label_contains(row.label, needle):
        return False
    return True


def _links_by_pair(links: Sequence[FormulaLink]) -> dict[tuple[str, str], list[FormulaLink]]:
    out: dict[tuple[str, str], list[FormulaLink]] = {}
    for link in links:
        if not link.row_key or not link.period_id:
            continue
        out.setdefault((link.row_key, link.period_id), []).append(link)
    return out


def _pick_link(
    by_pair: dict[tuple[str, str], list[FormulaLink]],
    row_key: str,
    period_id: str,
    cell: str,
) -> FormulaLink | None:
    matched = by_pair.get((row_key, period_id)) or []
    if not matched:
        return None
    for link in matched:
        if link.cell == cell or link.cell.endswith(f"!{cell}"):
            return link
    return matched[0]


def _formula(
    row: BlockRow,
    period_id: str,
    cell: str,
    by_pair: dict[tuple[str, str], list[FormulaLink]],
) -> ObservationFormula:
    link = _pick_link(by_pair, row.row_key, period_id, cell)
    if link is None:
        return ObservationFormula(text=row.formula, formula_class=None, precedents=[])
    return ObservationFormula(text=link.formula, formula_class=link.formula_class, precedents=[])


def _unit(row: BlockRow) -> ObservationUnit:
    hints = row.hints
    return ObservationUnit(
        kind=hints.unit,
        currency=hints.currency,
        scale=hints.scale,
        sign=hints.sign,
    )


def _dimensions(row: BlockRow) -> ObservationDimensions | None:
    if not row.hints.segment:
        return None
    return ObservationDimensions(segment=row.hints.segment)


def _cache(text: str | None, status: str | None) -> tuple[str | None, ValueStatus]:
    if status in {"cached", "empty", "zero_explicit", "not_applicable"}:
        resolved: ValueStatus = status
    else:
        resolved = "empty" if text in (None, "") else "cached"
    if resolved in {"empty", "not_applicable"}:
        return None, resolved
    if text in (None, ""):
        return None, resolved
    return str(text), resolved


def _at[T](items: Sequence[T], index: int) -> T | None:
    if index < 0 or index >= len(items):
        return None
    return items[index]


def _timeline_row(
    block: FinancialBlock,
    row: BlockRow,
    axes: dict,
    by_pair: dict[tuple[str, str], list[FormulaLink]],
    *,
    periods: set[str],
    phases: set[str],
) -> list[Observation]:
    series_for = {item.axis_id: item for item in row.series}
    axis_ids = list(block.axis_ids) or list(series_for)
    out: list[Observation] = []
    used_fallback = False
    for axis_id in axis_ids:
        axis = axes.get(axis_id)
        if axis is None:
            continue
        series = series_for.get(axis_id)
        if series is None:
            if row.series or used_fallback:
                continue
            used_fallback = True
            values: Sequence[str | None] = row.values
            statuses: Sequence[str] = row.value_statuses
            normalized: Sequence[str | None] = row.normalized_values
        else:
            values = series.values
            statuses = series.value_statuses
            normalized = series.normalized_values
        for index, period in enumerate(axis.periods):
            if periods and period.period_key not in periods:
                continue
            if phases and period.phase not in phases:
                continue
            out.append(
                _timeline_observation(
                    row,
                    axis_id,
                    period,
                    by_pair,
                    text=_at(values, index),
                    status=_at(statuses, index),
                    normalized=_at(normalized, index),
                )
            )
    return out


def _timeline_observation(
    row: BlockRow,
    axis_id: str,
    period: ContextPeriod,
    by_pair: dict[tuple[str, str], list[FormulaLink]],
    *,
    text: str | None,
    status: str | None,
    normalized: str | None,
) -> Observation:
    value, value_status = _cache(text, status)
    cell = format_addr(period.col, row.row)
    return Observation(
        row_key=row.row_key,
        label=row.label,
        concept_id=row.concept_id,
        disposition=row.disposition,
        dimensions=_dimensions(row),
        period_id=period.period_key,
        value=value,
        value_status=value_status,
        normalized_value=None if normalized is None else str(normalized),
        scale_factor=row.scale_factor,
        period_position=row.period_position,
        aggregation=row.aggregation,
        unit=_unit(row),
        formula=_formula(row, period.period_key, cell, by_pair),
        source=ObservationSource(sheet=row.sheet, cell=cell),
        timeline=ObservationTimeline(
            axis_id=axis_id,
            phase=period.phase,
            phase_year=period.phase_year,
            start_date=period.start_date,
            end_date=period.end_date,
            group_key=period.group_key,
            flags=dict(period.flags),
        ),
    )


def _params_row(
    block: FinancialBlock,
    row: BlockRow,
    by_pair: dict[tuple[str, str], list[FormulaLink]],
    *,
    periods: set[str],
    phases: set[str],
) -> list[Observation]:
    if phases:
        return []
    cells = sorted(
        (cell for cell in row.cells if cell.role in _VALUE_ROLES),
        key=lambda cell: cell.col,
    )
    series = next(
        (
            item
            for item in row.series
            if item.axis_id == block.block_id or item.axis_id in block.axis_ids
        ),
        None,
    )
    out: list[Observation] = []
    for index, cell in enumerate(cells):
        period_id = _params_period_id(block, cell)
        if periods and period_id not in periods:
            continue
        text = _at(series.values, index) if series is not None else cell.cached_value
        status = _at(series.value_statuses, index) if series is not None else None
        normalized = (
            _at(series.normalized_values, index)
            if series is not None
            else _at(row.normalized_values, index)
        )
        value, value_status = _cache(
            None if text is None else str(text),
            None if status is None else str(status),
        )
        addr = cell.addr or format_addr(cell.col, row.row)
        out.append(
            Observation(
                row_key=row.row_key,
                label=row.label,
                concept_id=row.concept_id,
                disposition=row.disposition,
                dimensions=_dimensions(row),
                period_id=period_id,
                value=value,
                value_status=value_status,
                normalized_value=None if normalized is None else str(normalized),
                scale_factor=row.scale_factor,
                period_position=row.period_position,
                aggregation=row.aggregation,
                scenario=cell.header or None,
                unit=_unit(row),
                formula=_formula(row, period_id, addr, by_pair),
                source=ObservationSource(sheet=row.sheet, cell=addr),
            )
        )
    return out


def _params_period_id(block: FinancialBlock, cell) -> str:
    for period in block.periods:
        if not isinstance(period, dict) or period.get("col") != cell.col:
            continue
        key = period.get("period_key")
        if key:
            return str(key)
    if cell.header:
        return cell.header
    return cell.addr or format_addr(cell.col, 0)
