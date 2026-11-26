"""Identify independently headed regions before assigning rows to sheet-wide blocks."""

from __future__ import annotations

from bisect import bisect_right
from itertools import pairwise

from finance_context.excel.a1 import index_to_col
from finance_context.layout.axes import _vertical_axes
from finance_context.layout.cells import _is_number, _text
from finance_context.layout.coverage import _value_role, _without_taken, coverage_blocks
from finance_context.layout.models import Axis, AxisHeader, Block, LayoutRow, RowCell, TimeAxis
from finance_context.layout.params import detect_params_block
from finance_context.layout.periods import classify_header, is_calendar_hit
from finance_context.layout.rows import _vertical_aggregate_rows


def local_table_blocks(
    sheet, by_row, date1904, horizontal_headers, hidden_cols, edges, defined_names
):
    blocks, taken, schedules = _panels(
        sheet, by_row, date1904, horizontal_headers, edges, defined_names
    )
    if horizontal_headers:
        blocks.extend(_headed_sections(sheet, by_row, date1904, horizontal_headers, taken))
        return blocks, taken, schedules
    remaining = _without_taken(by_row, taken)
    stacked = _stacked_tables(sheet, remaining, date1904)
    blocks.extend(stacked)
    taken.update(_coordinates(stacked))
    remaining = _without_taken(remaining, taken)
    side = _side_lists(sheet, remaining, date1904, hidden_cols)
    blocks.extend(side)
    taken.update(_coordinates(side))
    return blocks, taken, schedules


def _headed_sections(sheet, by_row, date1904, horizontal_headers, taken):
    """Reserve side-by-side local tables within a horizontal timeline's body."""
    remaining = _without_taken(by_row, taken)
    blocks = []
    last = max(remaining, default=0) + 1
    for n, cells in sorted(remaining.items()):
        next_header = bisect_right(horizontal_headers, n)
        if not next_header or n == horizontal_headers[next_header - 1]:
            continue
        captions = []
        for col, text in _populated(cells, date1904):
            if (n, col) in taken or not _caption(text):
                break
            if captions and col != captions[-1][0] + 1:
                break
            captions.append((col, text))
        if len(captions) < 2:
            continue
        first, right = captions[0][0], captions[-1][0]
        anchor = _section_anchor(remaining, n, first, right, date1904, taken)
        if anchor is None:
            continue
        title_row, left, _ = anchor
        end = horizontal_headers[next_header] if next_header < len(horizontal_headers) else last
        block = _section_table(sheet, remaining, n, end, anchor, captions, date1904)
        if block is None:
            continue
        neighbors = _section_neighbors(sheet, remaining, block, end, date1904)
        if not neighbors:
            continue
        local = [block, *neighbors]
        blocks.extend(local)
        taken.update(_coordinates(local))
        taken.add((title_row, left))
    return blocks


def _section_anchor(by_row, heading, first, right, date1904, taken):
    for n in range(heading - 1, max(0, heading - 4), -1):
        local = [
            (col, text) for col, text in _populated(by_row.get(n, []), date1904)
            if max(1, first - 4) <= col <= right and (n, col) not in taken
        ]
        if not local:
            continue
        if len(local) == 1 and local[0][0] < first and _caption(local[0][1]):
            return n, *local[0]
        break
    return None


