from __future__ import annotations

import re
from collections import defaultdict

from finance_context.excel.a1 import format_addr, index_to_col
from finance_context.excel.dates import is_date_format, serial_to_date
from finance_context.formulas.engine import FormulaEngine
from finance_context.layout.models import (
    AxisHeader,
    AxisPeriod,
    Block,
    Layout,
    LayoutRow,
    SheetLayout,
    TimeAxis,
)
from finance_context.layout.params import (
    attach_stub_cells,
    carve_params_regions,
    detect_params_block,
    is_unit_text,
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
from finance_context.layout.resolve import project_axis

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
    return Layout(sheets=sheets)


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


def _axis_candidates(
    sheet: str,
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> list[tuple[list[int], TimeAxis]]:
    formula_cols = _formula_timeline_cols(by_row)
    candidates: list[tuple[list[int], TimeAxis]] = []
    for band_rows in _header_bands(by_row, date1904):
        for axis in _axes_from_band(sheet, band_rows, by_row, date1904):
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
    return _keep_dominant(_fold_year_banners(candidates))


def _period_count(axis: TimeAxis) -> int:
    return sum(
        1
        for period in axis.periods
        if period.role in _PERIOD_ROLES and period.period_key not in _STRUCTURAL_KEYS
    )


def _axis_cols(axis: TimeAxis) -> set[int]:
    return {period.col for period in axis.periods}


def _axis_signature(axis: TimeAxis) -> tuple:
    return (
        axis.grain,
        tuple(
            (period.col, period.period_key, period.role, period.group_key)
            for period in axis.periods
        ),
    )


def _flag_bit(cell: dict | None, date1904: bool) -> bool:
    if cell is None:
        return False
    text = _text(cell, date1904)
    blob = (text or str(cell.get("cached_value") or "")).strip().replace(",", ".")
    if blob.lower() in {"1", "1.0", "true", "yes"}:
        return True
    try:
        return abs(float(blob) - 1.0) < 1e-9
    except ValueError:
        return False


def _flag_fingerprint(
    block: Block | None,
    axis: TimeAxis,
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> tuple:
    if block is None:
        return ()
    rows = [row for row in block.rows if row.kind == "flag"]
    if not rows:
        return ()
    items: list[tuple[str, tuple[bool, ...]]] = []
    for row in rows:
        bits = tuple(
            _flag_bit(_cell_at(by_row.get(row.row, []), period.col), date1904)
            for period in axis.periods
        )
        items.append((row.label, bits))
    return tuple(items)


def _flags_compatible(left: tuple, right: tuple) -> bool:
    if not left or not right:
        return True
    return left == right


def _share_repeated_axes(
    blocks: list[Block],
    axes: list[TimeAxis],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> tuple[list[Block], list[TimeAxis]]:
    """Later section headers that repeat an axis reference the first one."""
    owner: dict[str, Block] = {}
    for block in blocks:
        for axis_id in block.axis_ids:
            owner.setdefault(axis_id, block)
    buckets: dict[tuple, list[tuple[TimeAxis, tuple]]] = {}
    alias: dict[str, str] = {}
    kept: list[TimeAxis] = []
    for axis in axes:
        derived = next((other for other in kept if _derived_axis(axis, other, by_row)), None)
        if derived is not None:
            alias[axis.id] = derived.id
            continue
        signature = _axis_signature(axis)
        fingerprint = _flag_fingerprint(owner.get(axis.id), axis, by_row, date1904)
        match: TimeAxis | None = None
        for previous, previous_flags in buckets.get(signature, []):
            if _flags_compatible(previous_flags, fingerprint):
                match = previous
                if not previous_flags and fingerprint:
                    buckets[signature] = [
                        (item, fingerprint if item.id == previous.id else flags)
                        for item, flags in buckets[signature]
                    ]
                break
        if match is not None:
            alias[axis.id] = match.id
            continue
        buckets.setdefault(signature, []).append((axis, fingerprint))
        kept.append(axis)
    if not alias:
        return blocks, axes
    by_id = {axis.id: axis for axis in kept}
    for block in blocks:
        seen: list[str] = []
        for axis_id in block.axis_ids:
            mapped = alias.get(axis_id, axis_id)
            if mapped not in seen:
                seen.append(mapped)
        block.axis_ids = seen
        if len(seen) == 1 and seen[0] in by_id:
            block.axis = project_axis(by_id[seen[0]])
        elif len(seen) != 1:
            block.axis = None
    return blocks, kept


_CELL_REF = re.compile(r"(?<![A-Za-z_])\$?([A-Z]{1,3})\$?(\d+)(?![\d(])")


def _derived_axis(axis: TimeAxis, other: TimeAxis, by_row: dict[int, list[dict]]) -> bool:
    """Header cells that compute from another axis's ruler (`=YEAR(M7)`) are that axis."""
    if axis.header_row <= other.header_row:
        return False
    keys = {period.col: period.period_key for period in other.periods}
    periods = [period for period in axis.periods if period.col in keys]
    if len(periods) < 2 or len(periods) < 0.8 * len(axis.periods):
        return False
    if any(keys[period.col] != period.period_key for period in periods):
        return False
    derived = 0
    for period in periods:
        cell = _cell_at(by_row.get(axis.header_row, []), period.col)
        formula = str((cell or {}).get("formula_raw") or "")
        letter = index_to_col(period.col)
        if any(
            col == letter and int(row) == other.header_row
            for col, row in _CELL_REF.findall(formula.upper())
        ):
            derived += 1
    return derived >= 0.8 * len(periods)


def _table_groups(
    candidates: list[tuple[list[int], TimeAxis]],
) -> list[list[tuple[list[int], TimeAxis]]]:
    groups: list[list[tuple[list[int], TimeAxis]]] = []
    index: dict[tuple[int, tuple[int, ...]], int] = {}
    for band_rows, axis in candidates:
        key = (axis.header_row, tuple(band_rows))
        slot = index.get(key)
        if slot is None:
            index[key] = len(groups)
            groups.append([(band_rows, axis)])
        else:
            groups[slot].append((band_rows, axis))
    groups.sort(key=lambda group: (min(group[0][0]), min(_axis_cols(group[0][1]) or {0})))
    return groups


def _repeated_year_axis(axis: TimeAxis) -> bool:
    keys = [
        period.period_key
        for period in axis.periods
        if period.role in _PERIOD_ROLES and period.period_key not in _STRUCTURAL_KEYS
    ]
    if len(keys) < 2 or len(set(keys)) == len(keys):
        return False
    return all(len(key) == 4 and key.isdigit() for key in keys)


def _fold_year_banners(
    candidates: list[tuple[list[int], TimeAxis]],
) -> list[tuple[list[int], TimeAxis]]:
    """A repeated year row over a finer axis is that axis's group_key, not a timeline."""
    banners = [(band, axis) for band, axis in candidates if _repeated_year_axis(axis)]
    if not banners:
        return candidates
    kept = [(band, axis) for band, axis in candidates if not _repeated_year_axis(axis)]
    folded: list[tuple[list[int], TimeAxis]] = []
    consumed: set[int] = set()
    for band, axis in kept:
        updated = axis
        cols = _axis_cols(axis)
        for banner_index, (_banner_band, banner) in enumerate(banners):
            banner_cols = _axis_cols(banner)
            if not _same_timeline(cols, banner_cols):
                continue
            by_col = {period.col: period.period_key for period in banner.periods}
            periods = [
                period.model_copy(update={"group_key": by_col[period.col]})
                if period.col in by_col and period.group_key is None
                else period
                for period in updated.periods
            ]
            updated = updated.model_copy(update={"periods": periods})
            consumed.add(banner_index)
        folded.append((band, updated))
    for banner_index, item in enumerate(banners):
        if banner_index not in consumed:
            folded.append(item)
    folded.sort(key=lambda item: (min(item[0]), min(_axis_cols(item[1]) or {0})))
    return folded


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
    candidates: list[tuple[list[int], TimeAxis]],
) -> list[tuple[list[int], TimeAxis]]:
    if not candidates:
        return []
    primary = max(candidates, key=lambda item: _period_count(item[1]))
    pcols = _axis_cols(primary[1])
    pmin = min(pcols) if pcols else 0
    pwidth = _period_count(primary[1])
    kept: list[tuple[list[int], TimeAxis]] = []
    for item in candidates:
        if item is primary:
            kept.append(item)
            continue
        cols = _axis_cols(item[1])
        if not cols:
            continue
        width = _period_count(item[1])
        if max(cols) < pmin:
            if width >= 3:
                kept.append(item)
            continue
        if _same_timeline(cols, pcols):
            kept.append(item)
            continue
        if cols.isdisjoint(pcols) and (
            width >= 3 or _comparable_width(width, pwidth)
        ):
            kept.append(item)
    kept.sort(key=lambda item: (min(item[0]), min(_axis_cols(item[1]) or {0})))
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
) -> TimeAxis:
    prefix = _relative_prefix(_row_label(row_cells, date1904))
    periods = [
        AxisPeriod(
            col=col,
            text=str(value),
            role="relative",
            period_key=f"{prefix}{value}",
        )
        for col, value in run
    ]
    keys = [period.period_key for period in periods]
    return TimeAxis(
        id=f"{sheet}!r{row_n}",
        grain=infer_grain(keys),
        header_row=row_n,
        periods=periods,
    )


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
            if _is_flag_bits_row(cells, _band_calendar_cols(band, by_row, date1904), date1904):
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


def _is_flag_bits_row(
    row_cells: list[dict], calendar_cols: set[int], date1904: bool
) -> bool:
    """A 0/1 row under the date ruler is a timing flag, not another header line."""
    bits = 0
    for cell in row_cells:
        if int(cell["col"]) not in calendar_cols:
            continue
        text = _text(cell, date1904)
        if not text:
            continue
        if not _is_number(text):
            return False
        if float(text.replace(" ", "").replace(",", ".")) not in {0.0, 1.0}:
            return False
        bits += 1
    return bits >= 2


_DMY = re.compile(r"^(\d{1,2})[./-](\d{1,2})[./-](\d{4})$")
_YMD = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _cell_iso_date(cell: dict | None, date1904: bool) -> str | None:
    if cell is None:
        return None
    if is_date_format(cell.get("number_format")):
        parsed = serial_to_date(cell.get("cached_value") or "", date1904=date1904)
        return parsed.isoformat() if parsed is not None else None
    text = str(cell.get("cached_value") or "").strip()
    match = _DMY.match(text)
    if match:
        day, month, year = (int(part) for part in match.groups())
        return f"{year:04d}-{month:02d}-{day:02d}" if 1 <= month <= 12 and 1 <= day <= 31 else None
    match = _YMD.match(text)
    return "-".join(match.groups()) if match else None


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


def _axes_from_band(
    sheet: str,
    band_rows: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> list[TimeAxis]:
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
    labels = {row_n: _row_label(by_row[row_n], date1904) for row_n in band_rows}
    start_rows = [row_n for row_n in band_rows if is_start_period_label(labels[row_n])]
    end_rows = [row_n for row_n in band_rows if is_end_period_label(labels[row_n])]
    bounds: dict[int, tuple[str | None, str | None]] = {}
    composed: list[AxisHeader] = []
    year_hint: str | None = None
    for col in cols:
        if prefer_end and start_rows:
            starts = [_cell_at(by_row[row_n], col) for row_n in start_rows]
            if not any(_text(cell, date1904) for cell in starts if cell is not None):
                continue
            start = next(
                (iso for cell in starts if (iso := _cell_iso_date(cell, date1904))), None
            )
            end = next(
                (
                    iso
                    for row_n in end_rows
                    if (iso := _cell_iso_date(_cell_at(by_row[row_n], col), date1904))
                ),
                None,
            )
            bounds[col] = (start, end)
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
        role, explicit_role = _column_role(col, atoms, hit, role_runs)
        composed.append(
            AxisHeader(
                col=col,
                text=text,
                role=role,
                period_key=hit.period_key,
                explicit_role=explicit_role,
            )
        )
    runs = _split_header_runs(composed)
    axes: list[TimeAxis] = []
    multi = len(runs) > 1
    for run in runs:
        grain = infer_grain([header.period_key for header in run])
        grained = [apply_grain(header.period_key, grain) for header in run]
        if grain in {"week", "biweek"} or len(set(grained)) < len(grained):
            headers = run
        else:
            headers = [
                header.model_copy(update={"period_key": key})
                for header, key in zip(run, grained, strict=True)
            ]
        axis_id = f"{sheet}!r{header_row}"
        if multi:
            axis_id = f"{axis_id}c{headers[0].col}"
        axes.append(
            TimeAxis(
                id=axis_id,
                grain=grain,
                header_row=header_row,
                periods=[
                    AxisPeriod(
                        col=header.col,
                        text=header.text,
                        role=header.role,
                        period_key=header.period_key,
                        explicit_role=header.explicit_role,
                        start_date=bounds.get(header.col, (None, None))[0],
                        end_date=bounds.get(header.col, (None, None))[1],
                    )
                    for header in headers
                ],
            )
        )
    return axes


def _header_key_class(key: str) -> str:
    if len(key) == 4 and key.isdigit():
        return "year"
    if len(key) >= 7 and key[4] == "-":
        return "date"
    if key[:1] in {"Y", "Q", "M", "P"} and key[1:].isdigit():
        return "relative"
    return "other"


def _split_header_runs(headers: list[AxisHeader]) -> list[list[AxisHeader]]:
    if not headers:
        return []
    ordered = sorted(headers, key=lambda item: item.col)
    runs: list[list[AxisHeader]] = [[ordered[0]]]
    for prev, item in zip(ordered, ordered[1:], strict=False):
        gap = item.col - prev.col > 1
        left_cls = _header_key_class(prev.period_key)
        right_cls = _header_key_class(item.period_key)
        grain_break = (
            not gap
            and left_cls != right_cls
            and "other" not in {left_cls, right_cls}
        )
        if gap or grain_break:
            runs.append([item])
        else:
            runs[-1].append(item)
    return [run for run in runs if len(run) >= 2]


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


def _column_role(
    col: int, atoms: list[HeaderAtom], hit, role_runs: dict[int, str]
) -> tuple[str, bool]:
    for atom in atoms:
        if atom.explicit_role and atom.role in {"historical", "forecast", "stub"}:
            if atom.kind == "full" and atom.period_key in {"actual", "plan"}:
                continue
            return atom.role, True
    for atom in atoms:
        if atom.kind == "role_marker" and atom.role in {"historical", "forecast"}:
            return atom.role, True
    if col in role_runs:
        return role_runs[col], True
    return hit.role, bool(getattr(hit, "explicit_role", False))


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
        # `Total` shares the section header's indent, so the outline parent is the
        # statement (`Balance Sheet`) and gold cannot tell Current from Non-current.
        if (
            section_stack
            and label.strip().casefold() in {"total", "subtotal", "sub total", "sum", "итого", "всего"}
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
