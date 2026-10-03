from __future__ import annotations

from collections import defaultdict

from finance_context.layout.axes import _axis_candidates, _table_groups
from finance_context.layout.models import (
    Block,
    Layout,
    SheetLayout,
    TimeAxis,
)
from finance_context.layout.params import carve_params_regions, detect_params_block
from finance_context.layout.resolve import project_axis
from finance_context.layout.rows import _band_title, _data_rows, _label_span
from finance_context.layout.timelines import _share_repeated_axes, link_workbook_timelines


def detect_layout(
    cells: list[dict], *, date1904: bool = False, edges: list[dict] | None = None
) -> Layout:
    by_sheet: dict[str, list[dict]] = {}
    for cell in cells:
        by_sheet.setdefault(cell["sheet"], []).append(cell)
    sheets = []
    for name, sheet_cells in by_sheet.items():
        blocks, axes = _blocks_for_sheet(name, sheet_cells, date1904, edges)
        sheets.append(SheetLayout(name=name, blocks=blocks, axes=axes))
    layout = Layout(sheets=sheets)
    link_workbook_timelines(layout, cells, date1904)
    return layout


def _blocks_for_sheet(
    sheet: str,
    cells: list[dict],
    date1904: bool,
    edges: list[dict] | None = None,
) -> tuple[list[Block], list[TimeAxis]]:
    by_row: dict[int, list[dict]] = defaultdict(list)
    for cell in cells:
        by_row[int(cell["row"])].append(cell)
    row_ids = sorted(by_row)
    candidates = _axis_candidates(sheet, by_row, date1904)
    groups = _table_groups(candidates)
    blocks: list[Block] = []
    params_blocks: list[Block] = []
    axes: list[TimeAxis] = []
    for index, group in enumerate(groups):
        band_rows = group[0][0]
        group_axes = [axis for _, axis in group]
        axes.extend(group_axes)
        start = min(band_rows)
        later = [
            min(other[0][0])
            for other_index, other in enumerate(groups)
            if other_index != index and min(other[0][0]) > start
        ]
        end = min(later) if later else (max(row_ids) + 1 if row_ids else start + 1)
        body_rows = [r for r in row_ids if start <= r < end]
        period_cols = {period.col for axis in group_axes for period in axis.periods}
        label_col, span = _label_span(
            by_row,
            body_rows,
            set(band_rows),
            period_cols,
            date1904,
        )
        data_rows = _data_rows(
            by_row,
            body_rows,
            set(band_rows),
            span,
            date1904,
            period_cols,
            seed=_band_title(by_row, group_axes[0].header_row, span, period_cols, date1904),
        )
        data_rows, carved = carve_params_regions(
            sheet, data_rows, by_row, date1904, period_cols, label_col
        )
        params_blocks.extend(carved)
        if len(group_axes) == 1:
            block_id = group_axes[0].id
        else:
            block_id = f"{sheet}!r{group_axes[0].header_row}"
        blocks.append(
            Block(
                block_id=block_id,
                label_col=label_col,
                axis=project_axis(group_axes[0]) if len(group_axes) == 1 else None,
                axis_ids=[axis.id for axis in group_axes],
                rows=data_rows,
                kind="timeline",
            )
        )
    blocks, axes = _share_repeated_axes(blocks, axes, by_row, date1904)
    if params_blocks:
        blocks = sorted(
            [*blocks, *params_blocks],
            key=lambda block: min((row.row for row in block.rows), default=0),
        )
    if not blocks:
        extra = detect_params_block(sheet, by_row, date1904, edges)
        if extra is not None:
            blocks.append(extra)
    return blocks, axes
