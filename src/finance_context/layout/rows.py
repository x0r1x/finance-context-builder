from __future__ import annotations

import re
from collections import defaultdict

from finance_context.layout.cells import (
    _cell_at,
    _is_int_text,
    _is_number,
    _text,
    parse_int,
    parse_number,
)
from finance_context.layout.models import LayoutRow
from finance_context.layout.params import attach_stub_cells
from finance_context.layout.periods import (
    _YEAR_KEY,
    LAYER_BOUNDS,
    LAYER_DATE,
    LAYER_HALF,
    LAYER_MONTH,
    LAYER_QUARTER,
    LAYER_YEAR,
    classify_atom,
    classify_header,
    is_calendar_hit,
    is_end_period_label,
    is_month_label,
    is_quarter_label,
    is_start_period_label,
    normalize_header,
)
from finance_context.layout.units import is_unit_text

# `должен` is a whole word: the bare stem also hits `задолженность`.
# `проверк` is a substring (`Перепроверка` has no such collision).
# `сход` allows an optional `не`/`ра` prefix, so `Несходимость` matches
# and `Исходящее` does not. `контроль` stays at a word start.
_CHECK = re.compile(
    r"check|tie[- ]?out|plug\b|проверк|"
    r"\bконтроль|\b(?:не|ра)?сход[ия]|\bдолж(?:ен|на|но|ны)\b",
    re.IGNORECASE,
)


_INDEX_LABEL = re.compile(r"^(№|n|no|#)$", re.IGNORECASE)


_COUNTER_LABEL = re.compile(
    r"week\b|#|№|period\s*index|\bindex\b|счётчик|счетчик|номер",
    re.IGNORECASE,
)


_FLAG_BODY = re.compile(
    r"\b(flag|flags|toggle|switch|binary)\b|start date|end date|"
    r"beginning of construction|end of construction",
    re.IGNORECASE,
)


_SCENARIO_LABEL = re.compile(
    r"live case|case number|\bchoice\b|\bmid case\b|\blow case\b|"
    r"applied \(real terms\)|\breal terms\b|covenant breach",
    re.IGNORECASE,
)


_PLACEHOLDER_LABEL = re.compile(r"^(spare|none)$", re.IGNORECASE)
# A dash typed in place of a value: `-`, `–`, `—`.
_DASH = re.compile(r"\s*[-\u2013\u2014]\s*")


_RELATIVE_LABEL = re.compile(
    r"^(project\s+)?(year|period|month|quarter|год|период|мес\w*|кв\w*)s?\b",
    re.IGNORECASE,
)


def _row_layers(row_cells: list[dict], date1904: bool) -> set[str]:
    layers: set[str] = set()
    label = _row_label(row_cells, date1904)
    allow_q = is_quarter_label(label)
    allow_m = is_month_label(label)
    if is_start_period_label(label) or is_end_period_label(label):
        layers.add(LAYER_BOUNDS)
    atoms = [
        classify_atom(
            _text(cell, date1904),
            allow_quarter_num=allow_q,
            allow_month_num=allow_m,
        )
        for cell in row_cells
    ]
    atoms = [atom for atom in atoms if atom is not None and atom.kind != "noise"]
    if sum(1 for atom in atoms if atom.kind == "full" and atom.day) >= 1:
        layers.add(LAYER_DATE)
    if _year_layer(row_cells, date1904):
        layers.add(LAYER_YEAR)
    if sum(1 for atom in atoms if atom.quarter) >= 2:
        layers.add(LAYER_QUARTER)
    if sum(1 for atom in atoms if atom.half) >= 2:
        layers.add(LAYER_HALF)
    if sum(1 for atom in atoms if atom.month and atom.day is None) >= 2:
        layers.add(LAYER_MONTH)
    return layers


def _is_starter_row(row_cells: list[dict], date1904: bool) -> bool:
    calendar = 0
    bare_only = True
    quarters = 0
    for cell in row_cells:
        text = _text(cell, date1904)
        atom = classify_atom(text)
        if atom is not None and atom.kind == "quarter_token":
            quarters += 1
        if not is_calendar_hit(classify_header(text)):
            continue
        calendar += 1
        if not _is_bare_year_text(text):
            bare_only = False
    # A row of "4Q" / "1 кв." is a header even when no year sits in the same cells.
    if calendar < 2:
        return quarters >= 2
    # Dates, quarters and "2025E" are headers. Bare years are a header only when
    # the visible run is non-decreasing, with one positive step if it advances.
    if not bare_only:
        return True
    return _bare_years_are_header(row_cells, date1904)


