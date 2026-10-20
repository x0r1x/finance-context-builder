from __future__ import annotations

from collections import defaultdict

from finance_context.layout.axes import _axis_candidates, _table_groups
from finance_context.layout.cells import _is_number, _text
from finance_context.layout.models import (
    Block,
    Layout,
    SheetLayout,
    TimeAxis,
)
from finance_context.layout.params import carve_params_regions, detect_params_block
from finance_context.layout.resolve import project_axis
from finance_context.layout.rows import _band_title, _data_rows, _is_empty_row, _label_span
from finance_context.layout.timelines import _share_repeated_axes, link_workbook_timelines


def detect_layout(
    cells: list[dict],
    *,
    date1904: bool = False,
    edges: list[dict] | None = None,
    hidden_sheets: set[str] | None = None,
    hidden_columns: dict[str, set[int]] | None = None,
    hidden_rows: dict[str, set[int]] | None = None,
    summary_below: dict[str, bool] | None = None,
) -> Layout:
    hidden = hidden_sheets or set()
    columns = hidden_columns or {}
    below = summary_below or {}
    by_sheet: dict[str, list[dict]] = {}
    for cell in cells:
        by_sheet.setdefault(cell["sheet"], []).append(cell)
    sheets = []
    for name, sheet_cells in by_sheet.items():
        row_ids = None if hidden_rows is None else set(hidden_rows.get(name, ()))
        blocks, axes = _blocks_for_sheet(
            name,
            sheet_cells,
            date1904,
            edges,
            hidden_cols=set(columns.get(name, ())),
            hidden_row_ids=row_ids,
            summary_below=below.get(name, True),
        )
        sheets.append(SheetLayout(name=name, blocks=blocks, axes=axes, hidden=name in hidden))
    layout = Layout(sheets=sheets)
    link_workbook_timelines(layout, cells, date1904)
    return layout


def _blocks_for_sheet(
    sheet: str,
    cells: list[dict],
    date1904: bool,
    edges: list[dict] | None = None,
    hidden_cols: set[int] | None = None,
    hidden_row_ids: set[int] | None = None,
    summary_below: bool = True,
) -> tuple[list[Block], list[TimeAxis]]:
    by_row: dict[int, list[dict]] = defaultdict(list)
    for cell in cells:
        by_row[int(cell["row"])].append(cell)
    row_ids = sorted(by_row)
    candidates = _axis_candidates(sheet, by_row, date1904, hidden_cols)
    groups = _table_groups(candidates)
    blocks: list[Block] = []
    params_blocks: list[Block] = []
    axes: list[TimeAxis] = []
    title_owner = _section_title_owners(groups, by_row, date1904)
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
        body_rows = [
            r
            for r in row_ids
            if start <= r < end and title_owner.get(r, index) == index
        ]
        above = [r for r, owner in title_owner.items() if owner == index and r < start]
        body_rows = sorted(set(body_rows) | set(above))
        period_cols = {period.col for axis in group_axes for period in axis.periods}
        label_col, span = _label_span(
            by_row,
            body_rows,
            set(band_rows),
            period_cols,
            date1904,
        )
        reveal_rows = {row_n for row_n, owner in title_owner.items() if owner == index}
        data_rows = _data_rows(
            by_row,
            body_rows,
            set(band_rows),
            span,
            date1904,
            period_cols,
            seed=_band_title(by_row, group_axes[0].header_row, span, period_cols, date1904),
            reveal_rows=reveal_rows,
            summary_below=summary_below,
            hidden_row_ids=hidden_row_ids,
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
                header_rows=sorted({row_n for band, _axis in group for row_n in band}),
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


def _section_title_owners(
    groups: list[list[tuple[list[int], TimeAxis]]],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> dict[int, int]:
    """Label-only rows above a header belong to that block, not the one they close."""
    owners: dict[int, int] = {}
    starts = [min(group[0][0]) for group in groups]
    for index, group in enumerate(groups):
        start = starts[index]
        earlier = [other for other in starts if other < start]
        floor = max(earlier) if earlier else 0
        period_cols = {period.col for _band, axis in group for period in axis.periods}
        for row_n in _label_rows_above(by_row, start, period_cols, date1904, floor):
            owners[row_n] = index
    return owners


def _label_rows_above(
    by_row: dict[int, list[dict]],
    header_row: int,
    period_cols: set[int],
    date1904: bool,
    floor: int,
) -> list[int]:
    if not period_cols:
        return []
    found: list[int] = []
    for row_n in range(header_row - 1, floor, -1):
        cells = by_row.get(row_n)
        if not cells or _is_empty_row(cells, date1904):
            continue
        if not _is_label_only_row(cells, period_cols, date1904):
            break
        found.append(row_n)
    return found


def _is_label_only_row(
    cells: list[dict], period_cols: set[int], date1904: bool
) -> bool:
    left = min(period_cols)
    has_label = False
    for cell in cells:
        if cell.get("formula_raw"):
            return False
        text = _text(cell, date1904)
        if not text:
            continue
        col = int(cell["col"])
        if col in period_cols or _is_number(text):
            return False
        if col < left:
            has_label = True
    return has_label