def _section_table(sheet, by_row, heading, end, anchor, captions, date1904):
    _, left, title = anchor
    first, right = captions[0][0], captions[-1][0]
    names = dict(captions)
    caption_cols = set(names)
    body = []
    numeric_cols: dict[int, int] = {}
    qualified = 0
    blank = 0
    for n in range(heading + 1, end):
        cells = [cell for cell in by_row.get(n, []) if left <= int(cell["col"]) <= right]
        populated = dict(_populated(cells, date1904))
        label = populated.get(left)
        values = {col for col, value in populated.items() if col > left and _is_number(value)}
        if label and _caption(label):
            if not values:
                break
            qualified += int(len(values & caption_cols) >= 2)
            for col in values:
                numeric_cols[col] = numeric_cols.get(col, 0) + 1
            hidden = any(int(cell["col"]) == left and cell.get("hidden") for cell in cells)
            body.append((n, label, hidden, None))
        elif not label and body:
            totals = _section_totals(sheet, cells, body[0][0], n, caption_cols)
            if totals:
                body.append((n, "Total", False, totals))
                blank = 0
                continue
            blank += 1
            if blank == 2:
                break
            continue
        else:
            break
        blank = 0
    if qualified < 2:
        return None

    # Some amount columns have no caption, immediately before the captioned fields.
    while first - 1 > left and numeric_cols.get(first - 1, 0) >= 2:
        first -= 1
    headers = [
        AxisHeader(col=col, text=names.get(col, index_to_col(col)), period_key=f"c{col}",
                   role=_value_role(names.get(col)))
        for col in range(first, right + 1)
    ]
    rows = [
        LayoutRow(
            row=n, label=label, label_col=left, hidden=hidden, section_path=[title],
            cells=[RowCell(col=h.col, role=h.role, header=names.get(h.col))
                   for h in headers if totals is None or h.col in totals],
        )
        for n, label, hidden, totals in body
    ]
    block_id = f"{sheet}!c{rows[0].row}"
    return Block(block_id=block_id, label_col=left, axis=Axis(
        id=block_id, row=heading, headers=headers
    ), rows=rows, kind="matrix" if len(headers) > 2 else "key_value")


def _section_totals(sheet, cells, first, current, headings):
    if current - first < 2:
        return set()
    totals = set()
    for cell in cells:
        col = int(cell["col"])
        formula = cell.get("formula_raw")
        if col not in headings or not formula:
            continue
        spans = _vertical_aggregate_rows(formula, current, sheet, col).spans
        if spans == ((first, current - 1),):
            totals.add(col)
    return totals


def _section_neighbors(sheet, by_row, block, end, date1904):
    heading = block.axis.row
    right = block.axis.headers[-1].col
    candidates = [
        (col, text) for col, text in _populated(by_row[heading], date1904) if col > right
    ]
    if candidates:
        left, title = candidates[0]
        if left > right + 1 and _caption(title):
            limit = min(candidates[1][0], left + 4) if len(candidates) > 1 else left + 4
            region = _panel_rows(by_row, heading, end, left, limit, date1904)
            neighbors = coverage_blocks(sheet, region, date1904, [], set())[0]
            neighbors = [item for item in neighbors if item.label_col == left]
            for item in neighbors:
                for row in item.rows:
                    row.section_path = [title]
            if neighbors:
                # The heading names the list even if coverage starts on its first value row.
                neighbors[0].axis.row = heading
                return neighbors
        return []

    first = block.rows[0].row
    sibling = [
        (col, text) for col, text in _populated(by_row[first], date1904) if col > right
    ]
    if len(sibling) != 2:
        return []
    (left, label), (col, value) = sibling
    if left <= right + 1 or col - left > 3 or not _caption(label) or not _is_number(value):
        return []
    block_id = f"{sheet}!c{first}"
    header = AxisHeader(col=col, text=index_to_col(col), period_key=f"c{col}", role="value")
    row = LayoutRow(row=first, label=label, label_col=left, cells=[RowCell(col=col, role="value")])
    return [Block(block_id=block_id, label_col=left, kind="key_value",
                  axis=Axis(id=block_id, row=first, headers=[header]), rows=[row])]