def _is_quarter_token_row(row_cells: list[dict], date1904: bool) -> bool:
    """True when the row is only quarter tokens such as ``4Q`` or ``1 кв.``."""
    quarters = 0
    for cell in row_cells:
        text = _text(cell, date1904)
        if is_calendar_hit(classify_header(text)):
            return False
        atom = classify_atom(text)
        if atom is not None and atom.kind == "quarter_token":
            quarters += 1
    return quarters >= 2


def _is_year_banner_row(row_cells: list[dict], date1904: bool) -> bool:
    """A year above a quarter row. Other numbers make it a data row."""
    years = 0
    for cell in row_cells:
        text = _text(cell, date1904)
        if not text:
            continue
        if _is_bare_year_text(text):
            years += 1
            continue
        if _is_number(text):
            return False
    return years >= 1


def _is_banner_gap(row_cells: list[dict], period_cols: set[int], date1904: bool) -> bool:
    """A text banner in the period columns, with no date and no values.

    ``Факт`` / ``План`` above a date ruler used to be a marker layer inside the
    header band. It is not a period row. Label-only titles above it still belong
    to the table, so the walk through section titles steps over this gap.
    """
    if not period_cols or _row_layers(row_cells, date1904) or _is_data_row(row_cells, date1904):
        return False
    saw = False
    for cell in row_cells:
        if cell.get("formula_raw"):
            return False
        text = _text(cell, date1904)
        if not text:
            continue
        if _is_number(text):
            return False
        if int(cell["col"]) in period_cols:
            if _is_column_header_text(text):
                return False
            saw = True
    return saw


def _is_data_row(row_cells: list[dict], date1904: bool) -> bool:
    if _is_starter_row(row_cells, date1904):
        return False
    if _row_layers(row_cells, date1904):
        return False
    if _is_counter_row(row_cells, date1904):
        return False
    has_label = False
    has_number = False
    for cell in row_cells:
        text = _text(cell, date1904)
        if not text:
            continue
        if classify_header(text) is not None:
            continue
        if _is_number(text):
            has_number = True
        else:
            has_label = True
    return has_label and has_number


def _is_counter_row(row_cells: list[dict], date1904: bool) -> bool:
    label = _row_label(row_cells, date1904)
    if label and not _INDEX_LABEL.fullmatch(label.strip()):
        return False
    nums: list[int] = []
    for cell in sorted(row_cells, key=lambda c: int(c["col"])):
        text = _text(cell, date1904)
        if not text:
            continue
        if classify_header(text) is not None:
            continue
        number = parse_int(text)
        if number is None:
            if parse_number(text) is not None:
                return False
            continue
        nums.append(number)
    return len(nums) >= 2 and nums == list(range(1, len(nums) + 1))


def _is_bare_year_text(text: str | None) -> bool:
    """An integer in the year range, not ``2025E`` or a date."""
    if not text or not _is_int_text(text):
        return False
    hit = classify_header(text)
    return (
        hit is not None
        and bool(_YEAR_KEY.fullmatch(hit.period_key))
        and not hit.explicit_role
    )


def _bare_year_values(row_cells: list[dict], date1904: bool) -> list[int]:
    """Bare years on the row. A non-year number means this is not a year ruler.

    A hidden cell on a row that also has a visible cell is a hidden column, so
    scratch years there stay out of the run. A row whose every cell is hidden
    is a hidden year banner (the columns are not hidden) and those years count.
    """
    row_hidden = bool(row_cells) and all(cell.get("hidden") for cell in row_cells)
    values: list[int] = []
    for cell in sorted(row_cells, key=lambda item: int(item["col"])):
        if cell.get("hidden") and not row_hidden:
            continue
        text = _text(cell, date1904)
        if text is None:
            continue
        if _is_bare_year_text(text):
            year = parse_int(text)
            if year is not None:
                values.append(year)
            continue
        if _is_number(text):
            return []
    return values


def _year_run_is_header(values: list[int]) -> bool:
    """Non-decreasing years. A positive step, when one exists, stays constant.

    ``2022, 2022, 2023, 2023`` is a banner. ``2016, 2017, 2018`` is a ruler.
    ``2074, 2035, 1988`` is a data row.
    """
    if len(values) < 2:
        return False
    steps = [right - left for left, right in zip(values, values[1:], strict=False)]
    if any(step < 0 for step in steps):
        return False
    positive = {step for step in steps if step > 0}
    return len(positive) <= 1


