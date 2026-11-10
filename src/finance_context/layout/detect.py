from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from finance_context.formulas.models import as_edge
from finance_context.layout.axes import _axis_candidates, _series_row_above, _table_groups
from finance_context.layout.cells import _is_number, _text, bind_row_index, clear_row_index
from finance_context.layout.coverage import chart_note, coverage_blocks
from finance_context.layout.models import (
    Block,
    Layout,
    LayoutRow,
    SheetLayout,
    TimeAxis,
)
from finance_context.layout.params import (
    attach_orphan_switches,
    carve_params_regions,
    detect_params_block,
)
from finance_context.layout.periods import classify_header
from finance_context.layout.resolve import project_axis
from finance_context.layout.rows import (
    _band_title,
    _data_rows,
    _is_banner_gap,
    _is_empty_row,
    _label_span,
    _year_label_section,
)
from finance_context.layout.timelines import _share_repeated_axes, link_workbook_timelines


def detect_layout(
    cells: list[dict],
    *,
    date1904: bool = False,
    edges: list | Path | None = None,
    hidden_sheets: set[str] | None = None,
    hidden_columns: dict[str, set[int]] | None = None,
    hidden_rows: dict[str, set[int]] | None = None,
    summary_below: dict[str, bool] | None = None,
    defined_names: list | None = None,
) -> Layout:
    hidden = hidden_sheets or set()
    columns = hidden_columns or {}
    below = summary_below or {}
    if not isinstance(edges, Path):
        edges = None if edges is None else [as_edge(edge) for edge in edges]
    by_sheet: dict[str, list[dict]] = {}
    order: list[str] = []
    for cell in cells:
        name = cell["sheet"]
        bucket = by_sheet.get(name)
        if bucket is None:
            bucket = []
            by_sheet[name] = bucket
            order.append(name)
        bucket.append(cell)
    sheets = [
        _sheet_layout(
            name,
            by_sheet[name],
            date1904=date1904,
            edges=edges,
            hidden_sheets=hidden,
            hidden_columns=columns,
            hidden_rows=hidden_rows,
            summary_below=below,
            defined_names=defined_names,
        )
        for name in order
    ]
    layout = Layout(sheets=sheets)
    link_workbook_timelines(layout, cells, date1904)
    return layout


def _sheet_layout(
    name: str,
    sheet_cells: list[dict],
    *,
    date1904: bool,
    edges: list | Path | None,
    hidden_sheets: set[str],
    hidden_columns: dict[str, set[int]],
    hidden_rows: dict[str, set[int]] | None,
    summary_below: dict[str, bool],
    defined_names: list | None,
) -> SheetLayout:
    """Blocks for one sheet. The caller drops ``sheet_cells`` before the next sheet."""
    row_ids = None if hidden_rows is None else set(hidden_rows.get(name, ()))
    blocks, axes, notes = _blocks_for_sheet(
        name,
        sheet_cells,
        date1904,
        edges,
        hidden_cols=set(hidden_columns.get(name, ())),
        hidden_row_ids=row_ids,
        summary_below=summary_below.get(name, True),
        defined_names=defined_names,
    )
    chart = chart_note(name)
    if chart:
        notes.append(chart)
    return SheetLayout(
        name=name, blocks=blocks, axes=axes, hidden=name in hidden_sheets, notes=notes
    )


def _blocks_for_sheet(
    sheet: str,
    cells: list[dict],
    date1904: bool,
    edges: list | Path | None = None,
    hidden_cols: set[int] | None = None,
    hidden_row_ids: set[int] | None = None,
    summary_below: bool = True,
    defined_names: list | None = None,
) -> tuple[list[Block], list[TimeAxis], list[str]]:
    by_row: dict[int, list[dict]] = defaultdict(list)
    for cell in cells:
        by_row[int(cell["row"])].append(cell)
    bind_row_index(by_row)
    try:
        return _blocks_indexed(
            sheet,
            by_row,
            date1904,
            edges,
            hidden_cols,
            hidden_row_ids,
            summary_below,
            defined_names,
        )
    finally:
        clear_row_index()


