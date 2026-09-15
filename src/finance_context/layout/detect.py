from __future__ import annotations

import re
from collections import defaultdict

from finance_context.layout.models import (
    Axis,
    AxisHeader,
    Block,
    Layout,
    LayoutRow,
    SheetLayout,
)
from finance_context.layout.periods import (
    LAYER_BOUNDS,
    LAYER_DATE,
    LAYER_MONTH,
    LAYER_QUARTER,
    LAYER_ROLE,
    LAYER_YEAR,
    HeaderAtom,
    apply_grain,
    classify_atom,
    classify_header,
    compose_period,
    display_cell_text,
    infer_grain,
    is_calendar_hit,
    is_end_period_label,
    is_month_label,
    is_quarter_label,
    is_role_marker_text,
    is_start_period_label,
)

_CHECK = re.compile(
    r"check|проверк|контроль|tie[- ]?out|plug\b|сход[ия]|должен",
    re.IGNORECASE,
)
_INDEX_LABEL = re.compile(r"^(№|n|no|#)$", re.IGNORECASE)
_CALENDAR_LAYERS = frozenset({LAYER_DATE, LAYER_YEAR, LAYER_QUARTER, LAYER_MONTH})



def detect_layout(cells: list[dict], *, date1904: bool = False) -> Layout:
    by_sheet: dict[str, list[dict]] = {}
    for cell in cells:
        by_sheet.setdefault(cell["sheet"], []).append(cell)
    sheets = [
        SheetLayout(name=name, blocks=_blocks_for_sheet(name, sheet_cells, date1904))
        for name, sheet_cells in by_sheet.items()
    ]
    return Layout(sheets=sheets)


def _blocks_for_sheet(sheet: str, cells: list[dict], date1904: bool) -> list[Block]:
    by_row: dict[int, list[dict]] = defaultdict(list)
    for cell in cells:
        by_row[int(cell["row"])].append(cell)
    bands = _header_bands(by_row, date1904)
    blocks: list[Block] = []
    row_ids = sorted(by_row)
    for i, band_rows in enumerate(bands):
        header_row = max(band_rows)
        end = min(bands[i + 1]) if i + 1 < len(bands) else max(row_ids) + 1
        start = min(band_rows)
        body_rows = [r for r in row_ids if start <= r < end]
        axis = _axis_from_band(sheet, band_rows, by_row, date1904)
        calendar_n = sum(
            1
            for header in axis.headers
            if header.role in {"historical", "forecast", "stub"} and header.period_key
            not in {"actual", "plan", "total", "stub"}
        )
        if calendar_n < 2:
            continue
        band_cells = [c for r in body_rows for c in by_row[r]]
        label_col = _label_col(band_cells, date1904)
        period_cols = {header.col for header in axis.headers}
        data_rows = _data_rows(
            by_row, body_rows, set(band_rows), label_col, date1904, period_cols
        )
        blocks.append(
            Block(
                block_id=f"{sheet}!r{header_row}",
                label_col=label_col,
                axis=axis,
                rows=data_rows,
            )
        )
    return blocks


def _header_bands(by_row: dict[int, list[dict]], date1904: bool) -> list[list[int]]:
    rows = sorted(by_row)
    bands: list[list[int]] = []
    i = 0
    while i < len(rows):
        row_n = rows[i]
        if not _is_starter_row(by_row[row_n], date1904):
            i += 1
            continue
        prefix: list[int] = []
        k = i - 1
        prev_max = max(bands[-1]) if bands else -1
        while k >= 0 and rows[k] > prev_max:
            prev = rows[k]
            prev_cells = by_row[prev]
            if _is_empty_row(prev_cells, date1904):
                k -= 1
                continue
            prev_layers = _row_layers(prev_cells, date1904)
            if prev_layers <= {LAYER_ROLE} and LAYER_ROLE in prev_layers:
                prefix.insert(0, prev)
                k -= 1
                continue
            break
        layers = _row_layers(by_row[row_n], date1904)
        for prow in prefix:
            layers |= _row_layers(by_row[prow], date1904)
        band = prefix + [row_n]
        j = i + 1
        while j < len(rows):
            nxt = rows[j]
            cells = by_row[nxt]
            if _is_empty_row(cells, date1904) or _is_counter_row(cells, date1904):
                j += 1
                continue
            if _is_data_row(cells, date1904):
                break
            new_layers = _row_layers(cells, date1904)
            if not new_layers:
                break
            if new_layers <= {LAYER_ROLE} and (layers & _CALENDAR_LAYERS):
                marker_cols = [
                    int(cell["col"])
                    for cell in cells
                    if is_role_marker_text(_text(cell, date1904))
                ]
                calendar_cols = _band_calendar_cols(band, by_row, date1904)
                if marker_cols and all(col not in calendar_cols for col in marker_cols):
                    break
                layers |= new_layers
                band.append(nxt)
                j += 1
                continue
            overlap = layers & new_layers & {LAYER_YEAR, LAYER_DATE}
            if (
                overlap
                and _is_starter_row(cells, date1904)
                and not (LAYER_BOUNDS in layers or LAYER_BOUNDS in new_layers)
            ):
                break
            layers |= new_layers
            band.append(nxt)
            j += 1
        bands.append(band)
        i = j if j > i else i + 1
    return bands