def _bare_years_are_header(row_cells: list[dict], date1904: bool) -> bool:
    return _year_run_is_header(_bare_year_values(row_cells, date1904))


def _year_banner_by_column(row_cells: list[dict], date1904: bool) -> dict[int, str]:
    """Column → year for a real year banner, including a fully hidden row."""
    if not _bare_years_are_header(row_cells, date1904):
        return {}
    row_hidden = bool(row_cells) and all(cell.get("hidden") for cell in row_cells)
    own: list[tuple[int, str, int | None]] = []
    for cell in row_cells:
        if cell.get("hidden") and not row_hidden:
            continue
        text = _text(cell, date1904)
        if not text or not _is_bare_year_text(text):
            continue
        year = parse_int(text)
        if year is None:
            continue
        end = cell.get("merge_end_col")
        try:
            end_col = int(end) if end is not None else None
        except (TypeError, ValueError):
            end_col = None
        own.append((int(cell["col"]), str(year), end_col))
    own.sort()
    found: dict[int, str] = {}
    for index, (col, year, end_col) in enumerate(own):
        found[col] = year
        if end_col is None or end_col <= col:
            continue
        nxt = own[index + 1][0] if index + 1 < len(own) else end_col + 1
        stop = min(end_col, nxt - 1)
        for covered in range(col + 1, stop + 1):
            found.setdefault(covered, year)
    return found


def _word_residual(text: str | None) -> bool:
    """A content word beside one date, as opposed to a one-letter suffix.

    ``2024E`` is still a year header. ``Forecast 2024`` in a label cell is not
    a header layer by itself.
    """
    hit = classify_header(text)
    if hit is None or not hit.variant:
        return False
    return len(hit.variant.strip()) > 1


def _year_layer(row_cells: list[dict], date1904: bool) -> bool:
    """Text year headers count. Bare numbers count only as a real year run.

    One word glued to a year (``Forecast 2024``) does not make the row a year
    layer: that caption sits in a label cell beside numeric data. A run of two
    such cells is a header. A suffix letter (``2024E``) still counts as one cell.
    """
    letters = 0
    words = 0
    for cell in row_cells:
        text = _text(cell, date1904)
        hit = classify_header(text)
        if hit is None or not _YEAR_KEY.fullmatch(hit.period_key):
            continue
        if _is_bare_year_text(text):
            continue
        if _word_residual(text):
            words += 1
        else:
            letters += 1
    if letters:
        return True
    if words >= 2:
        return True
    return _bare_years_are_header(row_cells, date1904)


def _is_empty_row(row_cells: list[dict], date1904: bool) -> bool:
    return all(_text(cell, date1904) is None for cell in row_cells)


def _row_label(row_cells: list[dict], date1904: bool) -> str | None:
    for cell in sorted(row_cells, key=lambda c: int(c["col"])):
        if cell.get("hidden"):
            continue
        text = _text(cell, date1904)
        if not text or _is_number(text):
            continue
        atom = classify_atom(text)
        if atom is not None and atom.kind != "marker":
            continue
        return text
    return None


def _label_span(
    by_row: dict[int, list[dict]],
    body_rows: list[int],
    header_rows: set[int],
    period_cols: set[int],
    date1904: bool,
    *,
    col_floor: int = 0,
) -> tuple[int, list[int]]:
    left_edge = min(period_cols) if period_cols else 10**6
    leftmost: dict[int, int] = defaultdict(int)
    for row_n in body_rows:
        if row_n in header_rows:
            continue
        for cell in sorted(by_row[row_n], key=lambda item: int(item["col"])):
            if cell.get("hidden"):
                continue
            col = int(cell["col"])
            if col <= col_floor or col >= left_edge or col in period_cols:
                continue
            text = _text(cell, date1904)
            if not text or _is_number(text) or _is_column_header_text(text):
                continue
            leftmost[col] += 1
            break
    if not leftmost:
        visible = [
            int(cell["col"])
            for row_n in body_rows
            for cell in by_row[row_n]
            if not cell.get("hidden")
        ]
        fallback = min(visible) if visible else 1
        return fallback, [fallback]
    primary = max(leftmost.items(), key=lambda item: (item[1], -item[0]))[0]
    span = sorted(col for col in leftmost if col < primary)
    span.append(primary)
    return primary, span


