from __future__ import annotations

import re
from collections import defaultdict

from finance_context.layout.cells import _cell_at, _is_int_text, _is_number, _text
from finance_context.layout.models import LayoutRow
from finance_context.layout.params import attach_stub_cells
from finance_context.layout.periods import (
    LAYER_BOUNDS,
    LAYER_DATE,
    LAYER_MONTH,
    LAYER_QUARTER,
    LAYER_ROLE,
    LAYER_YEAR,
    classify_atom,
    classify_header,
    is_calendar_hit,
    is_end_period_label,
    is_month_label,
    is_quarter_label,
    is_role_marker_text,
    is_start_period_label,
    normalize_header,
)
from finance_context.layout.units import is_unit_text

_CHECK = re.compile(
    r"check|проверк|контроль|tie[- ]?out|plug\b|сход[ия]|должен",
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
    if any(atom.kind == "role_marker" for atom in atoms) or is_role_marker_text(label):
        layers.add(LAYER_ROLE)
    if sum(1 for atom in atoms if atom.kind == "full" and atom.day) >= 1:
        layers.add(LAYER_DATE)
    years = sum(
        1
        for atom in atoms
        if atom.year and atom.quarter is None and atom.month is None and atom.day is None
    )
    if years >= 1:
        layers.add(LAYER_YEAR)
    if sum(1 for atom in atoms if atom.quarter) >= 2:
        layers.add(LAYER_QUARTER)
    if sum(1 for atom in atoms if atom.month and atom.day is None) >= 2:
        layers.add(LAYER_MONTH)
    return layers


def _is_starter_row(row_cells: list[dict], date1904: bool) -> bool:
    return (
        sum(1 for cell in row_cells if is_calendar_hit(classify_header(_text(cell, date1904))))
        >= 2
    )


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
        if classify_header(text) is not None or is_role_marker_text(text):
            continue
        if not _is_int_text(text):
            if _is_number(text):
                return False
            continue
        nums.append(int(float(text.replace(",", "."))))
    return len(nums) >= 2 and nums == list(range(1, len(nums) + 1))


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
        if atom is not None and atom.kind != "role_marker":
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
) -> list[LayoutRow]:
    out: list[LayoutRow] = []
    section_stack: list[LayoutRow] = [seed] if seed is not None else []
    for row_n in band_rows:
        if row_n in header_rows:
            continue
        if _is_empty_row(by_row[row_n], date1904):
            continue
        if _is_counter_row(by_row[row_n], date1904):
            continue
        label, depth, label_cell = _row_span_label(by_row[row_n], label_span, date1904)
        if label is None or label_cell is None:
            label, depth, label_cell = _left_of_axis_label(
                by_row[row_n], period_cols, date1904
            )
        if label is None or label_cell is None:
            continue
        indent = depth + _indent(label)
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
        if kind == "abstract" and _has_scalar_value(probe, by_row[row_n], date1904):
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
        parent_row = None
        for prev in reversed(out):
            if is_unit_text(prev.label):
                continue
            if prev.indent < indent:
                parent_row = prev.row
                break
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
        if kind == "abstract" and not is_unit_text(label):
            section_stack.append(item)
    return out


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


def _row_span_label(
    row_cells: list[dict],
    label_span: list[int],
    date1904: bool,
) -> tuple[str | None, int, dict | None]:
    for depth, col in enumerate(label_span):
        cell = _cell_at(row_cells, col)
        if cell is None or cell.get("hidden"):
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
) -> tuple[str | None, int, dict | None]:
    """First text left of the period axis when the label span is empty."""
    if not period_cols:
        return None, 0, None
    left_edge = min(period_cols)
    for cell in sorted(row_cells, key=lambda item: int(item["col"])):
        if cell.get("hidden"):
            continue
        if int(cell["col"]) >= left_edge:
            break
        text = _text(cell, date1904)
        if not text or _is_number(text) or _is_column_header_text(text):
            continue
        return text, 0, cell
    return None, 0, None


def _is_column_header_text(text: str) -> bool:
    """Year, scenario, and role headers are not row names. Total/Sum/Итого is."""
    hit = classify_header(text)
    return hit is not None and hit.period_key != "total"


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
                try:
                    value = float(text.replace(" ", "").replace(",", "."))
                except ValueError:
                    binary_ok = False
                else:
                    if value not in {0.0, 1.0}:
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
        if not _is_int_text(text):
            if _is_number(text):
                return False
            continue
        value = int(float(text.replace(",", ".")))
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
