from __future__ import annotations

import re
from datetime import date

from finance_context.excel.a1 import format_addr, index_to_col
from finance_context.excel.dates import is_date_format, serial_to_date
from finance_context.formulas.engine import FormulaEngine
from finance_context.layout.cells import _cell_at, _is_number, _text, parse_int, parse_number
from finance_context.layout.markers import DerivedScan, apply_axis_markers
from finance_context.layout.models import AxisHeader, AxisPeriod, TimeAxis, is_series_column
from finance_context.layout.periods import (
    _HALF_KEY,
    LAYER_BOUNDS,
    LAYER_DATE,
    LAYER_HALF,
    LAYER_MONTH,
    LAYER_QUARTER,
    LAYER_YEAR,
    HeaderAtom,
    apply_grain,
    classify_atom,
    classify_header,
    compose_period,
    infer_grain,
    is_end_period_label,
    is_month_label,
    is_quarter_label,
    is_start_period_label,
    marker_token,
    normalize_header,
    parse_period_key,
)
from finance_context.layout.rows import (
    _RELATIVE_LABEL,
    _is_counter_row,
    _is_data_row,
    _is_empty_row,
    _is_starter_row,
    _row_label,
    _row_layers,
    _year_banner_by_column,
)
from finance_context.layout.totals import apply_total_columns

_FLAG_LABEL = re.compile(
    r"\b(flag|flags|construction|ops|operating|online|toggle|switch|binary)\b",
    re.IGNORECASE,
)


_CALENDAR_LAYERS = frozenset({LAYER_DATE, LAYER_YEAR, LAYER_QUARTER, LAYER_HALF, LAYER_MONTH})


_UNLABELED_RELATIVE_MIN = 8


_FORMULA_RUN_MIN = 3


_FP_ENGINE = FormulaEngine(locale_hint="en")


def _axis_candidates(
    sheet: str,
    by_row: dict[int, list[dict]],
    date1904: bool,
    hidden_cols: set[int] | None = None,
) -> list[tuple[list[int], TimeAxis]]:
    known = set(hidden_cols or ()) | _inferred_hidden_columns(by_row)
    formula_cols = _formula_timeline_cols(by_row)
    candidates: list[tuple[list[int], TimeAxis]] = []
    bands = _header_bands(by_row, date1904)
    # One pass per column set. Every band used to rescan the rows under its header.
    scan = DerivedScan(by_row, date1904, [max(band_rows) for band_rows in bands])
    for band_rows in bands:
        body_end = _next_band_row(bands, max(band_rows))
        for axis in _axes_from_band(
            sheet, band_rows, by_row, date1904, known, body_end=body_end, scan=scan
        ):
            if _period_count(axis) >= 2:
                candidates.append((band_rows, axis))
    taken = {row_n for band_rows, _ in candidates for row_n in band_rows}
    for row_n in sorted(by_row):
        if row_n in taken:
            continue
        labeled = _relative_run(
            by_row[row_n], date1904, require_label=True, min_len=3, hidden_cols=known
        )
        if labeled:
            axis = _axis_from_relative(
                sheet, row_n, labeled, by_row[row_n], date1904, hidden_cols=known
            )
            candidates.append(([row_n], axis))
            taken.add(row_n)
            continue
        # Without a formula timeline the run is discarded. Scanning it still
        # classifies every body cell.
        if not formula_cols:
            continue
        unlabeled = _relative_run(
            by_row[row_n],
            date1904,
            require_label=False,
            min_len=_UNLABELED_RELATIVE_MIN,
            hidden_cols=known,
        )
        if not unlabeled:
            continue
        run_cols = {col for col, _ in unlabeled}
        if not _same_timeline(run_cols, formula_cols):
            continue
        if any(_same_timeline(run_cols, _axis_cols(axis)) for _, axis in candidates):
            continue
        axis = _axis_from_relative(
            sheet, row_n, unlabeled, by_row[row_n], date1904, hidden_cols=known
        )
        candidates.append(([row_n], axis))
        taken.add(row_n)
    return _keep_dominant(_fold_year_banners(candidates))


