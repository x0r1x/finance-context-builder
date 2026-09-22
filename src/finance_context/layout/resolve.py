from __future__ import annotations

from finance_context.layout.models import (
    Axis,
    AxisHeader,
    AxisPeriod,
    Block,
    SheetLayout,
    TimeAxis,
)


def time_axis_from_embedded(axis: Axis, grain: str | None = None) -> TimeAxis:
    return TimeAxis(
        id=axis.id,
        grain=grain,
        header_row=axis.row,
        periods=[
            AxisPeriod(
                col=header.col,
                text=header.text,
                role=header.role,
                period_key=header.period_key,
            )
            for header in axis.headers
        ],
    )


def project_axis(axis: TimeAxis) -> Axis:
    return Axis(
        id=axis.id,
        row=axis.header_row,
        headers=[
            AxisHeader(
                col=period.col,
                text=period.text,
                role=period.role,
                period_key=period.period_key,
            )
            for period in axis.periods
        ],
    )


def axes_for(sheet: SheetLayout, block: Block) -> list[TimeAxis]:
    if getattr(block, "kind", "timeline") == "params":
        return []
    by_id = {axis.id: axis for axis in sheet.axes}
    picked = [by_id[axis_id] for axis_id in block.axis_ids if axis_id in by_id]
    if picked:
        return picked
    if block.axis is not None:
        return [time_axis_from_embedded(block.axis)]
    return []


def period_headers(sheet: SheetLayout, block: Block) -> list[AxisHeader]:
    if getattr(block, "kind", "timeline") == "params" and block.axis is not None:
        return list(block.axis.headers)
    headers: list[AxisHeader] = []
    for axis in axes_for(sheet, block):
        headers.extend(
            AxisHeader(
                col=period.col,
                text=period.text,
                role=period.role,
                period_key=period.period_key,
            )
            for period in axis.periods
        )
    return headers
