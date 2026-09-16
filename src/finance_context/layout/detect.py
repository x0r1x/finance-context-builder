from __future__ import annotations

import re
from collections import defaultdict

from finance_context.excel.a1 import format_addr
from finance_context.formulas.engine import FormulaEngine
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
    normalize_header,
)

_CHECK = re.compile(
    r"check|проверк|контроль|tie[- ]?out|plug\b|сход[ия]|должен",
    re.IGNORECASE,
)
_INDEX_LABEL = re.compile(r"^(№|n|no|#)$", re.IGNORECASE)
_COUNTER_LABEL = re.compile(
    r"week\b|#|№|period\s*index|\bindex\b|счётчик|счетчик|номер",
    re.IGNORECASE,
)
_FLAG_LABEL = re.compile(
    r"\b(flag|flags|construction|ops|operating|online|toggle|switch|binary)\b",
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
_CALENDAR_LAYERS = frozenset({LAYER_DATE, LAYER_YEAR, LAYER_QUARTER, LAYER_MONTH})
_RELATIVE_LABEL = re.compile(
    r"^(project\s+)?(year|period|month|quarter|год|период|мес\w*|кв\w*)s?\b",
    re.IGNORECASE,
)
_PERIOD_ROLES = frozenset({"historical", "forecast", "stub", "relative"})
_STRUCTURAL_KEYS = frozenset({"actual", "plan", "total", "stub"})
_UNLABELED_RELATIVE_MIN = 8
_FORMULA_RUN_MIN = 3
_FP_ENGINE = FormulaEngine(locale_hint="en")



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
    row_ids = sorted(by_row)
    candidates = _axis_candidates(sheet, by_row, date1904)
    blocks: list[Block] = []
    for i, (band_rows, axis) in enumerate(candidates):
        header_row = max(band_rows)
        end = min(candidates[i + 1][0]) if i + 1 < len(candidates) else max(row_ids) + 1
        start = min(band_rows)
        body_rows = [r for r in row_ids if start <= r < end]
        period_cols = {header.col for header in axis.headers}
        label_col, span = _label_span(
            by_row, body_rows, set(band_rows), period_cols, date1904
        )
        data_rows = _data_rows(
            by_row, body_rows, set(band_rows), span, date1904, period_cols
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


def _axis_candidates(
    sheet: str,
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> list[tuple[list[int], Axis]]:
    formula_cols = _formula_timeline_cols(by_row)
    candidates: list[tuple[list[int], Axis]] = []
    for band_rows in _header_bands(by_row, date1904):
        axis = _axis_from_band(sheet, band_rows, by_row, date1904)
        if _period_count(axis) >= 2:
            candidates.append((band_rows, axis))
    taken = {row_n for band_rows, _ in candidates for row_n in band_rows}
    for row_n in sorted(by_row):
        if row_n in taken:
            continue
        labeled = _relative_run(by_row[row_n], date1904, require_label=True, min_len=3)
        if labeled:
            axis = _axis_from_relative(sheet, row_n, labeled, by_row[row_n], date1904)
            candidates.append(([row_n], axis))
            taken.add(row_n)
            continue
        unlabeled = _relative_run(
            by_row[row_n],
            date1904,
            require_label=False,
            min_len=_UNLABELED_RELATIVE_MIN,
        )
        if not unlabeled or not formula_cols:
            continue
        run_cols = {col for col, _ in unlabeled}
        if not _same_timeline(run_cols, formula_cols):
            continue
        if any(_same_timeline(run_cols, _axis_cols(axis)) for _, axis in candidates):
            continue
        axis = _axis_from_relative(sheet, row_n, unlabeled, by_row[row_n], date1904)
        candidates.append(([row_n], axis))
        taken.add(row_n)
    return _keep_dominant(candidates)


def _period_count(axis: Axis) -> int:
    return sum(
        1
        for header in axis.headers
        if header.role in _PERIOD_ROLES and header.period_key not in _STRUCTURAL_KEYS
    )


def _axis_cols(axis: Axis) -> set[int]:
    return {header.col for header in axis.headers}


def _same_timeline(left: set[int], right: set[int]) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    lmin, lmax = min(left), max(left)
    rmin, rmax = min(right), max(right)
    if abs(lmin - rmin) <= 1 and abs(lmax - rmax) <= 1:
        return True
    union = len(left | right)
    return union > 0 and len(left & right) / union >= 0.8


def _comparable_width(left: int, right: int) -> bool:
    if left <= 0 or right <= 0:
        return False
    return min(left, right) / max(left, right) >= 0.75


def _keep_dominant(
    candidates: list[tuple[list[int], Axis]],
) -> list[tuple[list[int], Axis]]:
    if not candidates:
        return []
    primary = max(candidates, key=lambda item: _period_count(item[1]))
    pcols = _axis_cols(primary[1])
    pmin = min(pcols) if pcols else 0
    pwidth = _period_count(primary[1])
    kept: list[tuple[list[int], Axis]] = []
    for item in candidates:
        if item is primary:
            kept.append(item)
            continue
        cols = _axis_cols(item[1])
        if not cols:
            continue
        if max(cols) < pmin:
            continue
        if _same_timeline(cols, pcols):
            kept.append(item)
            continue
        if cols.isdisjoint(pcols) and _comparable_width(_period_count(item[1]), pwidth):
            kept.append(item)
    kept.sort(key=lambda item: min(item[0]))
    return kept


def _cell_fingerprint(cell: dict) -> str | None:
    template = cell.get("formula_template")
    if template:
        return str(template)
    raw = cell.get("formula_raw")
    if not raw:
        return None
    addr = cell.get("addr") or format_addr(int(cell["col"]), int(cell["row"]))
    parsed = _FP_ENGINE.parse(str(raw), sheet=str(cell.get("sheet") or ""), addr=str(addr))
    if parsed.unparsed or not parsed.template:
        return None
    return parsed.template


def _row_formula_run(row_cells: list[dict]) -> list[int]:
    items: list[tuple[int, str]] = []
    for cell in sorted(row_cells, key=lambda item: int(item["col"])):
        fingerprint = _cell_fingerprint(cell)
        if fingerprint:
            items.append((int(cell["col"]), fingerprint))
    best: list[tuple[int, str]] = []
    run: list[tuple[int, str]] = []
    for col, fingerprint in items:
        if run and col == run[-1][0] + 1 and fingerprint == run[-1][1]:
            run.append((col, fingerprint))
        else:
            run = [(col, fingerprint)]
        if len(run) > len(best):
            best = list(run)
    if len(best) < _FORMULA_RUN_MIN:
        return []
    return [col for col, _ in best]


def _formula_timeline_cols(by_row: dict[int, list[dict]]) -> set[int] | None:
    best: tuple[int, int, int, int] | None = None
    for cells in by_row.values():
        cols = _row_formula_run(cells)
        if len(cols) < _FORMULA_RUN_MIN:
            continue
        width = cols[-1] - cols[0]
        candidate = (width, len(cols), cols[0], cols[-1])
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    if best is None:
        return None
    _, _, start, end = best
    return set(range(start, end + 1))


def _relative_run(
    row_cells: list[dict],
    date1904: bool,
    *,
    require_label: bool,
    min_len: int,
) -> list[tuple[int, int]]:
    label = _row_label(row_cells, date1904)
    raw = normalize_header(label)
    if require_label and (not raw or not _RELATIVE_LABEL.match(raw)):
        return []
    if label and _FLAG_LABEL.search(label):
        return []
    seq: list[tuple[int, int]] = []
    for cell in sorted(row_cells, key=lambda item: int(item["col"])):
        text = _text(cell, date1904)
        if not text or classify_header(text) is not None:
            continue
        if not _is_int_text(text):
            if _is_number(text):
                seq = []
            continue
        seq.append((int(cell["col"]), int(float(text.replace(",", ".")))))
    best: list[tuple[int, int]] = []
    run: list[tuple[int, int]] = []
    for col, value in seq:
        if run and value == run[-1][1] + 1 and col == run[-1][0] + 1:
            run.append((col, value))
        else:
            run = [(col, value)]
        if len(run) > len(best):
            best = list(run)
    if len(best) >= min_len and best[0][1] in {0, 1}:
        return best
    return []


def _relative_prefix(label: str | None) -> str:
    raw = normalize_header(label) or ""
    if re.search(r"quarter|кв", raw, re.IGNORECASE):
        return "Q"
    if re.search(r"month|мес", raw, re.IGNORECASE):
        return "M"
    if re.search(r"year|год", raw, re.IGNORECASE):
        return "Y"
    return "P"


def _axis_from_relative(
    sheet: str,
    row_n: int,
    run: list[tuple[int, int]],
    row_cells: list[dict],
    date1904: bool,
) -> Axis:
    prefix = _relative_prefix(_row_label(row_cells, date1904))
    headers = [
        AxisHeader(
            col=col,
            text=str(value),
            role="relative",
            period_key=f"{prefix}{value}",
        )
        for col, value in run
    ]
    return Axis(id=f"{sheet}!r{row_n}", row=row_n, headers=headers)


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


def _label_span(
    by_row: dict[int, list[dict]],
    body_rows: list[int],
    header_rows: set[int],
    period_cols: set[int],
    date1904: bool,
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
            if col >= left_edge or col in period_cols:
                continue
            text = _text(cell, date1904)
            if not text or _is_number(text) or classify_header(text) is not None:
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
        label, depth, label_cell = _row_span_label(by_row[row_n], label_span, date1904)
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
            label_col=int(label_cell["col"]),
        )
        out.append(item)
        if kind == "abstract":
            section_stack.append(item)
    return out


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
        if not text or _is_number(text) or classify_header(text) is not None:
            continue
        return text, depth, cell
    return None, 0, None


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
    binary_vals: set[int] = set()
    binary_ok = True
    binary_n = 0
    for cell in row_cells:
        if int(cell["col"]) not in period_cols:
            continue
        if cell.get("formula_raw"):
            has_formula = True
        text = _text(cell, date1904)
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
    if not has_formula and not has_number:
        if _SCENARIO_LABEL.search(label) or _FLAG_BODY.search(label):
            return "flag"
        return "abstract"
    if _SCENARIO_LABEL.search(label) or _FLAG_BODY.search(label):
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