def _data_rows(
    by_row: dict[int, list[dict]],
    band_rows: list[int],
    header_rows: set[int],
    label_span: list[int],
    date1904: bool,
    period_cols: set[int],
    seed: LayoutRow | None = None,
    reveal_rows: set[int] | None = None,
    summary_below: bool = True,
    hidden_row_ids: set[int] | None = None,
) -> list[LayoutRow]:
    revealed = reveal_rows or set()
    out: list[LayoutRow] = []
    # Non-unit rows, indents strictly increasing. The nearest shallower row is
    # the top of this stack, so a flat table does not rescan every earlier row.
    parent_stack: list[LayoutRow] = []
    outlines: list[int] = []
    section_stack: list[LayoutRow] = [seed] if seed is not None else []
    for row_n in band_rows:
        if row_n in header_rows:
            continue
        if _is_empty_row(by_row[row_n], date1904):
            continue
        if _is_counter_row(by_row[row_n], date1904):
            continue
        reveal = row_n in revealed or _row_all_hidden(
            row_n, by_row[row_n], hidden_row_ids
        )
        label, depth, label_cell = _row_span_label(
            by_row[row_n], label_span, date1904, include_hidden=reveal
        )
        if label is None or label_cell is None:
            label, depth, label_cell = _left_of_axis_label(
                by_row[row_n], period_cols, date1904, include_hidden=reveal
            )
        if label is None or label_cell is None:
            continue
        indent = depth + _indent(label) + _style_indent(label_cell)
        outline = _outline_level(label_cell)
        check_row = bool(_CHECK.search(label))
        kind = _row_kind(
            by_row[row_n],
            period_cols,
            date1904,
            label=label,
            check_row=check_row,
        )
        probe = attach_stub_cells(
            LayoutRow(row=row_n, label=label),
            by_row[row_n],
            label_span=label_span,
            period_cols=period_cols,
            date1904=date1904,
        )
        if kind == "abstract" and (
            _has_scalar_value(probe, by_row[row_n], date1904)
            or _is_blank_data_row(by_row[row_n], label_cell, period_cols, date1904)
        ):
            in_check = any(_CHECK.search(item.label) for item in section_stack) or any(
                prev.check_row for prev in _ancestors(out, indent)
            )
            kind = "helper" if in_check else "fact"
        unit_caption = is_unit_text(label)
        if unit_caption:
            pass
        elif kind == "abstract":
            while section_stack and section_stack[-1].indent >= indent:
                section_stack.pop()
        else:
            while section_stack and section_stack[-1].indent > indent:
                section_stack.pop()
        section_path = [item.label for item in section_stack]
        parent_row = _indent_parent(parent_stack, indent)
        # A fact in the same column as its section header has no shallower indent.
        # section_path already kept that header; parent_row should name it too.
        if parent_row is None and section_stack and kind != "abstract":
            parent_row = section_stack[-1].row
        # `Total` shares the section header's indent, so the outline parent is the
        # statement (`Balance Sheet`) and gold cannot tell Current from Non-current.
        if (
            section_stack
            and label.strip().casefold()
            in {"total", "subtotal", "sub total", "sum", "итого", "всего"}
            and (parent_row is None or parent_row in {item.row for item in section_stack})
        ):
            parent_row = section_stack[-1].row
        item = LayoutRow(
            row=row_n,
            label=label.strip(),
            parent_row=parent_row,
            indent=indent,
            check_row=check_row,
            hidden=bool(label_cell.get("hidden")),
            kind=kind,
            section_path=section_path,
            label_col=int(label_cell["col"]),
        )
        attach_stub_cells(
            item,
            by_row[row_n],
            label_span=label_span,
            period_cols=period_cols,
            date1904=date1904,
            by_row=by_row,
            floor_row=band_rows[0] if band_rows else None,
        )
        out.append(item)
        outlines.append(outline)
        if not unit_caption:
            while parent_stack and parent_stack[-1].indent >= indent:
                parent_stack.pop()
            parent_stack.append(item)
        if kind == "abstract" and not is_unit_text(label):
            section_stack.append(item)
    if any(level > 0 for level in outlines):
        _apply_outline_parents(out, outlines, summary_below)
    return out


def _indent_parent(stack: list[LayoutRow], indent: int) -> int | None:
    """Nearest earlier non-unit row whose indent is strictly smaller.

    ``stack`` holds those rows in sheet order and its indents rise.
    """
    for prev in reversed(stack):
        if prev.indent < indent:
            return prev.row
    return None


def _style_indent(cell: dict) -> int:
    return _nonneg_int(cell.get("style_indent"))