def _blocks_indexed(
    sheet: str,
    by_row: dict[int, list[dict]],
    date1904: bool,
    edges: list | Path | None,
    hidden_cols: set[int] | None,
    hidden_row_ids: set[int] | None,
    summary_below: bool,
    defined_names: list | None,
) -> tuple[list[Block], list[TimeAxis], list[str]]:
    row_ids = sorted(by_row)
    found = _axis_candidates(sheet, by_row, date1904, hidden_cols)
    vertical = [item for item in found if item[1].transposed]
    candidates = [item for item in found if not item[1].transposed]
    groups = _table_groups(candidates)
    blocks: list[Block] = []
    params_blocks: list[Block] = []
    axes: list[TimeAxis] = []
    header_rows: set[int] = set()
    title_owner = _section_title_owners(groups, by_row, date1904)
    group_cols = [
        {period.col for _band, axis in group for period in axis.periods} for group in groups
    ]
    raw_bodies = [
        _group_body(index, groups, group_cols, row_ids, title_owner) for index in range(len(groups))
    ]
    bodies = _assign_overlapping_rows(raw_bodies, groups, group_cols, by_row, date1904)
    for index, group in enumerate(groups):
        band_rows = group[0][0]
        header_rows.update(band_rows)
        group_axes = [axis for _, axis in group]
        axes.extend(group_axes)
        body_rows = bodies[index]
        period_cols = group_cols[index]
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
            seed=_section_seed(by_row, group_axes[0], span, period_cols, date1904),
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
            )
        )
    blocks, axes = _share_repeated_axes(blocks, axes, by_row, date1904)
    turned_blocks, turned_axes = _transposed_blocks(sheet, by_row, date1904, vertical)
    blocks = [*blocks, *turned_blocks]
    axes = [*axes, *turned_axes]
    if params_blocks:
        blocks = sorted(
            [*blocks, *params_blocks],
            key=lambda block: min((row.row for row in block.rows), default=0),
        )
    if not blocks:
        extra = detect_params_block(sheet, by_row, date1904, edges, defined_names)
        if extra is not None:
            blocks.append(extra)
    attach_orphan_switches(sheet, blocks, by_row, date1904, defined_names)
    for block in blocks:
        axis = block.axis
        if axis is not None and axis.row:
            header_rows.add(int(axis.row))
    covered, notes = coverage_blocks(
        sheet, by_row, date1904, blocks, header_rows, hidden_cols
    )
    if covered:
        blocks = sorted(
            [*blocks, *covered],
            key=lambda block: min((row.row for row in block.rows), default=0),
        )
    return blocks, axes, notes


def _group_body(
    index: int,
    groups: list[list[tuple[list[int], TimeAxis]]],
    group_cols: list[set[int]],
    row_ids: list[int],
    title_owner: dict[int, int],
) -> list[int]:
    """Rows from this header until the next axis that replaces it.

    A later axis replaces this one only when it covers the same columns and is
    not narrower. A monthly chart inside a yearly table does not end the yearly
    table.
    """
    start = min(groups[index][0][0])
    current = group_cols[index]
    later: list[int] = []
    for other, other_group in enumerate(groups):
        other_start = min(other_group[0][0])
        if other == index or other_start <= start:
            continue
        if _axis_replaces(current, group_cols[other]):
            later.append(other_start)
    end = min(later) if later else (max(row_ids) + 1 if row_ids else start + 1)
    body = [row for row in row_ids if start <= row < end and title_owner.get(row, index) == index]
    above = [row for row, owner in title_owner.items() if owner == index and row < start]
    return sorted(set(body) | set(above))


def _axis_replaces(current: set[int], later: set[int]) -> bool:
    """True when ``later`` covers this axis and is at least as wide."""
    if not current or not later:
        return True
    overlap = len(current & later)
    return overlap >= 0.6 * len(current) and len(later) >= len(current)