def _panels(sheet, by_row, date1904, horizontal_headers, edges, defined_names):
    blocks: list[Block] = []
    taken: set[tuple[int, int]] = set()
    schedules: list[tuple[dict[int, list[dict]], list[tuple[list[int], TimeAxis]]]] = []
    if not by_row:
        return blocks, taken, schedules
    last = max(by_row)
    for start in sorted(by_row):
        populated = _populated(by_row[start], date1904)
        titles = [
            (col, text) for col, text in populated
            if (start, col) not in taken and _caption(text)
        ]
        if not 2 <= len(titles) == len(populated):
            continue
        if any(right[0] - left[0] <= 1 for left, right in pairwise(titles)):
            continue
        next_header = bisect_right(horizontal_headers, start)
        end = horizontal_headers[next_header] if next_header < len(horizontal_headers) else last + 1
        if start in horizontal_headers or end - start < 3:
            continue
        right_edge = max(
            (col for row in range(start + 1, min(end, start + 9))
             for col, _ in _populated(by_row.get(row, []), date1904)),
            default=titles[-1][0],
        ) + 1
        right_edges = [col for col, _ in titles[1:]] + [right_edge]
        candidates = []
        for (left, title), right in zip(titles, right_edges, strict=True):
            region = _panel_rows(by_row, start, end, left, right, date1904)
            if not region:
                continue
            block = detect_params_block(sheet, region, date1904, edges, defined_names)
            local = [block] if block is not None else coverage_blocks(
                sheet, region, date1904, [], set()
            )[0]
            local = [item for item in local if item.label_col == left and item.axis.headers]
            if local:
                if block is None:
                    _keep_panel_scalars(sheet, region, local, left, date1904)
                candidates.append((title, left, right, max(region), local))
        if len(candidates) < 2:
            continue
        for title, left, right, last_row, local in candidates:
            for block in local:
                for row in block.rows:
                    if row.kind == "fact" and not row.section_path:
                        row.section_path = [title]
            blocks.extend(local)
            taken.update(_coordinates(local))
            taken.add((start, left))
            for region, axes in _panel_schedules(
                sheet, by_row, last_row + 1, end, left, right, date1904
            ):
                schedules.append((region, axes))
                taken.update((row, int(cell["col"])) for row, cells in region.items()
                             for cell in cells)
    return blocks, taken, schedules


def _panel_rows(by_row, start, end, left, right, date1904):
    region = {}
    named = 0
    numeric = 0
    value_cols: set[int] = set()
    blank = 0
    for n in range(start, end):
        cells = [cell for cell in by_row.get(n, []) if left <= int(cell["col"]) < right]
        populated = _populated(cells, date1904)
        if not populated:
            blank += 1
            if named and blank >= 2:
                break
            continue
        blank = 0
        label = next((text for col, text in populated if col == left), None)
        values = [text for col, text in populated if col != left]
        if n > start:
            if label is not None and not _caption(label):
                break
            amounts = {col for col, text in populated if col != left and _is_number(text)}
            if numeric >= 2 and label and amounts and not amounts & value_cols:
                break
            if numeric >= 2 and label and values and all(_caption(text) for text in values):
                break
            named += int(bool(label and values))
            if label and amounts:
                numeric += 1
                value_cols.update(amounts)
        region[n] = cells
        if n - start >= 8 and numeric < 2:
            return {}
    return region if named >= 2 and numeric >= 2 else {}


def _keep_panel_scalars(sheet, region, blocks, left, date1904):
    """A confirmed panel can contain a date or a text selector in a value cell."""
    scalar_blocks = {}
    for block in blocks[:]:
        if len(block.axis.headers) != 1:
            continue
        [header] = block.axis.headers
        # A date mistaken for a header is a scalar within this panel.
        if not _caption(header.text):
            header.text = index_to_col(header.col)
            block.axis.row = block.rows[0].row
        existing = scalar_blocks.get(header.col)
        if existing and existing.axis.headers[0].text == header.text:
            existing.rows.extend(block.rows)
            blocks.remove(block)
        else:
            scalar_blocks[header.col] = block
    occupied_rows = {row.row for block in blocks for row in block.rows}
    headings = {block.axis.row for block in blocks}
    for n, cells in region.items():
        if n in occupied_rows or n in headings:
            continue
        populated = _populated(cells, date1904)
        if len(populated) != 2 or populated[0][0] != left:
            continue
        (_, label), (col, value) = populated
        if not _caption(label) or len(value) > 24:
            continue
        row = LayoutRow(
            row=n, label=label, label_col=left, cells=[RowCell(col=col, role="value")],
            hidden=any(int(cell["col"]) == left and cell.get("hidden") for cell in cells),
        )
        if col in scalar_blocks:
            scalar_blocks[col].rows.append(row)
            continue
        block_id = f"{sheet}!c{n}"
        header = AxisHeader(col=col, text=index_to_col(col), period_key=f"c{col}", role="value")
        block = Block(
            block_id=block_id, label_col=left, kind="key_value",
            axis=Axis(id=block_id, row=n, headers=[header]), rows=[row],
        )
        scalar_blocks[col] = block
        blocks.append(block)
    for block in scalar_blocks.values():
        block.rows.sort(key=lambda row: row.row)


