"""Catalog projection over an already built ContextDocument."""

from __future__ import annotations

from finance_context.models.catalog import (
    CatalogAxis,
    CatalogDocument,
    CatalogHints,
    CatalogPeriod,
    CatalogRow,
    SliceMappingStats,
)
from finance_context.models.context import BlockRow, ContextAxis, ContextDocument, MappingStats


def slice_mapping_stats(stats: MappingStats) -> SliceMappingStats:
    return SliceMappingStats(
        inventory_rows=stats.inventory_rows,
        mapped=stats.mapped,
        abstained=stats.abstained,
        excluded=stats.excluded,
        concept_coverage=stats.concept_coverage,
    )


def build_catalog(
    context: ContextDocument,
    *,
    q: str | None = None,
    concept_ids: list[str] | None = None,
    labels: list[str] | None = None,
    sheet: str | None = None,
    disposition: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> CatalogDocument:
    """Rows and axes. Does not copy values, series, or normalized values."""
    needle = (q or "").strip()
    concepts = set(concept_ids or [])
    exact_labels = {item.strip().casefold() for item in (labels or []) if item.strip()}
    matched: list[CatalogRow] = []
    for block in context.blocks:
        for row in block.rows:
            if sheet is not None and row.sheet != sheet:
                continue
            if disposition is not None and row.disposition != disposition:
                continue
            if concepts and row.concept_id not in concepts:
                continue
            if not _label_exact(row, exact_labels):
                continue
            if needle and not _label_hit(row, needle):
                continue
            matched.append(_catalog_row(row, block.axis_ids))
    start = max(offset, 0)
    page = matched[start:] if limit is None else matched[start : start + limit]
    return CatalogDocument(
        total=len(matched),
        offset=start,
        limit=limit,
        mapping_stats=slice_mapping_stats(context.mapping_stats),
        axes=[_catalog_axis(axis) for axis in context.axes],
        rows=page,
    )


def _catalog_axis(axis: ContextAxis) -> CatalogAxis:
    return CatalogAxis(
        id=axis.id,
        sheet=axis.sheet,
        grain=axis.grain,
        periods=[
            CatalogPeriod(
                period_key=period.period_key,
                phase=period.phase,
                phase_year=period.phase_year,
                start_date=period.start_date,
                end_date=period.end_date,
                group_key=period.group_key,
                flags=dict(period.flags),
            )
            for period in axis.periods
        ],
    )


def _catalog_row(row: BlockRow, axis_ids: list[str]) -> CatalogRow:
    hints = row.hints
    return CatalogRow(
        row_key=row.row_key,
        sheet=row.sheet,
        label=row.label,
        label_path=list(row.label_path),
        concept_id=row.concept_id,
        disposition=row.disposition,
        kind=row.kind,
        axis_ids=list(axis_ids),
        period_position=row.period_position,
        aggregation=row.aggregation,
        has_formula=bool(row.formula) or any(bool(item.formula) for item in row.series),
        hints=CatalogHints(
            unit=hints.unit,
            currency=hints.currency,
            scale=hints.scale,
            sign=hints.sign,
        ),
    )


def _label_exact(row: BlockRow, labels: set[str]) -> bool:
    if not labels:
        return True
    return row.label.casefold() in labels


def _label_hit(row: BlockRow, needle: str) -> bool:
    folded = needle.casefold()
    if folded in row.label.casefold():
        return True
    return any(folded in part.casefold() for part in row.label_path)