def _assign_overlapping_rows(
    bodies: list[list[int]],
    groups: list[list[tuple[list[int], TimeAxis]]],
    group_cols: list[set[int]],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> list[list[int]]:
    """A row under two axes belongs to the one its values fit.

    The wide table keeps rows that spill past a nested ruler. The nested ruler
    keeps rows that sit inside its columns.
    """
    owners: dict[int, list[int]] = defaultdict(list)
    for index, rows in enumerate(bodies):
        for row_n in rows:
            owners[row_n].append(index)
    chosen: dict[int, int] = {}
    for row_n, indexes in owners.items():
        if len(indexes) == 1:
            chosen[row_n] = indexes[0]
            continue
        chosen[row_n] = _prefer_axis(row_n, indexes, groups, group_cols, by_row, date1904)
    return [
        [row_n for row_n in rows if chosen[row_n] == index] for index, rows in enumerate(bodies)
    ]


def _prefer_axis(
    row_n: int,
    indexes: list[int],
    groups: list[list[tuple[list[int], TimeAxis]]],
    group_cols: list[set[int]],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> int:
    occupied = _occupied_cols(by_row.get(row_n, []), date1904)
    best = indexes[0]
    best_key: tuple[int, int, int, int] | None = None
    for index in indexes:
        header = max(axis.header_row for _band, axis in groups[index])
        cols = group_cols[index]
        if not occupied:
            key = (0, 0, header, 0)
        else:
            covered = len(occupied & cols)
            outside = len(occupied - cols)
            width = max(cols) - min(cols) if cols else 0
            if covered == 0:
                key = (-1, -outside, header, -width)
            else:
                key = (int(outside == 0), covered - outside, header, -width)
        if best_key is None or key > best_key:
            best_key = key
            best = index
    return best


def _occupied_cols(cells: list[dict], date1904: bool) -> set[int]:
    found: set[int] = set()
    for cell in cells:
        col = int(cell["col"])
        if cell.get("formula_raw"):
            found.add(col)
            continue
        text = _text(cell, date1904)
        if text and _is_number(text):
            found.add(col)
    return found


def _transposed_blocks(
    sheet: str,
    by_row: dict[int, list[dict]],
    date1904: bool,
    vertical: list[tuple[list[int], TimeAxis]],
) -> tuple[list[Block], list[TimeAxis]]:
    blocks: list[Block] = []
    axes: list[TimeAxis] = []
    for band_rows, axis in vertical:
        col = axis.periods[0].col if axis.periods else 0
        rows = _transposed_items(by_row, date1904, col, band_rows)
        if not rows:
            continue
        axes.append(axis)
        blocks.append(
            Block(
                block_id=axis.id,
                label_col=col,
                axis=project_axis(axis),
                axis_ids=[axis.id],
                rows=rows,
                kind="timeline",
            )
        )
    return blocks, axes


def _transposed_items(
    by_row: dict[int, list[dict]],
    date1904: bool,
    period_col: int,
    period_rows: list[int],
) -> list[LayoutRow]:
    """One line item per labelled column to the right of a vertical ruler."""
    if not period_rows:
        return []
    label_row = _series_row_above(by_row, period_col, min(period_rows), date1904)
    labels: dict[int, str] = {}
    if label_row is not None and label_row in by_row:
        for cell in by_row[label_row]:
            col = int(cell["col"])
            if col <= period_col:
                continue
            text = _text(cell, date1904)
            if text and classify_header(text) is None and not _is_number(text):
                labels[col] = text.strip()
    value_cols: set[int] = set()
    for row_n in period_rows:
        for cell in by_row.get(row_n, []):
            col = int(cell["col"])
            if col <= period_col:
                continue
            shown = _text(cell, date1904)
            if cell.get("formula_raw") or (shown and _is_number(shown)):
                value_cols.add(col)
    items: list[LayoutRow] = []
    prev: int | None = None
    anchor = label_row if label_row is not None and label_row >= 1 else min(period_rows)
    for col in sorted(set(labels) | value_cols):
        # Two empty columns end the ruler. One empty column does not.
        if prev is not None and col - prev >= 3:
            break
        label = labels.get(col)
        if not label:
            prev = col
            continue
        items.append(
            LayoutRow(
                row=anchor,
                label=label,
                kind="fact",
                label_col=col,
                value_col=col,
            )
        )
        prev = col
    return items


def _section_seed(by_row, axis, span, period_cols, date1904):
    """Band title, or the word beside the year that filled a quarter ruler."""
    header = axis.header_row
    seed = _band_title(by_row, header, span, period_cols, date1904)
    if seed is not None or axis.grain != "quarter":
        return seed
    years = {
        period.period_key[:4]
        for period in axis.periods
        if period.period_key[:4].isdigit()
    }
    return _year_label_section(by_row, header, period_cols, date1904, years)


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
        axis = group[0][1]
        period_cols = {period.col for _band, item in group for period in item.periods}
        years = {
            period.period_key[:4]
            for period in axis.periods
            if period.period_key[:4].isdigit()
        }
        section = (
            _year_label_section(by_row, axis.header_row, period_cols, date1904, years)
            if axis.grain == "quarter"
            else None
        )
        for row_n in _label_rows_above(by_row, start, period_cols, date1904, floor):
            # A note between the named year and the quarters is not the section.
            if section is not None and section.row < row_n < start:
                continue
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
    # A single text banner stacked on the header used to sit inside the band,
    # so titles above that banner belong here. A banner with a title already
    # between it and the header is the previous table's boundary.
    can_skip_banner = True
    for row_n in range(header_row - 1, floor, -1):
        cells = by_row.get(row_n)
        if not cells or _is_empty_row(cells, date1904):
            continue
        if can_skip_banner and _is_banner_gap(cells, period_cols, date1904):
            can_skip_banner = False
            continue
        can_skip_banner = False
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
