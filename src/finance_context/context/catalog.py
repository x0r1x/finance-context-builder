"""Catalog projection over an already built ContextDocument."""

from __future__ import annotations

from finance_context.labels import own_label_contains
from finance_context.models.catalog import (
    CatalogAxis,
    CatalogDocument,
    CatalogHints,
    CatalogPeriod,
    CatalogRow,
)
from finance_context.models.context import BlockRow, ContextAxis, ContextDocument


def build_catalog(
    context: ContextDocument,
    *,
    q: str | None = None,
    sheet: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> CatalogDocument:
    """Rows and axes. Does not copy values, series, or normalized values."""
    needle = (q or "").strip()
    matched: list[CatalogRow] = []
    for block in context.blocks:
        for row in block.rows:
            if sheet is not None and row.sheet != sheet:
                continue
            if needle and not own_label_contains(row.label, needle):
                continue
            matched.append(_catalog_row(row, block.axis_ids))
    start = max(offset, 0)
    page = matched[start:] if limit is None else matched[start : start + limit]
    return CatalogDocument(
        total=len(matched),
        offset=start,
        limit=limit,
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
        kind=row.kind,
        axis_ids=list(axis_ids),
        has_formula=bool(row.formula) or any(bool(item.formula) for item in row.series),
        hints=CatalogHints(
            unit=hints.unit,
            currency=hints.currency,
            scale=hints.scale,
        ),
    )