def _period_count(axis: TimeAxis) -> int:
    return sum(
        1
        for period in axis.periods
        if is_series_column(period.kind, period.period_key)
    )


def _axis_cols(axis: TimeAxis) -> set[int]:
    return {period.col for period in axis.periods}


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
        if is_series_column(period.kind, period.period_key)
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
            periods: list[AxisPeriod] = []
            applied = False
            comparable = 0
            contradicted = 0
            for period in updated.periods:
                banner_year = by_col.get(period.col)
                own_year = _key_year(period.period_key)
                if banner_year is not None and own_year is not None:
                    comparable += 1
                    if own_year != banner_year:
                        contradicted += 1
                        periods.append(period)
                        continue
                if banner_year is not None and period.group_key is None:
                    periods.append(period.model_copy(update={"group_key": banner_year}))
                    applied = True
                else:
                    periods.append(period)
            updated = updated.model_copy(update={"periods": periods})
            if applied or (comparable > 0 and contradicted == comparable):
                consumed.add(banner_index)
        folded.append((band, updated))
    for banner_index, item in enumerate(banners):
        if banner_index not in consumed:
            folded.append(item)
    folded.sort(key=lambda item: (min(item[0]), min(_axis_cols(item[1]) or {0})))
    return folded


def _key_year(period_key: str | None) -> str | None:
    """Year already written into a composed key such as ``2014Q3`` or ``2020-01``."""
    if period_key is None or len(period_key) < 4:
        return None
    head = period_key[:4]
    if head.isdigit():
        return head
    return None


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
        # A later header on the same columns is its own table, not a stub of the wider axis.
        if item[1].header_row != primary[1].header_row and width >= 3:
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


def _inferred_hidden_columns(by_row: dict[int, list[dict]]) -> set[int]:
    """A column is hidden when a visible row still has a hidden cell in it.

    A row whose every cell is hidden is a hidden row. Its cells do not mark columns.
    """
    found: set[int] = set()
    for cells in by_row.values():
        if not cells or not any(not cell.get("hidden") for cell in cells):
            continue
        for cell in cells:
            if cell.get("hidden"):
                found.add(int(cell["col"]))
    return found


def _cols_connected(left: int, right: int, hidden_cols: set[int]) -> bool:
    """Adjacent columns, or a gap filled only by hidden columns."""
    if right == left + 1:
        return True
    if right <= left + 1:
        return False
    return all(col in hidden_cols for col in range(left + 1, right))


# ``parse_number`` rejects a non-finite float. Fifteen digits stays finite.
_SURE_INT_LEN = 15


def _sure_integer(text: str) -> bool:
    """True when ``text`` is an integer ``parse_number`` accepts."""
    if len(text) > _SURE_INT_LEN:
        return False
    if text.startswith("-"):
        text = text[1:]
    return bool(text) and text.isdigit()


def _non_numeric_text(cell: dict, date1904: bool) -> str | None:
    """Displayed text of one visible cell that is not a number.

    A plain integer skips display and ``float``. A date format can turn that
    same integer into a date string, so those cells still go through display.
    """
    cached = cell.get("cached_value")
    if cached is None:
        return None
    fmt = cell.get("number_format")
    if fmt is not None and is_date_format(fmt):
        shown = _text(cell, date1904)
        if shown is None or _is_number(shown):
            return None
        return shown
    shown = cached if isinstance(cached, str) else str(cached)
    if not shown.strip() or _sure_integer(shown) or _is_number(shown):
        return None
    return shown


def _visible_text_matches(
    row_cells: list[dict],
    date1904: bool,
    pattern: re.Pattern[str],
    *,
    normalized: bool,
) -> bool:
    """True when a visible, non-numeric cell matches ``pattern``.

    ``normalized`` uses ``match`` on the header fold, which is how a relative
    label is recognized. A flag word uses ``search`` on the cell text.
    """
    for cell in row_cells:
        if cell.get("hidden"):
            continue
        text = _non_numeric_text(cell, date1904)
        if text is None:
            continue
        if normalized:
            raw = normalize_header(text)
            if raw and pattern.match(raw):
                return True
        elif pattern.search(text):
            return True
    return False


