from __future__ import annotations

from finance_context.context.measure import Measure, _combine_measure, _parse_text
from finance_context.context.series import normalize_series, scale_factor_for, value_status
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


def _row_for_layout(
    *,
    sheet_name: str,
    block_id: str,
    layout_row: LayoutRow,
    layout_row_parent: str | None,
    headers: list,
    value_headers: list,
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
    label_path: list[str],
    neighbors: list[str],
    formula: str | None,
    neighbor_ranges: list[str] | None = None,
    formula_exceptions: list[str],
    numeric_summary: NumericSummary | None,
    hints: RowHints,
    role_cells: list[RoleCell],
    series_unit: str | None,
    series: list[RowSeries] | None = None,
) -> BlockRow:
    row_num = layout_row.row
    keep_cols = {header.col for header in value_headers}
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
        structural = layout_row.kind not in _SERIES_KINDS and text is None
        statuses.append(value_status(text, applicable=not structural))
    unit = hints.unit or series_unit
    factor = scale_factor_for(hints.scale)
    if series:
        series = [
            item.model_copy(
                update={"normalized_values": normalize_series(list(item.values), factor)}
            )
            for item in series
        ]
        values = list(series[0].values)
        statuses = list(series[0].value_statuses)
    normalized = normalize_series(values, factor)
    return BlockRow(
        row_key=f"{sheet_name}|{row_num}|{block_id}",
        sheet=sheet_name,
        row=row_num,
        label=layout_row.label,
        parent_label=layout_row_parent,
        unit=unit,
        scale_factor=factor,
        values=values,
        value_statuses=statuses,
        normalized_values=normalized,
        series=list(series or []),
        kind=layout_row.kind,
        indent=layout_row.indent,
        hidden=layout_row.hidden,
        check_row=layout_row.check_row,
        label_path=label_path,
        neighbors=neighbors,
        neighbor_ranges=list(neighbor_ranges or []),
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


def _resolved_hints(
    layout_row: LayoutRow,
    unit_text: str | None = None,
    *,
    unit_from_cell: str | None = None,
    number_formats: list[str] | None = None,
) -> RowHints:
    """Currency, scale, and unit for one row. The label is parsed once.

    ``unit_text`` is the block caption or the row's own unit. ``unit_from_cell``
    is only that row's unit cell. The label can still name the unit when the
    caption does, and a percent format can replace a money caption.
    """
    label_measure = _parse_text(layout_row.label or "")
    hinted = _combine_measure(unit_text, label_measure, number_formats)
    if hinted.unit == "money" and _percent_formatted(number_formats or []):
        hinted = Measure(unit="rate")
    if hinted.unit == "rate":
        row_measure = Measure(unit="rate")
    else:
        row_measure = _combine_measure(unit_from_cell, label_measure, None)
    # row_measure is main's second pass (row unit cell + label) and wins for unit.
    # hinted is the first pass (block or row caption, label, and number formats)
    # and wins for unit_per, currency, and scale. This mirrors main's old
    # `unit = m2.unit or hints.unit` and `hints.x or m2.x` exactly. It must not be normalized.
    return RowHints(
        unit=row_measure.unit or hinted.unit,
        unit_per=hinted.per or row_measure.per,
        currency=hinted.currency or row_measure.currency,
        scale=hinted.scale or row_measure.scale,
    )


def _percent_formatted(formats: list[str]) -> bool:
    percents = sum(1 for fmt in formats if "%" in fmt)
    return percents > 0 and percents * 2 >= len(formats)
