from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from finance_context.formulas.models import as_edge
from finance_context.layout.axes import (
    _axis_candidates,
    _series_row_above,
    _table_groups,
    is_labeled_model_year,
    local_model_year_regions,
)
from finance_context.layout.cells import (
    _cell_at,
    _is_number,
    _text,
    bind_row_index,
    clear_row_index,
)
from finance_context.layout.coverage import (
    _disambiguate_ids,
    _without_taken,
    chart_note,
    coverage_blocks,
)
from finance_context.layout.local_tables import local_table_blocks
from finance_context.layout.models import (
    Axis,
    AxisHeader,
    Block,
    Layout,
    LayoutRow,
    RowCell,
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
    _column_headers,
    _data_rows,
    _is_banner_gap,
    _is_empty_row,
    _label_span,
    _year_label_section,
)
from finance_context.layout.sensitivity import sensitivity_blocks
from finance_context.layout.timelines import _share_repeated_axes, link_workbook_timelines
from finance_context.layout.units import is_unit_text


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
    name_headers: tuple[str, ...] | None = None,
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
            name_headers=name_headers,
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
    name_headers: tuple[str, ...] | None = None,
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
        name_headers=name_headers,
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
    name_headers: tuple[str, ...] | None = None,
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
            name_headers,
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
    name_headers: tuple[str, ...] | None = None,
) -> tuple[list[Block], list[TimeAxis], list[str]]:
    grids, grid_cells = sensitivity_blocks(sheet, by_row, date1904)
    if grid_cells:
        by_row = _without_taken(by_row, grid_cells)
        bind_row_index(by_row)
    found, axis_notes = _axis_candidates(sheet, by_row, date1904, hidden_cols)
    horizontal_headers = sorted({axis.header_row for _band, axis in found if not axis.transposed})
    panels, panel_cells, schedules = local_table_blocks(
        sheet, by_row, date1904, horizontal_headers, hidden_cols, edges, defined_names,
        hidden_row_ids=hidden_row_ids,
    )
    if panel_cells:
        by_row = _without_taken(by_row, panel_cells)
        bind_row_index(by_row)
    local_claims: dict[int, set[int]] = defaultdict(set)
    for row, col in grid_cells | panel_cells:
        local_claims[row].add(col)
    row_ids = sorted(by_row)
    vertical = [
        item for item in found if item[1].transposed
        and not any((period.row, period.col) in panel_cells for period in item[1].periods)
    ]
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
    group_headers = [{axis.header_row for _band, axis in group} for group in groups]
    raw_bodies = [
        _group_body(index, groups, group_cols, row_ids, title_owner) for index in range(len(groups))
    ]
    bodies = _assign_overlapping_rows(raw_bodies, groups, group_cols, by_row, date1904)
    for index, group in enumerate(groups):
        band_rows = group[0][0]
        group_axes = [axis for _, axis in group]
        axes.extend(group_axes)
        body_rows = bodies[index]
        period_cols = group_cols[index]
        reveal_rows = {row_n for row_n, owner in title_owner.items() if owner == index}
        table_headers, column_roles = _column_headers(
            by_row, band_rows, reveal_rows, period_cols, date1904
        )
        header_rows.update(table_headers)
        label_col, span = _label_span(
            by_row,
            body_rows,
            table_headers,
            period_cols,
            date1904,
            column_roles=column_roles,
            name_headers=name_headers,
        )
        axis = group_axes[0]
        # Only the row whose year filled these quarter keys. Another 2024
        # below that banner is a value.
        section = _quarter_year_row(by_row, axis, period_cols, date1904)
        if section is not None:
            body_rows = [row_n for row_n in body_rows if row_n != section.row]
        data_rows = _data_rows(
            by_row,
            body_rows,
            table_headers,
            span,
            date1904,
            period_cols,
            seed=_section_seed(by_row, axis, span, period_cols, date1904),
            reveal_rows=reveal_rows,
            summary_below=summary_below,
            hidden_row_ids=hidden_row_ids,
            column_roles=column_roles,
            name_headers=name_headers,
            intervening_rulers=sorted({
                header
                for other, columns in enumerate(group_cols)
                if other != index and period_cols & columns
                for header in group_headers[other]
            }),
        )
        data_rows, carved = carve_params_regions(
            sheet, data_rows, by_row, date1904, period_cols, label_col
        )
        for row in data_rows:
            row.excluded_cols = sorted(period_cols & local_claims.get(row.row, set()))
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
    for region, scheduled_axes in schedules:
        scheduled_blocks, local_axes = _transposed_blocks(
            sheet, region, date1904, scheduled_axes
        )
        turned_blocks.extend(scheduled_blocks)
        turned_axes.extend(local_axes)
    blocks = [*blocks, *turned_blocks]
    axes = [*axes, *turned_axes]
    if params_blocks:
        blocks = sorted(
            [*blocks, *params_blocks],
            key=lambda block: min((row.row for row in block.rows), default=0),
        )
    timeline_rows = {int(axis.header_row) for axis in axes}
    year_blocks = _local_year_blocks(
        sheet,
        local_model_year_regions(
            by_row, date1904, set(hidden_cols or ()), timeline_rows
        ),
        by_row,
        date1904,
    )
    if year_blocks:
        blocks = [*blocks, *year_blocks]
    if not blocks:
        extra = detect_params_block(sheet, by_row, date1904, edges, defined_names)
        if extra is not None:
            blocks.append(extra)
    parameters = _parameters_above_model_years(sheet, blocks, axes, by_row, date1904)
    if parameters:
        blocks = sorted(
            [*blocks, *parameters],
            key=lambda block: min((row.row for row in block.rows), default=0),
        )
    attach_orphan_switches(sheet, blocks, by_row, date1904, defined_names)
    for block in blocks:
        axis = block.axis
        if axis is not None and axis.row:
            header_rows.add(int(axis.row))
    covered, notes = coverage_blocks(
        sheet,
        by_row,
        date1904,
        blocks,
        header_rows,
        hidden_cols,
        taken=_vertical_period_cells(axes),
        axes=axes,
    )
    if covered or grids or panels:
        local = [*covered, *grids, *panels]
        _disambiguate_ids(local)
        blocks = sorted(
            [*blocks, *local],
            key=lambda block: min((row.row for row in block.rows), default=0),
        )
    return blocks, axes, [*axis_notes, *notes]