def _panel_schedules(sheet, by_row, start, end, left, right, date1904):
    """A Year / Amount heading below a panel starts its own vertical ruler."""
    for n in range(start, min(start + 3, end)):
        captions = [
            (col, text) for col, text in _populated(by_row.get(n, []), date1904)
            if left <= col < right
        ]
        for (year_col, year), (value_col, caption) in pairwise(captions):
            if year.casefold() not in {"year", "год"} or value_col != year_col + 1:
                continue
            if not _caption(caption):
                continue
            region = {
                row: [
                    cell for cell in by_row.get(row, [])
                    if int(cell["col"]) in {year_col, value_col}
                ]
                for row in range(n, end)
            }
            found, _notes = _vertical_axes(sheet, region, date1904, [])
            for rows, axis in found:
                if rows[0] == n + 1 and axis.periods[0].col == year_col:
                    yield {row: region[row] for row in [n, *rows]}, [(rows, axis)]


def _stacked_tables(sheet, by_row, date1904):
    """Two explicit column headings on one label column start separate local tables."""
    headed: list[Block] = []
    for n in sorted(by_row):
        captions = _populated(by_row[n], date1904)
        if len(captions) < 3 or not all(_caption(text) for _, text in captions):
            continue
        left, title = captions[0]
        right = captions[-1][0]
        region = {n: by_row[n]}
        row = n + 1
        while cells := by_row.get(row):
            scoped = [cell for cell in cells if left <= int(cell["col"]) <= right]
            populated = _populated(scoped, date1904)
            if not populated or populated[0][0] != left or not _caption(populated[0][1]):
                break
            if sum(_is_number(text) for _, text in populated[1:]) < 2:
                break
            region[row] = scoped
            row += 1
        if len(region) < 3:
            continue
        candidates = coverage_blocks(sheet, region, date1904, [], set())[0]
        for block in candidates:
            if block.label_col == left and len(block.rows) >= 2:
                for data in block.rows:
                    data.section_path = [title]
                headed.append(block)
    return [
        block for block in headed
        if any(
            other.label_col == block.label_col and other.axis.row != block.axis.row
            and [h.text for h in other.axis.headers] != [h.text for h in block.axis.headers]
            for other in headed
        )
    ]


def _side_lists(sheet, by_row, date1904, hidden_cols):
    """Reserve right-hand lists so a left-hand params block cannot absorb them."""
    candidates = coverage_blocks(sheet, by_row, date1904, [], set(), hidden_cols)[0]
    return [
        right for right in candidates
        if any(
            left.axis.headers[-1].col < right.label_col
            and left.rows[0].row <= right.rows[-1].row
            and right.rows[0].row <= left.rows[-1].row
            for left in candidates
        )
    ]


def _coordinates(blocks):
    taken = set()
    for block in blocks:
        taken.add((block.axis.row, block.label_col))
        taken.update((block.axis.row, header.col) for header in block.axis.headers)
        for row in block.rows:
            taken.add((row.row, row.label_col or block.label_col))
            taken.update((row.row, cell.col) for cell in row.cells)
    return taken


def _caption(text):
    return not _is_number(text) and not is_calendar_hit(classify_header(text))


def _populated(cells, date1904):
    return sorted(
        (int(cell["col"]), text.strip()) for cell in cells
        if (text := _text(cell, date1904))
    )