def _relative_run(
    row_cells: list[dict],
    date1904: bool,
    *,
    require_label: bool,
    min_len: int,
    hidden_cols: set[int] | None = None,
) -> list[tuple[int, int]]:
    # The run is counted from cells on the row. Fewer cells cannot hold it,
    # and a year triple must not be classified once per number to learn that.
    if len(row_cells) < min_len:
        return []
    # The resolved label is one visible non-number cell. Neither check can
    # succeed when no such cell matches, and finding that out does not parse
    # the label as a header.
    if require_label and not _visible_text_matches(
        row_cells, date1904, _RELATIVE_LABEL, normalized=True
    ):
        return []
    label = None
    if _visible_text_matches(row_cells, date1904, _FLAG_LABEL, normalized=False):
        label = _row_label(row_cells, date1904)
        if label and _FLAG_LABEL.search(label):
            return []
    elif require_label:
        label = _row_label(row_cells, date1904)
    raw = normalize_header(label)
    if require_label and (not raw or not _RELATIVE_LABEL.match(raw)):
        return []
    seq: list[tuple[int, int]] = []
    for cell in sorted(row_cells, key=lambda item: int(item["col"])):
        text = _text(cell, date1904)
        if not text or classify_header(text) is not None:
            continue
        number = parse_int(text)
        if number is None:
            if parse_number(text) is not None:
                seq = []
            continue
        seq.append((int(cell["col"]), number))
    bridge = hidden_cols or set()
    best: list[tuple[int, int]] = []
    run: list[tuple[int, int]] = []
    for col, value in seq:
        # The next integer is the continuation. A hidden-only prefix stays
        # attached across an empty hidden column when that holds; a scratch
        # value that is not the next integer starts a new run.
        connected = (
            bool(run)
            and value == run[-1][1] + 1
            and _cols_connected(run[-1][0], col, bridge)
        )
        if connected:
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
    hidden_cols: set[int] | None = None,
) -> TimeAxis:
    prefix = _relative_prefix(_row_label(row_cells, date1904))
    hidden = hidden_cols or set()
    periods = [
        AxisPeriod(
            col=col,
            text=str(value),
            kind="relative",
            period_key=f"{prefix}{value}",
            hidden=col in hidden,
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


def _next_band_row(bands: list[list[int]], header_row: int) -> int | None:
    """First row of the next header band. Rows from there belong to a later table."""
    later = [min(band) for band in bands if min(band) > header_row]
    return min(later) if later else None


def _header_bands(by_row: dict[int, list[dict]], date1904: bool) -> list[list[int]]:
    rows = sorted(by_row)
    bands: list[list[int]] = []
    i = 0
    while i < len(rows):
        row_n = rows[i]
        if not _is_starter_row(by_row[row_n], date1904):
            i += 1
            continue
        layers = _row_layers(by_row[row_n], date1904)
        band = [row_n]
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
        number = parse_number(text)
        if number is None or number not in {0.0, 1.0}:
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
            if atom is None or atom.kind in {"marker", "noise"}:
                continue
            if atom.kind == "full" or atom.year or atom.quarter or atom.half or atom.month:
                cols.add(int(cell["col"]))
    return cols


def _axes_from_band(
    sheet: str,
    band_rows: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
    hidden_cols: set[int] | None = None,
    body_end: int | None = None,
    *,
    scan: DerivedScan,
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
    # One label per header row. `_column_atoms` used to rescan the row on every
    # column, so a wide ruler was classified width times.
    labels = {row_n: _row_label(by_row[row_n], date1904) for row_n in band_rows}
    quarter_row = {row_n: is_quarter_label(labels[row_n]) for row_n in band_rows}
    month_row = {row_n: is_month_label(labels[row_n]) for row_n in band_rows}
    start_row = {row_n: is_start_period_label(labels[row_n]) for row_n in band_rows}
    allow_q = any(quarter_row.values())
    allow_m = any(month_row.values())
    start_rows = [row_n for row_n in band_rows if start_row[row_n]]
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
            start_row=start_row,
            quarter_row=quarter_row,
            month_row=month_row,
        )
        hit = compose_period(atoms, year_hint=year_hint)
        if hit is None:
            continue
        if hit.period_key[:4].isdigit():
            year_hint = hit.period_key[:4]
        text = _column_text(
            col,
            band_rows,
            by_row,
            date1904,
            prefer_end=prefer_end,
            start_row=start_row,
        )
        kind, variant, explicit_role = _column_mark(atoms, hit)
        composed.append(
            AxisHeader(
                col=col,
                text=text,
                kind=kind,
                variant=variant,
                period_key=hit.period_key,
                explicit_role=explicit_role,
            )
        )
    known_hidden = hidden_cols or set()
    banner_years: dict[int, str] = {}
    for row_n in band_rows:
        for col, year in _year_banner_by_column(by_row[row_n], date1904).items():
            banner_years.setdefault(col, year)
    runs = _split_header_runs(composed, known_hidden)
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
        drafted = [
            AxisPeriod(
                col=header.col,
                text=header.text,
                kind=header.kind or "period",
                variant=header.variant,
                period_key=header.period_key,
                explicit_role=header.explicit_role,
                group_key=_banner_group(header.period_key, banner_years.get(header.col)),
                start_date=bounds.get(header.col, (None, None))[0],
                end_date=bounds.get(header.col, (None, None))[1],
                hidden=header.col in known_hidden,
            )
            for header in headers
        ]
        marked, notes = apply_axis_markers(
            drafted,
            sheet=sheet,
            header_row=header_row,
            by_row=by_row,
            date1904=date1904,
            banner_row=min(band_rows) - 1,
            scan=scan,
        )
        totaled = apply_total_columns(
            marked,
            header_row=header_row,
            by_row=by_row,
            date1904=date1904,
            band_rows=band_rows,
            body_end=body_end,
        )
        periods = disambiguate_period_keys(totaled)
        axes.append(
            TimeAxis(
                id=axis_id,
                grain=grain,
                header_row=header_row,
                periods=periods,
                notes=notes,
            )
        )
    return axes


_RELATIVE_STEP = re.compile(r"^([YQMP])(\d+)$")


def _banner_group(period_key: str, year: str | None) -> str | None:
    """A year banner over a finer header is that period's group, not a second key."""
    if not year or period_key == year:
        return None
    head = period_key[:4]
    if head.isdigit() and head == year:
        return year
    return None


def _period_continues(left: str, right: str) -> bool:
    """True when ``right`` is the next period of the same grain as ``left``."""
    left_rel = _RELATIVE_STEP.fullmatch(left)
    right_rel = _RELATIVE_STEP.fullmatch(right)
    if left_rel and right_rel and left_rel.group(1) == right_rel.group(1):
        return int(right_rel.group(2)) == int(left_rel.group(2)) + 1
    left_half = _HALF_KEY.fullmatch(left)
    right_half = _HALF_KEY.fullmatch(right)
    if left_half and right_half:
        left_index = int(left_half.group(1)) * 2 + int(left_half.group(2))
        right_index = int(right_half.group(1)) * 2 + int(right_half.group(2))
        return right_index == left_index + 1
    left_year, left_quarter, left_month, left_day = parse_period_key(left)
    right_year, right_quarter, right_month, right_day = parse_period_key(right)
    if left_year is None or right_year is None:
        return False
    if left_day and right_day and left_month and right_month:
        try:
            left_date = date(int(left_year), left_month, left_day)
            right_date = date(int(right_year), right_month, right_day)
        except ValueError:
            return False
        return (right_date - left_date).days in {1, 7, 14}
    if left_quarter and right_quarter:
        return int(right_year) * 4 + right_quarter == int(left_year) * 4 + left_quarter + 1
    if left_month and right_month and not left_day and not right_day:
        return int(right_year) * 12 + right_month == int(left_year) * 12 + left_month + 1
    if (
        left_quarter is None
        and right_quarter is None
        and left_month is None
        and right_month is None
        and left_day is None
        and right_day is None
    ):
        return int(right_year) == int(left_year) + 1
    return False


def _run_continues(headers: list[AxisHeader]) -> bool:
    return all(
        _period_continues(left.period_key, right.period_key)
        for left, right in zip(headers, headers[1:], strict=False)
    )


def _header_key_class(key: str) -> str:
    if len(key) == 4 and key.isdigit():
        return "year"
    if len(key) >= 7 and key[4] == "-":
        return "date"
    if key[:1] in {"Y", "Q", "M", "P"} and key[1:].isdigit():
        return "relative"
    return "other"


def _split_header_runs(
    headers: list[AxisHeader], hidden_cols: set[int] | None = None
) -> list[list[AxisHeader]]:
    if not headers:
        return []
    bridge = hidden_cols or set()
    ordered = sorted(headers, key=lambda item: item.col)
    runs: list[list[AxisHeader]] = [[ordered[0]]]
    for prev, item in zip(ordered, ordered[1:], strict=False):
        # An empty hidden column keeps a run together. A prefix that lives only
        # in hidden columns stays attached when the next header continues the
        # sequence (2024, empty hidden column, 2025). Scratch that does not
        # continue (1965 next to 1939) still starts a new run.
        physical_gap = item.col - prev.col > 1
        bridged = physical_gap and _cols_connected(prev.col, item.col, bridge)
        hidden_only = all(header.col in bridge for header in runs[-1])
        # 1938 then 1939 is a one-step continuation, but a hidden prefix of
        # 1965, 1938 is not itself a sequence. Require both.
        continues = _period_continues(prev.period_key, item.period_key) and (
            not hidden_only or _run_continues(runs[-1])
        )
        gap = physical_gap and (not bridged or (hidden_only and not continues))
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
    start_row: dict[int, bool],
    quarter_row: dict[int, bool],
    month_row: dict[int, bool],
) -> list[HeaderAtom]:
    atoms: list[HeaderAtom] = []
    for row_n in band_rows:
        if prefer_end and start_row[row_n]:
            continue
        cell = _cell_at(by_row[row_n], col)
        if cell is None:
            continue
        atom = classify_atom(
            _text(cell, date1904),
            allow_quarter_num=allow_q or quarter_row[row_n],
            allow_month_num=allow_m or month_row[row_n],
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
    start_row: dict[int, bool],
) -> str:
    parts: list[str] = []
    for row_n in band_rows:
        if prefer_end and start_row[row_n]:
            continue
        cell = _cell_at(by_row[row_n], col)
        if cell is None:
            continue
        text = _text(cell, date1904)
        if text and classify_atom(text) is not None and text not in parts:
            parts.append(text)
    return " ".join(parts)


def disambiguate_period_keys(periods: list[AxisPeriod]) -> list[AxisPeriod]:
    """Suffix a repeated key with ``@<marker>`` when the columns' markers differ.

    The token is ``marker_token``: ``casefold`` of the marker text recognized in
    the header. ``2024 факт`` and ``2024 план`` become ``2024@факт`` and
    ``2024@план``. A column with no marker keeps the base key. A group whose
    markers are all equal, including a repeated bare year, is left unchanged.
    """
    groups: dict[str, list[int]] = {}
    for index, period in enumerate(periods):
        groups.setdefault(period.period_key, []).append(index)
    updates: dict[int, str] = {}
    for key, indexes in groups.items():
        if len(indexes) < 2:
            continue
        markers = [marker_token(periods[index].variant) for index in indexes]
        if len(set(markers)) < 2:
            continue
        for index, marker in zip(indexes, markers, strict=True):
            if not marker:
                continue
            updates[index] = f"{key}@{marker}"
    if not updates:
        return periods
    return [
        period.model_copy(update={"period_key": updates[index]}) if index in updates else period
        for index, period in enumerate(periods)
    ]


def _column_mark(atoms: list[HeaderAtom], hit) -> tuple[str, str | None, bool]:
    for atom in atoms:
        if atom.explicit_role and atom.variant and atom.column_kind in {"period", "stub", None}:
            return atom.column_kind or "period", atom.variant, True
    return hit.kind, hit.variant, bool(getattr(hit, "explicit_role", False))


def _band_prefers_end(
    band_rows: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> bool:
    labels = [_row_label(by_row[row_n], date1904) for row_n in band_rows]
    return any(is_start_period_label(label) for label in labels) and any(
        is_end_period_label(label) for label in labels
    )