def _band_calendar_cols(
    band_rows: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> set[int]:
    cols: set[int] = set()
    for row_n in band_rows:
        for cell in by_row[row_n]:
            atom = classify_atom(_text(cell, date1904))
            if atom is None or atom.kind in {"role_marker", "noise"}:
                continue
            if atom.kind == "full" or atom.year or atom.quarter or atom.month:
                cols.add(int(cell["col"]))
    return cols


def _axis_from_band(
    sheet: str,
    band_rows: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> Axis:
    header_row = max(band_rows)
    cols = sorted(
        {
            int(cell["col"])
            for row_n in band_rows
            for cell in by_row[row_n]
            if _text(cell, date1904)
        }
    )
    prefer_end = _band_prefers_end(band_rows, by_row, date1904)
    allow_q = any(
        is_quarter_label(_row_label(by_row[row_n], date1904)) for row_n in band_rows
    )
    allow_m = any(
        is_month_label(_row_label(by_row[row_n], date1904)) for row_n in band_rows
    )
    role_runs = _role_runs(band_rows, by_row, cols, date1904, prefer_end=prefer_end)
    composed: list[AxisHeader] = []
    year_hint: str | None = None
    for col in cols:
        atoms = _column_atoms(
            col,
            band_rows,
            by_row,
            date1904,
            allow_q=allow_q,
            allow_m=allow_m,
            prefer_end=prefer_end,
        )
        hit = compose_period(atoms, year_hint=year_hint)
        if hit is None:
            continue
        if hit.period_key[:4].isdigit():
            year_hint = hit.period_key[:4]
        text = _column_text(col, band_rows, by_row, date1904, prefer_end=prefer_end)
        role = _column_role(col, atoms, hit, role_runs)
        composed.append(
            AxisHeader(col=col, text=text, role=role, period_key=hit.period_key)
        )
    grain = infer_grain([header.period_key for header in composed])
    grained = [apply_grain(header.period_key, grain) for header in composed]
    if grain in {"week", "biweek"} or len(set(grained)) < len(grained):
        headers = composed
    else:
        headers = [
            header.model_copy(update={"period_key": key})
            for header, key in zip(composed, grained, strict=True)
        ]
    return Axis(id=f"{sheet}!r{header_row}", row=header_row, headers=headers)


def _column_atoms(
    col: int,
    band_rows: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
    *,
    allow_q: bool,
    allow_m: bool,
    prefer_end: bool,
) -> list[HeaderAtom]:
    atoms: list[HeaderAtom] = []
    for row_n in band_rows:
        if prefer_end and is_start_period_label(_row_label(by_row[row_n], date1904)):
            continue
        cell = _cell_at(by_row[row_n], col)
        if cell is None:
            continue
        row_allow_q = allow_q or is_quarter_label(_row_label(by_row[row_n], date1904))
        row_allow_m = allow_m or is_month_label(_row_label(by_row[row_n], date1904))
        atom = classify_atom(
            _text(cell, date1904),
            allow_quarter_num=row_allow_q,
            allow_month_num=row_allow_m,
        )
        if atom is not None:
            atoms.append(atom)
    return atoms


def _column_text(
    col: int,
    band_rows: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
    *,
    prefer_end: bool,
) -> str:
    parts: list[str] = []
    for row_n in band_rows:
        if prefer_end and is_start_period_label(_row_label(by_row[row_n], date1904)):
            continue
        cell = _cell_at(by_row[row_n], col)
        if cell is None:
            continue
        text = _text(cell, date1904)
        if text and classify_atom(text) is not None and text not in parts:
            parts.append(text)
    return " ".join(parts)


def _column_role(col: int, atoms: list[HeaderAtom], hit, role_runs: dict[int, str]):
    for atom in atoms:
        if atom.explicit_role and atom.role in {"historical", "forecast", "stub"}:
            if atom.kind == "full" and atom.period_key in {"actual", "plan"}:
                continue
            return atom.role
    for atom in atoms:
        if atom.kind == "role_marker" and atom.role in {"historical", "forecast"}:
            return atom.role
    if col in role_runs:
        return role_runs[col]
    return hit.role


def _role_runs(
    band_rows: list[int],
    by_row: dict[int, list[dict]],
    cols: list[int],
    date1904: bool,
    *,
    prefer_end: bool,
) -> dict[int, str]:
    calendar_cols = {
        col
        for col in cols
        if any(
            atom.kind in {"full", "quarter_token", "month_num"}
            or atom.year
            or atom.quarter
            or atom.month
            for atom in _column_atoms(
                col,
                band_rows,
                by_row,
                date1904,
                allow_q=True,
                allow_m=True,
                prefer_end=prefer_end,
            )
            if atom.kind != "role_marker"
        )
    }
    assigned: dict[int, str] = {}
    for row_n in band_rows:
        markers: list[tuple[int, str]] = []
        for cell in sorted(by_row[row_n], key=lambda c: int(c["col"])):
            text = _text(cell, date1904)
            if is_role_marker_text(text):
                hit = classify_header(text)
                if hit is not None and hit.role in {"historical", "forecast"}:
                    markers.append((int(cell["col"]), hit.role))
        if not markers:
            continue
        for index, (mcol, role) in enumerate(markers):
            if mcol in calendar_cols:
                assigned[mcol] = role
                continue
            right = markers[index + 1][0] if index + 1 < len(markers) else max(cols) + 1
            for col in cols:
                if col >= mcol and col < right:
                    assigned[col] = role
        if len(markers) == 1 and markers[0][0] not in calendar_cols:
            _mcol, role = markers[0]
            for col in cols:
                assigned.setdefault(col, role)
    return assigned


def _band_prefers_end(
    band_rows: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> bool:
    labels = [_row_label(by_row[row_n], date1904) for row_n in band_rows]
    return any(is_start_period_label(label) for label in labels) and any(
        is_end_period_label(label) for label in labels
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


def _label_col(band_cells: list[dict], date1904: bool) -> int:
    candidates: list[int] = []
    for cell in band_cells:
        if cell.get("hidden"):
            continue
        text = _text(cell, date1904)
        if not text or classify_header(text) is not None or _is_number(text):
            continue
        candidates.append(int(cell["col"]))
    if candidates:
        return min(candidates)
    visible = [int(c["col"]) for c in band_cells if not c.get("hidden")]
    return min(visible) if visible else 1


def _data_rows(
    by_row: dict[int, list[dict]],
    band_rows: list[int],
    header_rows: set[int],
    label_col: int,
    date1904: bool,
    period_cols: set[int],
) -> list[LayoutRow]:
    out: list[LayoutRow] = []
    section_stack: list[LayoutRow] = []
    for row_n in band_rows:
        if row_n in header_rows:
            continue
        if _is_empty_row(by_row[row_n], date1904):
            continue
        if _is_counter_row(by_row[row_n], date1904):
            continue
        label_cell = _cell_at(by_row[row_n], label_col)
        if label_cell is None:
            continue
        label = _text(label_cell, date1904)
        if not label:
            continue
        indent = _indent(label)
        check_row = bool(_CHECK.search(label))
        kind = _row_kind(by_row[row_n], period_cols, date1904, check_row=check_row)
        if kind == "abstract":
            while section_stack and section_stack[-1].indent >= indent:
                section_stack.pop()
        else:
            while section_stack and section_stack[-1].indent > indent:
                section_stack.pop()
        section_path = [item.label for item in section_stack]
        parent_row = None
        for prev in reversed(out):
            if prev.indent < indent:
                parent_row = prev.row
                break
        if parent_row is None and section_stack:
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
        )
        out.append(item)
        if kind == "abstract":
            section_stack.append(item)
    return out


def _row_kind(
    row_cells: list[dict],
    period_cols: set[int],
    date1904: bool,
    *,
    check_row: bool,
) -> str:
    if check_row:
        return "helper"
    if _is_index_values(row_cells, period_cols, date1904):
        return "index"
    has_formula = False
    has_number = False
    for cell in row_cells:
        if int(cell["col"]) not in period_cols:
            continue
        if cell.get("formula_raw"):
            has_formula = True
        text = _text(cell, date1904)
        if text and _is_number(text):
            has_number = True
    if not has_formula and not has_number:
        return "abstract"
    return "fact"


def _is_index_values(
    row_cells: list[dict],
    period_cols: set[int],
    date1904: bool,
) -> bool:
    nums: list[int] = []
    for cell in sorted(row_cells, key=lambda c: int(c["col"])):
        if int(cell["col"]) not in period_cols:
            continue
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
    if start in {0, 1} and nums == list(range(start, start + len(nums))):
        return True
    return min(nums) >= 0 and max(nums) <= 12


def _cell_at(row_cells: list[dict], col: int) -> dict | None:
    for cell in row_cells:
        if int(cell["col"]) == col:
            return cell
    return None


def _text(cell: dict, date1904: bool = False) -> str | None:
    return display_cell_text(
        cell.get("cached_value") if cell.get("cached_value") is not None else None,
        cell.get("number_format"),
        date1904=date1904,
    )


def _is_number(text: str) -> bool:
    try:
        float(text.replace(" ", "").replace(",", "."))
    except ValueError:
        return False
    return True


def _is_int_text(text: str) -> bool:
    try:
        value = float(text.replace(" ", "").replace(",", "."))
    except ValueError:
        return False
    return value == int(value)


def _indent(label: str) -> int:
    stripped = label.lstrip(" \t")
    return len(label) - len(stripped)