def _outline_level(cell: dict) -> int:
    return _nonneg_int(cell.get("outline_level"))


def _nonneg_int(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _apply_outline_parents(
    rows: list[LayoutRow], levels: list[int], summary_below: bool
) -> None:
    """A nested outline level hangs off the summary row inside the group.

    Level 1 under a level 0 section is the collapsible band indent already
    models, so that parent stays. Level 2 under level 1 (IaaS under CAPEX)
    replaces the indent parent when the summary sits closer.
    ``summaryBelow`` false looks upward; true looks downward.
    """
    unit = [is_unit_text(row.label) for row in rows]
    parent_at = _outline_parent_at(levels, unit, summary_below)
    for index, (row, level) in enumerate(zip(rows, levels, strict=True)):
        if level <= 1 or unit[index]:
            continue
        parent_index = parent_at[index]
        if parent_index is None:
            continue
        parent = rows[parent_index]
        parent_level = levels[parent_index]
        if parent_level <= 0:
            continue
        if row.parent_row is None:
            row.parent_row = parent.row
            continue
        closer_above = not summary_below and parent.row > row.parent_row
        closer_below = summary_below and parent.row < row.parent_row
        if closer_above or closer_below:
            row.parent_row = parent.row


def _outline_parent_at(
    levels: list[int], unit: list[bool], summary_below: bool
) -> list[int | None]:
    """Nearest row in the summary direction with a strictly smaller level.

    Unit-text rows are not summaries. One stack pass: each row is pushed and
    popped at most once. ``summary_below`` walks upward from the bottom.
    """
    count = len(levels)
    found: list[int | None] = [None] * count
    stack: list[int] = []
    indexes = range(count - 1, -1, -1) if summary_below else range(count)
    for index in indexes:
        if unit[index]:
            continue
        level = levels[index]
        while stack and levels[stack[-1]] >= level:
            stack.pop()
        if stack:
            found[index] = stack[-1]
        stack.append(index)
    return found


def _row_all_hidden(
    row_n: int, row_cells: list[dict], hidden_row_ids: set[int] | None
) -> bool:
    """A row is hidden when the workbook lists it.

    Cell flags alone are not enough: a visible row can hold text only in a
    hidden column, and every cell is then flagged hidden. Callers that have no
    workbook metadata still fall back to those flags.
    """
    if hidden_row_ids is not None:
        return row_n in hidden_row_ids
    return bool(row_cells) and all(cell.get("hidden") for cell in row_cells)


def _ancestors(rows: list[LayoutRow], indent: int) -> list[LayoutRow]:
    """Nearest preceding row at each shallower indent: the row's outline parents."""
    found: list[LayoutRow] = []
    level = indent
    for prev in reversed(rows):
        if prev.indent < level:
            found.append(prev)
            level = prev.indent
            if level == 0:
                break
    return found


def _band_title(
    by_row: dict[int, list[dict]],
    header_row: int,
    label_span: list[int],
    period_cols: set[int],
    date1904: bool,
) -> LayoutRow | None:
    """A section title that carries computed year headers (`=YEAR(M7)`) opens the section."""
    cells = by_row.get(header_row, [])
    if not any(int(cell["col"]) in period_cols and cell.get("formula_raw") for cell in cells):
        return None
    label, depth, cell = _row_span_label(cells, label_span, date1904)
    if label is None or cell is None:
        return None
    if is_start_period_label(label) or is_end_period_label(label):
        return None
    if _RELATIVE_LABEL.match(normalize_header(label) or ""):
        return None
    return LayoutRow(
        row=header_row,
        label=label.strip(),
        kind="abstract",
        indent=depth + _indent(label),
        label_col=int(cell["col"]),
    )


def _has_scalar_value(probe: LayoutRow, row_cells: list[dict], date1904: bool) -> bool:
    """A number left of the ruler (`G563 = XIRR(...)`) is a point fact, not a section title."""
    for item in probe.cells:
        if item.role != "value":
            continue
        text = _text(_cell_at(row_cells, item.col) or {}, date1904)
        if text and _is_number(text):
            return True
    return False


def _is_blank_data_row(
    row_cells: list[dict], label_cell: dict, period_cols: set[int], date1904: bool
) -> bool:
    """A data line with nothing to show, not a section title.

    A formula beside the label (`=IF(ISERROR(E9),"",E9)` returning blank) or a dash
    placeholder (`-`) in a period column marks a list item whose values are empty.
    A section title carries only its label, or formulas that print period captions.
    """
    label_col = int(label_cell["col"])
    for cell in row_cells:
        col = int(cell["col"])
        if col == label_col:
            continue
        text = _text(cell, date1904) or ""
        if cell.get("formula_raw") and classify_atom(text) is None:
            return True
        if col in period_cols and _DASH.fullmatch(text):
            return True
    return False


def _row_span_label(
    row_cells: list[dict],
    label_span: list[int],
    date1904: bool,
    *,
    include_hidden: bool = False,
) -> tuple[str | None, int, dict | None]:
    for depth, col in enumerate(label_span):
        cell = _cell_at(row_cells, col)
        if cell is None or (cell.get("hidden") and not include_hidden):
            continue
        text = _text(cell, date1904)
        if not text or _is_number(text) or _is_column_header_text(text):
            continue
        return text, depth, cell
    return None, 0, None


def _left_of_axis_label(
    row_cells: list[dict],
    period_cols: set[int],
    date1904: bool,
    *,
    include_hidden: bool = False,
) -> tuple[str | None, int, dict | None]:
    """First text left of the period axis when the label span is empty."""
    if not period_cols:
        return None, 0, None
    left_edge = min(period_cols)
    for cell in sorted(row_cells, key=lambda item: int(item["col"])):
        if cell.get("hidden") and not include_hidden:
            continue
        if int(cell["col"]) >= left_edge:
            break
        text = _text(cell, date1904)
        if not text or _is_number(text) or _is_column_header_text(text):
            continue
        return text, 0, cell
    return None, 0, None


def _is_column_header_text(text: str) -> bool:
    """A period in the label column ("2024", "I кв. 2025") is not a row name.

    "План", "Факт", "Budget" or "Base case" there is a row label. So is a
    content word beside one date (``Revenue 2024``): the position says the cell
    names the row. A one-letter suffix (``2024E``) stays a period header.
    """
    hit = classify_header(text)
    if not is_calendar_hit(hit):
        return False
    return not _word_residual(text)


def _row_kind(
    row_cells: list[dict],
    period_cols: set[int],
    date1904: bool,
    *,
    label: str,
    check_row: bool,
) -> str:
    if check_row or _PLACEHOLDER_LABEL.fullmatch(label.strip()):
        return "helper"
    if _is_index_values(row_cells, period_cols, date1904):
        return "index"
    has_formula = False
    has_number = False
    has_text = False
    binary_vals: set[int] = set()
    binary_ok = True
    binary_n = 0
    for cell in row_cells:
        if int(cell["col"]) not in period_cols:
            continue
        if cell.get("formula_raw"):
            has_formula = True
        text = _text(cell, date1904)
        if text and not _is_number(text):
            has_text = True
        if text and _is_number(text):
            has_number = True
            if binary_ok:
                value = parse_number(text)
                if value is None or value not in {0.0, 1.0}:
                    binary_ok = False
                else:
                    binary_vals.add(int(value))
                    binary_n += 1
    flag_label = bool(_SCENARIO_LABEL.search(label) or _FLAG_BODY.search(label))
    if not has_formula and not has_number:
        return "flag" if flag_label and has_text else "abstract"
    if flag_label and (binary_ok or not has_number):
        return "flag"
    if binary_ok and binary_n >= 2 and binary_vals == {0, 1}:
        return "flag"
    return "fact"


def _is_index_values(
    row_cells: list[dict],
    period_cols: set[int],
    date1904: bool,
) -> bool:
    nums: list[int] = []
    has_formula = False
    for cell in sorted(row_cells, key=lambda c: int(c["col"])):
        if int(cell["col"]) not in period_cols:
            continue
        if cell.get("formula_raw") or cell.get("formula_template"):
            has_formula = True
        text = _text(cell, date1904)
        if not text:
            continue
        value = parse_int(text)
        if value is None:
            if parse_number(text) is not None:
                return False
            continue
        if abs(value) > 53:
            return False
        nums.append(value)
    if len(nums) < 2:
        return False
    start = nums[0]
    sequential = start in {0, 1} and nums == list(range(start, start + len(nums)))
    if not sequential:
        return False
    label = _row_label(row_cells, date1904) or ""
    if _COUNTER_LABEL.search(label) or _INDEX_LABEL.fullmatch(label.strip()):
        return True
    return not has_formula


def _indent(label: str) -> int:
    stripped = label.lstrip(" \t")
    return len(label) - len(stripped)