def _local_year_blocks(
    sheet: str,
    regions: list[tuple[int, int | None, list[tuple[int, str]], list[int]]],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> list[Block]:
    """Publish an unconfirmed ``Year N`` header with every column still in the series."""
    blocks: list[Block] = []
    for header_row, label_col, columns, body_rows in regions:
        if label_col is None or not body_rows:
            continue
        headers = [
            AxisHeader(col=col, text=text, role="unknown", period_key=f"c{col}")
            for col, text in columns
        ]
        rows: list[LayoutRow] = []
        for row_n in body_rows:
            cells = by_row.get(row_n) or []
            label_cell = _cell_at(cells, label_col)
            label = _text(label_cell, date1904) if label_cell else None
            if not label or _is_number(label):
                continue
            tagged = [
                RowCell(col=col, role="unknown")
                for col, _caption in columns
                if _year_value_cell(_cell_at(cells, col), date1904)
            ]
            if not tagged:
                continue
            rows.append(
                LayoutRow(
                    row=row_n,
                    label=label.strip(),
                    kind="fact",
                    label_col=label_col,
                    cells=tagged,
                )
            )
        if not rows:
            continue
        block_id = f"{sheet}!r{header_row}c{columns[0][0]}"
        blocks.append(
            Block(
                block_id=block_id,
                label_col=label_col,
                axis=Axis(id=block_id, row=header_row, headers=headers),
                rows=rows,
                kind="params",
            )
        )
    return blocks


def _year_value_cell(cell: dict | None, date1904: bool) -> bool:
    if cell is None:
        return False
    text = _text(cell, date1904)
    return bool(
        cell.get("formula_raw")
        or cell.get("formula_template")
        or (text and _is_number(text))
    )


def _parameters_above_model_years(
    sheet: str,
    blocks: list[Block],
    axes: list[TimeAxis],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> list[Block]:
    """Keep the parameter rows and their heading above a ``Year N`` header."""
    owned = {row.row for block in blocks for row in block.rows}
    found: list[Block] = []
    for axis in axes:
        if axis.grain != "model_year" or not axis.periods:
            continue
        if not all(is_labeled_model_year(period.text) for period in axis.periods):
            continue
        cols = {period.col for period in axis.periods}
        left = min(cols)
        picked: list[tuple[int, dict, list[dict]]] = []
        for row_n in range(axis.header_row - 1, 0, -1):
            if row_n in owned:
                break
            cells = by_row.get(row_n)
            if not cells or _is_empty_row(cells, date1904):
                if picked:
                    break
                continue
            label = _label_left_of(cells, left, date1904)
            scalars = _scalars_in_columns(cells, cols, date1904)
            if label is not None and not scalars:
                if picked:
                    break
                continue
            if label is None or not scalars or _year_caption_row(scalars, date1904):
                break
            picked.append((row_n, label, scalars))
        if not picked:
            continue
        picked.reverse()
        block = _parameter_block(sheet, picked, date1904)
        title = _title_above_parameters(by_row, block.rows[0].row, block.label_col, owned, date1904)
        if title is not None:
            block.rows.insert(0, title)
        owned.update(row.row for row in block.rows)
        found.append(block)
    return found


def _title_above_parameters(
    by_row: dict[int, list[dict]],
    first_row: int,
    label_col: int,
    owned: set[int],
    date1904: bool,
) -> LayoutRow | None:
    """Look through blank rows for a standalone heading aligned with the parameters."""
    for row_n in range(first_row - 1, 0, -1):
        if row_n in owned:
            break
        cells = by_row.get(row_n, [])
        if any(cell.get("formula_raw") or cell.get("formula_template") for cell in cells):
            break
        populated = [(cell, text) for cell in cells if (text := _text(cell, date1904))]
        if not populated:
            continue
        if len(populated) != 1:
            break
        cell, text = populated[0]
        if (
            int(cell["col"]) != label_col
            or cell.get("hidden")
            or _is_number(text)
            or is_unit_text(text)
        ):
            break
        return LayoutRow(row=row_n, label=text.strip(), kind="abstract", label_col=label_col)
    return None


def _label_left_of(cells: list[dict], left: int, date1904: bool) -> dict | None:
    found: dict | None = None
    for cell in cells:
        if cell.get("hidden"):
            continue
        col = int(cell["col"])
        if col >= left:
            continue
        text = _text(cell, date1904)
        if not text or _is_number(text):
            continue
        if found is None or col < int(found["col"]):
            found = cell
    return found


def _scalars_in_columns(
    cells: list[dict], cols: set[int], date1904: bool
) -> list[dict]:
    found: list[dict] = []
    for cell in sorted(cells, key=lambda item: int(item["col"])):
        if int(cell["col"]) not in cols or cell.get("hidden"):
            continue
        text = _text(cell, date1904)
        if cell.get("formula_raw") or cell.get("formula_template") or text:
            found.append(cell)
    return found


def _year_caption_row(cells: list[dict], date1904: bool) -> bool:
    captions = [
        cell
        for cell in cells
        if is_labeled_model_year(_text(cell, date1904)) and not cell.get("formula_raw")
    ]
    return len(captions) >= 2


def _parameter_block(
    sheet: str,
    rows: list[tuple[int, dict, list[dict]]],
    date1904: bool,
) -> Block:
    label_col = int(rows[0][1]["col"])
    columns = sorted({int(cell["col"]) for _row, _label, scalars in rows for cell in scalars})
    single = len(columns) == 1
    headers = [
        AxisHeader(
            col=col,
            text="",
            role="value",
            period_key="value" if single else f"c{col}",
        )
        for col in columns
    ]
    first = rows[0][0]
    block_id = f"{sheet}!r{first}"
    layout_rows = [
        LayoutRow(
            row=row_n,
            label=str(_text(label, date1904) or "").strip(),
            kind="fact",
            label_col=int(label["col"]),
            cells=[RowCell(col=int(cell["col"]), role="value") for cell in scalars],
        )
        for row_n, label, scalars in rows
    ]
    return Block(
        block_id=block_id,
        label_col=label_col,
        axis=Axis(id=block_id, row=first, headers=headers),
        rows=layout_rows,
        kind="params",
    )


def _vertical_period_cells(axes: list[TimeAxis]) -> set[tuple[int, int]]:
    """Cells of a vertical ruler. The rest of those rows can still be a list."""
    taken: set[tuple[int, int]] = set()
    for axis in axes:
        if not axis.transposed:
            continue
        for period in axis.periods:
            if period.row:
                taken.add((int(period.row), int(period.col)))
    return taken


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
            if (
                text
                and classify_header(text) is None
                and not _is_number(text)
                and not is_unit_text(text)
            ):
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


def _quarter_year_row(by_row, axis, period_cols, date1904):
    """The labelled year that was borrowed into this quarter ruler.

    Quarters that already name a year (``1 кв. 2024``) did not borrow the row
    below. ``Год ввода`` there is a fact, not the section that filled the keys.
    """
    if axis.grain != "quarter":
        return None
    for cell in by_row.get(axis.header_row, []):
        if int(cell["col"]) not in period_cols:
            continue
        hit = classify_header(_text(cell, date1904))
        if hit is not None and hit.period_key[:4].isdigit():
            return None
    years = {
        period.period_key[:4]
        for period in axis.periods
        if period.period_key[:4].isdigit()
    }
    return _year_label_section(by_row, axis.header_row, period_cols, date1904, years)


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
