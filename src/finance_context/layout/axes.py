from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from finance_context.excel.a1 import format_addr, index_to_col
from finance_context.excel.dates import is_date_format, serial_to_date
from finance_context.formulas.engine import FormulaEngine
from finance_context.layout.cells import (
    _cell_at,
    _cell_covering,
    _is_number,
    _text,
    parse_int,
    parse_number,
)
from finance_context.layout.format_words import is_total_word
from finance_context.layout.markers import DerivedScan, _fill_banner, apply_axis_markers
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
    is_calendar_hit,
    is_end_period_label,
    is_month_label,
    is_quarter_label,
    is_start_period_label,
    marker_token,
    normalize_header,
    parse_period_key,
    period_position,
)
from finance_context.layout.rows import (
    _RELATIVE_LABEL,
    _distinct_year_ruler,
    _is_bare_year_text,
    _is_counter_row,
    _is_data_row,
    _is_empty_row,
    _is_quarter_token_row,
    _is_starter_row,
    _is_year_banner_row,
    _repeated_year_line,
    _row_can_supply_year,
    _row_label,
    _row_layers,
    _word_residual,
    _year_banner_by_column,
    _year_run_is_header,
)
from finance_context.layout.totals import apply_total_columns
from finance_context.layout.units import is_unit_text

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
) -> tuple[list[tuple[list[int], TimeAxis]], list[str]]:
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
    horizontal = _drop_repeated_over_finer(
        _keep_dominant(_fold_year_banners(candidates)), by_row, date1904
    )
    vertical, notes = _vertical_axes(sheet, by_row, date1904, horizontal)
    return [*horizontal, *vertical], notes


# A smaller rank is a finer period. A repeated quarter label over months is not
# a second axis.
_GRAIN_RANK = {
    "week": 1,
    "biweek": 2,
    "month": 3,
    "quarter": 4,
    "half_year": 5,
    "year": 6,
}


def _drop_repeated_over_finer(
    candidates: list[tuple[list[int], TimeAxis]],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> list[tuple[list[int], TimeAxis]]:
    """Drop a ruler that repeats one full key where a finer ruler already covers it.

    ``3 кв. 2020`` three times, over June–September, is the month axis. The
    quarter text does not become 115 periods of the same keys.
    """
    header_keys: dict[int, dict[int, str]] = {}
    kept: list[tuple[list[int], TimeAxis]] = []
    for band, axis in candidates:
        if _repeated_full_keys(axis) and _finer_axis_covers(
            axis, candidates, by_row, date1904, header_keys
        ):
            continue
        kept.append((band, axis))
    return kept


def _repeated_full_keys(axis: TimeAxis) -> bool:
    keys = [
        period.period_key
        for period in axis.periods
        if is_series_column(period.kind, period.period_key)
    ]
    return any(left == right for left, right in zip(keys, keys[1:], strict=False))


def _finer_axis_covers(
    axis: TimeAxis,
    candidates: list[tuple[list[int], TimeAxis]],
    by_row: dict[int, list[dict]],
    date1904: bool,
    header_keys: dict[int, dict[int, str]],
) -> bool:
    rank = _GRAIN_RANK.get(axis.grain or "")
    cols = _axis_cols(axis)
    if rank is None or not cols:
        return False
    periods = [
        period for period in axis.periods if is_series_column(period.kind, period.period_key)
    ]
    for band, other in candidates:
        other_rank = _GRAIN_RANK.get(other.grain or "")
        if other_rank is None or other_rank >= rank or _repeated_full_keys(other):
            continue
        other_cols = _axis_cols(other)
        if len(cols & other_cols) < 0.6 * len(cols):
            continue
        # A lower section can reprint the banner beside the finer header.
        # Column overlap alone also occurs between independent stacked tables.
        for row in (*band, min(band) - 1, max(band) + 1):
            if row not in header_keys:
                header_keys[row] = {
                    int(cell["col"]): hit.period_key
                    for cell in by_row.get(row, ())
                    if (hit := classify_header(_text(cell, date1904))) is not None
                }
            keys = header_keys[row]
            for shift in (0, -1, 1):
                matches = sum(
                    period.col + shift in other_cols
                    and keys.get(period.col + shift) == period.period_key
                    for period in periods
                )
                if matches >= 0.6 * len(periods):
                    return True
    return False


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
        # ``Год ввода | 2024…`` under a finished ruler is a value row. One
        # repeated year is not the only such row: ``2024, 2024, 2025, 2025``
        # is too. A distinct ``2018, 2019, 2020`` ruler can still open a table.
        # The same years with no line name stay a header. Amounts with a few
        # calendar cells are the first value row, not the next header.
        if bands:
            covered = _band_calendar_cols(bands[-1], by_row, date1904)
            row_cells = by_row[row_n]
            if _band_covers_years(bands[-1], by_row, row_n, date1904) and (
                _repeated_year_line(row_cells, date1904)
                or _labeled_year_value_row(row_cells, covered, date1904)
            ):
                i += 1
                continue
            if _partial_year_beside_quarters(bands[-1], row_cells, by_row, date1904):
                i += 1
                continue
            # Amounts in the previous table do not cancel a header whose
            # calendar cells sit in other columns of the same physical row.
            if (
                covered
                and not _starter_outside_columns(row_cells, covered, date1904)
                and (
                    _labeled_numeric_under_axis(row_cells, covered, date1904)
                    or _blank_axis_formulas(row_cells, covered, date1904)
                )
                and not _distinct_year_ruler(row_cells, date1904)
            ):
                i += 1
                continue
        layers = _row_layers(by_row[row_n], date1904)
        band = [row_n]
        if i > 0 and _is_quarter_token_row(by_row[row_n], date1904):
            prev = rows[i - 1]
            if 0 < row_n - prev <= 2 and _is_year_banner_row(by_row[prev], date1904):
                band = [prev, row_n]
                layers |= _row_layers(by_row[prev], date1904)
        j = i + 1
        band_quarters = _is_quarter_token_row(by_row[row_n], date1904)
        band_years = _distinct_year_ruler(by_row[row_n], date1904)
        # Period columns of the rows already in the band. Later rows are not
        # scanned again to rediscover them.
        calendar_cols = _band_calendar_cols(band, by_row, date1904)
        while j < len(rows):
            nxt = rows[j]
            cells = by_row[nxt]
            if _is_empty_row(cells, date1904) or _is_counter_row(cells, date1904):
                j += 1
                continue
            if _is_data_row(cells, date1904):
                break
            # A year ruler and a row of bare quarters are two axes, whichever
            # comes first. A repeated year stays in the quarter band.
            quarter_tokens = _is_quarter_token_row(cells, date1904)
            year_ruler = _distinct_year_ruler(cells, date1904)
            if (band_quarters and year_ruler) or (band_years and quarter_tokens):
                break
            if _is_flag_bits_row(cells, calendar_cols, date1904):
                break
            # ``Остаток на начало периода`` is a bounds layer, so ``_is_data_row``
            # is false, but numeric caches in the axis columns are values.
            # A formula with an empty cache in those columns is a value too.
            # A formula cache of quarter text has no number and can still join.
            # ``Год | 2024 | 2024 | 2025 | 2025`` under bare quarters is the
            # missing year layer, so it stays in the band. The same shape under
            # quarters that already name a year is a value row. An unlabeled
            # copy of that year row stays with the quarters.
            quarter_cols = _quarter_columns(band, by_row, date1904)
            if _blank_axis_formulas(cells, calendar_cols, date1904):
                break
            if _year_layer_supplements(cells, calendar_cols, band, by_row, date1904):
                pass
            elif _labeled_year_value_row(cells, quarter_cols or calendar_cols, date1904):
                break
            elif _labeled_numeric_under_axis(cells, calendar_cols, date1904):
                break
            new_layers = _row_layers(cells, date1904)
            if not new_layers:
                break
            keep_year = (
                _row_label(cells, date1904) is None
                and bool(quarter_cols)
                and _year_values_on_axis(cells, quarter_cols, date1904)
            )
            overlap = layers & new_layers & {LAYER_YEAR, LAYER_DATE}
            if (
                overlap
                and _is_starter_row(cells, date1904)
                and not (LAYER_BOUNDS in layers or LAYER_BOUNDS in new_layers)
                and not keep_year
            ):
                break
            layers |= new_layers
            band.append(nxt)
            calendar_cols.update(_period_columns(cells, date1904))
            j += 1
        bands.append(band)
        i = j if j > i else i + 1
    return bands


def _band_covers_years(
    band: list[int],
    by_row: dict[int, list[dict]],
    row_n: int,
    date1904: bool,
) -> bool:
    years = {
        int(cell["col"])
        for cell in by_row[row_n]
        if _is_bare_year_text(_text(cell, date1904))
    }
    if not years:
        return False
    covered = {int(cell["col"]) for row in band for cell in by_row.get(row, [])}
    return bool(years & covered)


def _quarter_columns(
    band: list[int], by_row: dict[int, list[dict]], date1904: bool
) -> set[int]:
    """Columns whose header cell is a quarter, bare or already dated."""
    cols: set[int] = set()
    for row_n in band:
        for cell in by_row[row_n]:
            atom = classify_atom(_text(cell, date1904))
            if atom is None or atom.kind in {"marker", "noise"}:
                continue
            if atom.quarter or atom.kind == "quarter_token":
                cols.add(int(cell["col"]))
    return cols


def _column_has_year(
    band: list[int], by_row: dict[int, list[dict]], col: int, date1904: bool
) -> bool:
    for row_n in band:
        cell = _cell_covering(by_row[row_n], col)
        atom = classify_atom(_text(cell, date1904) if cell is not None else None)
        if atom is not None and atom.year:
            return True
    return False


def _quarters_need_a_year(
    band: list[int], by_row: dict[int, list[dict]], date1904: bool
) -> bool:
    """True when a quarter column in the band does not name its year yet.

    A title such as ``Отчет 2024`` left of the quarters is not one of those
    columns. One explicit ``1 кв. 2024`` does not finish the other quarters.
    """
    columns = _quarter_columns(band, by_row, date1904)
    if not columns:
        return False
    return any(not _column_has_year(band, by_row, col, date1904) for col in columns)


def _year_values_on_axis(
    row_cells: list[dict], calendar_cols: set[int], date1904: bool
) -> bool:
    """Bare years on every requested column, including a merged year cell."""
    if not calendar_cols:
        return False
    banner = _year_banner_by_column(row_cells, date1904)
    values: list[int] = []
    for col in sorted(calendar_cols):
        year = banner.get(col)
        if year is None:
            return False
        number = parse_int(year)
        if number is None:
            return False
        values.append(number)
    return _year_run_is_header(values)


def _year_layer_supplements(
    row_cells: list[dict],
    _calendar_cols: set[int],
    band: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> bool:
    """A consistent year row can fill quarter columns that do not name a year."""
    columns = _quarter_columns(band, by_row, date1904)
    if not columns or not _quarters_need_a_year(band, by_row, date1904):
        return False
    return _year_values_on_axis(row_cells, columns, date1904)


def _year_row_on_this_header(
    band: list[int],
    row_cells: list[dict],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> bool:
    """True when the year row belongs to this header, not to a later table.

    A year layer sits on the header or one blank row under it. A ruler further
    down the sheet keeps its own axis even where the columns overlap.
    """
    if not row_cells:
        return False
    row_n = int(row_cells[0]["row"])
    gap = row_n - max(band)
    if gap <= 0 or gap > 2:
        return False
    for mid in range(max(band) + 1, row_n):
        cells = by_row.get(mid, [])
        if cells and not _is_empty_row(cells, date1904):
            return False
    return True


def _partial_year_beside_quarters(
    band: list[int],
    row_cells: list[dict],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> bool:
    """A partial year layer of this header is not its own axis.

    The row counts only when these quarters still need a year and the years
    sit on this header. A later ruler is a separate axis even if some columns
    overlap. A real gap in the layer that does belong here must not become a
    short ``2024@…`` ruler.
    """
    columns = _quarter_columns(band, by_row, date1904)
    if len(columns) < 2 or not _quarters_need_a_year(band, by_row, date1904):
        return False
    if not _year_row_on_this_header(band, row_cells, by_row, date1904):
        return False
    banner = _year_banner_by_column(row_cells, date1904)
    covered = [col for col in columns if col in banner]
    if len(covered) < 2:
        return False
    return any(col not in banner for col in columns)


def _starter_outside_columns(
    row_cells: list[dict], columns: set[int], date1904: bool
) -> bool:
    """A period header outside one table's columns can open the next axis."""
    outside = [cell for cell in row_cells if int(cell["col"]) not in columns]
    return bool(outside) and _is_starter_row(outside, date1904)


def _labeled_year_value_row(
    row_cells: list[dict], calendar_cols: set[int], date1904: bool
) -> bool:
    """``Год ввода | 2024 | 2024 | 2025 | 2025`` after a finished axis.

    ``_repeated_year_line`` only matches one year copied sideways. A stepped
    ruler ``2018, 2019, 2020`` is still allowed to open the next table.
    """
    if _is_year_banner_row(row_cells, date1904) or _row_label(row_cells, date1904) is None:
        return False
    if _distinct_year_ruler(row_cells, date1904):
        return False
    return _year_values_on_axis(row_cells, calendar_cols, date1904)


def _labeled_numeric_under_axis(
    row_cells: list[dict], calendar_cols: set[int], date1904: bool
) -> bool:
    """A line name left of this axis and ordinary numbers in its columns.

    The number may be a formula cache. A cache that reads as a period label
    is a header cell, so a formula quarter row still extends the band. ``3``
    under a ``Квартал`` label is that kind of header, as is ``1``–``12`` under
    a month label. A bare year next to a line name is a value unless it fills
    quarters that do not name a year yet. Calendar text among ordinary amounts
    does not make another header layer, however many such cells there are. A
    date-formatted serial displays as a date, not as one of these numbers.
    """
    if not calendar_cols:
        return False
    label = _row_label(row_cells, date1904)
    allow_q = is_quarter_label(label)
    allow_m = is_month_label(label)
    left = min(calendar_cols)
    has_label = False
    ordinary = 0
    for cell in row_cells:
        text = _text(cell, date1904)
        if not text:
            continue
        col = int(cell["col"])
        if col < left:
            if _is_number(text):
                continue
            atom = classify_atom(text)
            if atom is None or atom.kind in {"marker", "noise"}:
                has_label = True
            continue
        if col not in calendar_cols:
            continue
        atom = classify_atom(
            text, allow_quarter_num=allow_q, allow_month_num=allow_m
        )
        # A date, quarter or month token is the header layer. An amount is not,
        # even when other columns on the same row carry a calendar label.
        if atom is not None and atom.kind not in {"marker", "noise"}:
            continue
        if _is_number(text):
            ordinary += 1
    # Amounts in the confirmed columns are the value row. A consistent date
    # or quarter layer has no amount, so it can still extend the band.
    return has_label and ordinary > 0


def _blank_axis_formulas(
    row_cells: list[dict], calendar_cols: set[int], date1904: bool
) -> bool:
    """A line name and formulas in the axis whose cache does not display.

    A calendar layer may contain blank date-formatted formulas. Calendar
    caches do not cancel other blank value formulas in the same axis.
    """
    if not calendar_cols:
        return False
    label = _row_label(row_cells, date1904)
    if label is None:
        return False
    allow_q = is_quarter_label(label)
    allow_m = is_month_label(label)
    has_calendar = False
    has_formula = False
    has_blank_value_formula = False
    for cell in row_cells:
        if int(cell["col"]) not in calendar_cols:
            continue
        text = _text(cell, date1904)
        atom = classify_atom(text, allow_quarter_num=allow_q, allow_month_num=allow_m)
        if atom is not None and atom.kind not in {"marker", "noise"}:
            has_calendar = True
            continue
        if text and _is_number(text):
            return False
        if cell.get("formula_raw") or cell.get("formula_template"):
            has_formula = True
            if not text and not is_date_format(cell.get("number_format")):
                has_blank_value_formula = True
    if has_calendar:
        return has_blank_value_formula and not (allow_q or allow_m)
    return has_formula


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


def _period_columns(row_cells: list[dict], date1904: bool) -> set[int]:
    cols: set[int] = set()
    for cell in row_cells:
        atom = classify_atom(_text(cell, date1904))
        if atom is None or atom.kind in {"marker", "noise"}:
            continue
        if atom.kind == "full" or atom.year or atom.quarter or atom.half or atom.month:
            cols.add(int(cell["col"]))
    return cols


def _band_calendar_cols(
    band_rows: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> set[int]:
    cols: set[int] = set()
    for row_n in band_rows:
        cols.update(_period_columns(by_row[row_n], date1904))
    return cols


def _partial_date_layer(
    band_rows: list[int], by_row: dict[int, list[dict]], date1904: bool
) -> bool:
    """A year ruler is missing a date in at least one of its columns.

    The dates that do exist must not replace that ruler or drop the open column.
    """
    ruler: set[int] = set()
    for row_n in band_rows:
        ruler.update(_year_banner_by_column(by_row[row_n], date1904))
    if len(ruler) < 2:
        return False
    for col in ruler:
        if any(
            _cell_iso_date(_cell_covering(by_row[row_n], col), date1904)
            for row_n in band_rows
        ):
            continue
        return True
    return False


def _keep_explicit_quarter(atoms: list[HeaderAtom]) -> list[HeaderAtom]:
    """An explicit ``1 кв. 2024`` wins over a different year in the same column."""
    explicit = {atom.year for atom in atoms if atom.quarter and atom.year}
    if len(explicit) != 1:
        return atoms
    year = next(iter(explicit))
    return [
        atom
        for atom in atoms
        if not (
            atom.year
            and atom.year != year
            and not atom.quarter
            and not atom.month
            and not atom.day
        )
    ]


def _side_year_caption(
    band_rows: list[int],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> set[int]:
    """Columns of a title such as ``Отчет 2024`` left of the period run.

    The period boundary and the title mark are read once per band. Calling
    this from every column reclassified the whole band once per column.
    """
    anchors: list[int] = []
    for row_n in band_rows:
        for cell in by_row[row_n]:
            text = _text(cell, date1904)
            atom = classify_atom(text)
            if atom is None or atom.kind in {"marker", "noise"}:
                continue
            if atom.quarter or atom.kind == "quarter_token" or _is_bare_year_text(text):
                anchors.append(int(cell["col"]))
            elif atom.day and atom.month:
                anchors.append(int(cell["col"]))
    if not anchors:
        return set()
    left = min(anchors)
    captions: set[int] = set()
    for row_n in band_rows:
        for cell in by_row[row_n]:
            col = int(cell["col"])
            if col >= left:
                continue
            text = _text(cell, date1904)
            if text and _word_residual(text):
                captions.add(col)
    return captions


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
    borrowed_cols: set[int] = set()
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
    drop_dates = _partial_date_layer(band_rows, by_row, date1904)
    captions = _side_year_caption(band_rows, by_row, date1904)
    for col in cols:
        if col in captions:
            continue
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
        if drop_dates:
            atoms = [
                atom
                for atom in atoms
                if not (atom.day and atom.month and atom.year)
            ]
        atoms = _keep_explicit_quarter(atoms)
        # A bare quarter, half, or month borrows a year from the same column,
        # up to three rows up or down. A later column keeps that year until
        # its own column has one. The run is kept only when the step is steady.
        borrowed = False
        if _bare_part(atoms) and not any(atom.year for atom in atoms):
            carried = _year_near(by_row, header_row, col, date1904)
            if carried:
                atoms = [*atoms, HeaderAtom(kind="year", year=carried)]
                borrowed = True
            elif year_hint:
                borrowed = True
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
        if borrowed:
            borrowed_cols.add(col)
    known_hidden = hidden_cols or set()
    banner_years: dict[int, str] = {}
    for row_n in band_rows:
        for col, year in _year_banner_by_column(by_row[row_n], date1904).items():
            banner_years.setdefault(col, year)
    runs = _split_header_runs(
        composed,
        known_hidden,
        by_row=by_row,
        band_rows=band_rows,
        date1904=date1904,
    )
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
        if any(header.col in borrowed_cols for header in headers) and not _steady_month_step(
            [period.period_key for period in periods]
        ):
            continue
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


# Columns between two pieces of one ruler: a year total, or a total and a blank.
_JOIN_GAP = 2


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
    headers: list[AxisHeader],
    hidden_cols: set[int] | None = None,
    *,
    by_row: dict[int, list[dict]] | None = None,
    band_rows: list[int] | None = None,
    date1904: bool = False,
) -> list[list[AxisHeader]]:
    if not headers:
        return []
    bridge = hidden_cols or set()
    banner: dict[int, str] | None = None
    ordered = sorted(headers, key=lambda item: item.col)
    runs: list[list[AxisHeader]] = [[ordered[0]]]
    for index, item in enumerate(ordered[1:], start=1):
        prev = ordered[index - 1]
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
        between = item.col - prev.col - 1
        # «Итого» between quarter groups is not a new axis. An empty column
        # still splits the run.
        join_gap = (
            0 < between <= _JOIN_GAP
            and _period_continues(prev.period_key, item.period_key)
            and (len(runs[-1]) == 1 or _run_continues(runs[-1]))
            and _gap_is_total(prev.col, item.col, by_row, band_rows, date1904)
        )
        gap = physical_gap and not join_gap and (not bridged or (hidden_only and not continues))
        left_cls = _header_key_class(prev.period_key)
        right_cls = _header_key_class(item.period_key)
        grain_break = (
            not gap
            and left_cls != right_cls
            and "other" not in {left_cls, right_cls}
        )
        # A second copy starts over at the first key of a steady ruler
        # (2020Q1 after 2020Q1–Q4, or 2024 after 2024–2029). The marker is part
        # of that identity: ``2024 план`` then ``2024 факт`` is still one ruler,
        # so the suffix can be written onto the key. A lone return of the
        # opening key is not a copy and stays here. A later repeat inside an
        # uneven row stays here too, and the same key in the next column does.
        opening = runs[-1][0]
        restarts = (
            len(runs[-1]) >= 2
            and item.period_key != prev.period_key
            and item.period_key == opening.period_key
            and marker_token(item.variant) == marker_token(opening.variant)
            and _run_continues(runs[-1])
            and _second_copy_follows(ordered, index, bridge)
        )
        if restarts and by_row and band_rows:
            if banner is None:
                banner = _fill_banner(
                    by_row.get(min(band_rows) - 1, []),
                    [header.col for header in ordered],
                    date1904,
                )
            marks = {marker_token(banner.get(header.col)) for header in (opening, item)}
            restarts = len(marks) != 2 or not marks <= _SCENARIO_BANNERS
        if gap or grain_break or restarts:
            runs.append([item])
        else:
            runs[-1].append(item)
    return [run for run in runs if len(run) >= 2]


_SCENARIO_BANNERS = {"actual", "budget", "forecast", "plan", "бюджет", "план", "прогноз", "факт"}


def _second_copy_follows(
    ordered: list[AxisHeader],
    index: int,
    hidden_cols: set[int],
) -> bool:
    """True when a second ruler of at least two periods starts at ``ordered[index]``.

    A single repeated opening key is not a copy. That column stays on the
    ruler that is already open instead of being discarded.
    """
    if index + 1 == len(ordered):
        return False
    start, next_header = ordered[index], ordered[index + 1]
    return (
        marker_token(next_header.variant) == marker_token(start.variant)
        and _cols_connected(start.col, next_header.col, hidden_cols)
        and _period_continues(start.period_key, next_header.period_key)
    )


def _gap_is_total(
    left: int,
    right: int,
    by_row: dict[int, list[dict]] | None,
    band_rows: list[int] | None,
    date1904: bool,
) -> bool:
    """True when every occupied cell between two headers is a total word."""
    if not by_row or not band_rows:
        return False
    saw = False
    for col in range(left + 1, right):
        for row_n in band_rows:
            cell = _cell_at(by_row.get(row_n, []), col)
            if cell is None:
                continue
            text = _text(cell, date1904)
            if not text:
                continue
            if not is_total_word(text):
                return False
            saw = True
    return saw


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
        cell = _cell_covering(by_row[row_n], col)
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
        cell = _cell_covering(by_row[row_n], col)
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


# Rows around a bare quarter that can still supply its year. CFS uses three.
_YEAR_NEAR = 3
# Rows above a vertical ruler that can still name its series.
_SERIES_NEAR = 5
_MONTH_STEPS = {1, 3, 6, 12}
_BARE_PARTS = {"quarter_token", "half_token", "month_num"}


def _bare_part(atoms: list[HeaderAtom]) -> bool:
    """Quarter, half, or month tokens with no finished period in the cell."""
    return any(atom.kind in _BARE_PARTS for atom in atoms)


def _year_near(
    by_row: dict[int, list[dict]], row_n: int, col: int, date1904: bool
) -> str | None:
    """Bare year in this column. Each distance looks up, then down."""
    for dist in range(1, _YEAR_NEAR + 1):
        for delta in (-dist, dist):
            cells = by_row.get(row_n + delta)
            if not cells or not _row_can_supply_year(cells, date1904):
                continue
            cell = _cell_covering(cells, col)
            text = _text(cell, date1904) if cell is not None else None
            if not text or not _is_bare_year_text(text):
                continue
            year = parse_int(text)
            if year is not None:
                return str(year)
    return None


def _month_index(key: str) -> int | None:
    """Month slot of a calendar key. Dates use the month, not the day number."""
    year, quarter, month, day = parse_period_key(key)
    if year is None:
        return None
    slot = int(year) * 12
    half = _HALF_KEY.fullmatch(key)
    if half:
        return slot + (int(half.group(2)) - 1) * 6
    if day and month:
        return slot + month - 1
    if quarter:
        return slot + (quarter - 1) * 3
    if month:
        return slot + month - 1
    return slot


def _steady_month_step(keys: list[str]) -> bool:
    """True when every step is the same 1, 3, 6, or 12 months."""
    indexes = [_month_index(key) for key in keys]
    if any(index is None for index in indexes):
        return False
    steps = [right - left for left, right in zip(indexes, indexes[1:], strict=False)]
    return bool(steps) and len(set(steps)) == 1 and steps[0] in _MONTH_STEPS


def _vertical_axes(
    sheet: str,
    by_row: dict[int, list[dict]],
    date1904: bool,
    horizontal: list[tuple[list[int], TimeAxis]],
) -> tuple[list[tuple[list[int], TimeAxis]], list[str]]:
    """Period runs down a column. A horizontal header row is left to that ruler.

    The period columns themselves stay free below that row, so a second table
    can use one of them as its own ruler.
    """
    taken_rows = {row_n for band, _axis in horizontal for row_n in band}
    by_col: dict[int, list[tuple[int, dict]]] = defaultdict(list)
    for row_n, cells in by_row.items():
        if row_n in taken_rows:
            continue
        for cell in cells:
            if not _period_candidate(cell, date1904):
                continue
            by_col[int(cell["col"])].append((row_n, cell))
    found: list[tuple[list[int], TimeAxis]] = []
    notes: list[str] = []
    for col, items in sorted(by_col.items()):
        items.sort(key=lambda item: item[0])
        for run in _vertical_runs(items, date1904):
            # 1902, 1905, 1908 inside a column of amounts is still amounts.
            # Item 1900, Item 1901 inside a label column is still a label.
            if _embedded_in_number_column(
                by_row, col, run, date1904
            ) or _year_slice_of_labels(by_row, col, run, date1904):
                continue
            if _vertical_run_ok(by_row, col, run, date1904) and _values_beside(
                by_row, col, run, date1904
            ):
                found.append(_vertical_axis(sheet, col, run))
                continue
            if _unnamed_vertical_run(by_row, col, run, date1904):
                notes.append(
                    f"Vertical periods on {sheet}!{format_addr(col, run[0][0])}"
                    " have no series names"
                )
    return found, notes


def _period_candidate(cell: dict, date1904: bool) -> bool:
    """True when the cell can be a period. Plain amounts never enter the scan."""
    cached = cell.get("cached_value")
    if cached in (None, ""):
        return False
    fmt = cell.get("number_format")
    if isinstance(fmt, str) and fmt and is_date_format(fmt):
        return True
    shown = cached if isinstance(cached, str) else str(cached)
    if not shown.strip():
        return False
    if not _is_number(shown):
        return True
    year = parse_int(shown)
    return year is not None and 1900 <= year <= 2099


def _year_slice_of_labels(
    by_row: dict[int, list[dict]],
    col: int,
    run: list[tuple[int, str, str]],
    date1904: bool,
) -> bool:
    """True when ``Item 1900`` continues a column of ``Item 1899``.

    The year was parsed out of a line label. A ruler such as ``2020 факт``
    has no neighbour with the same word and a non-year number.
    """
    words: list[str] = []
    for _row, _key, shown in run:
        hit = classify_header(shown)
        if hit is None or not hit.variant or " " in hit.variant:
            return False
        words.append(hit.variant.casefold())
    if len(set(words)) != 1:
        return False
    word = words[0]
    for edge, step in ((run[0][0], -1), (run[-1][0], 1)):
        cell = _cell_at(by_row.get(edge + step, []), col)
        if cell is None:
            continue
        text = _text(cell, date1904)
        if not text or not any(char.isdigit() for char in text):
            continue
        neighbour = classify_header(text)
        if neighbour is not None and is_calendar_hit(neighbour):
            continue
        if text.split()[0].casefold() == word:
            return True
    return False


def _embedded_in_number_column(
    by_row: dict[int, list[dict]],
    col: int,
    run: list[tuple[int, str, str]],
    date1904: bool,
) -> bool:
    """True when the year slice sits in a longer column of plain numbers.

    Three amounts on either side of the slice are enough. A total under a
    real year ruler is one number, so the ruler stays.
    """
    return _plain_number_streak(by_row, col, run[0][0], -1, date1904) or _plain_number_streak(
        by_row, col, run[-1][0], 1, date1904
    )


def _plain_number_streak(
    by_row: dict[int, list[dict]],
    col: int,
    start: int,
    step: int,
    date1904: bool,
) -> bool:
    row_n = start + step
    for _ in range(3):
        cell = _cell_at(by_row.get(row_n, []), col)
        if cell is None or _period_candidate(cell, date1904):
            return False
        text = _text(cell, date1904)
        if not text or not _is_number(text):
            return False
        row_n += step
    return True


def _unnamed_vertical_run(
    by_row: dict[int, list[dict]],
    col: int,
    run: list[tuple[int, str, str]],
    date1904: bool,
) -> bool:
    """A regular ruler with values, dropped only because no series row was found."""
    keys = [key for _row, key, _text in run]
    if not _regular_period_run(keys) or _entity_list(by_row, col, run, date1904):
        return False
    if not _values_beside(by_row, col, run, date1904):
        return False
    return _series_row_above(by_row, col, run[0][0], date1904) is None


def _vertical_runs(
    items: list[tuple[int, dict]], date1904: bool
) -> list[list[tuple[int, str, str]]]:
    runs: list[list[tuple[int, str, str]]] = []
    run: list[tuple[int, str, str]] = []
    for row_n, cell in items:
        text = _text(cell, date1904)
        hit = classify_header(text)
        key = hit.period_key if hit is not None and is_calendar_hit(hit) else None
        if key is None or (run and row_n != run[-1][0] + 1):
            if run:
                runs.append(run)
                run = []
            if key is None:
                continue
        run.append((row_n, key, text or key))
    if run:
        runs.append(run)
    return runs


def _vertical_run_ok(
    by_row: dict[int, list[dict]],
    col: int,
    run: list[tuple[int, str, str]],
    date1904: bool,
) -> bool:
    """A vertical ruler is a regular period step with series names above it.

    Three founded years, or a date journal beside one amount, stay a list.
    """
    keys = [key for _row, key, _text in run]
    if not _regular_period_run(keys):
        return False
    if _entity_list(by_row, col, run, date1904):
        return False
    return _series_row_above(by_row, col, run[0][0], date1904) is not None


def _regular_period_run(keys: list[str]) -> bool:
    """Three periods of one grain. Dates use the month step, not the day number.

    Month-ends step one month. Year-ends step twelve, including a leap day.
    Four adjacent days are a journal: the month step is zero, so they are not
    an axis.
    """
    if keys and all(parse_period_key(key)[3] for key in keys):
        return _steady_month_step(keys) and len(keys) >= 3
    positions = []
    for key in keys:
        found = period_position(key)
        if found is None:
            return False
        positions.append(found)
    if len({grain for grain, _index in positions}) != 1:
        return False
    steps = [
        right - left
        for (_g, left), (_h, right) in zip(positions, positions[1:], strict=False)
    ]
    if not steps or any(step <= 0 for step in steps) or len(set(steps)) != 1:
        return False
    if positions[0][0] == "day":
        return False
    return len(keys) >= 3


def _entity_list(
    by_row: dict[int, list[dict]],
    col: int,
    run: list[tuple[int, str, str]],
    date1904: bool,
) -> bool:
    """Different names on the left and a field header, not a repeated scenario.

    ``База`` down a column beside ``Год`` stays a ruler. ``A`` / ``B`` / ``C``
    beside ``Founded`` is a list.
    """
    names: list[str] = []
    for row_n, _key, _shown in run:
        for cell in by_row.get(row_n, []):
            if int(cell["col"]) >= col:
                continue
            text = _text(cell, date1904)
            if not text or _is_number(text) or is_calendar_hit(classify_header(text)):
                continue
            names.append(text.strip())
            break
    if len(names) < 2 or len(set(names)) < 2:
        return False
    header = _series_row_above(by_row, col, run[0][0], date1904)
    if header is None:
        return False
    caption_cell = _cell_at(by_row.get(header, []), col)
    caption = _text(caption_cell, date1904) if caption_cell is not None else None
    if not caption or is_calendar_hit(classify_header(caption)):
        return False
    return True


def _series_row_above(
    by_row: dict[int, list[dict]], col: int, first: int, date1904: bool
) -> int | None:
    """Nearest row above the first period that names series to its right.

    Empty rows and a unit row are skipped, so ``млн руб.`` under the names
    still leaves those names on the ruler. A real caption stops the search.
    """
    for row_n in range(first - 1, max(0, first - 1 - _SERIES_NEAR), -1):
        cells = by_row.get(row_n, [])
        if not cells or _is_empty_row(cells, date1904):
            continue
        if _names_right(cells, col, date1904):
            return row_n
        if _only_units(cells, date1904):
            continue
        return None
    return None


def _names_right(cells: list[dict], col: int, date1904: bool) -> list[str]:
    names: list[str] = []
    for cell in cells:
        if int(cell["col"]) <= col:
            continue
        text = _text(cell, date1904)
        if (
            text
            and classify_header(text) is None
            and not _is_number(text)
            and not is_unit_text(text)
        ):
            names.append(text.strip())
    return names


def _only_units(cells: list[dict], date1904: bool) -> bool:
    """True when every visible cell is a unit or a scale, and one is present."""
    seen = False
    for cell in cells:
        text = _text(cell, date1904)
        if not text:
            continue
        seen = True
        if not is_unit_text(text):
            return False
    return seen


def _values_beside(
    by_row: dict[int, list[dict]],
    col: int,
    run: list[tuple[int, str, str]],
    date1904: bool,
) -> bool:
    for row_n, _key, _shown in run:
        for cell in by_row.get(row_n, []):
            if int(cell["col"]) <= col:
                continue
            if cell.get("formula_raw"):
                return True
            shown = _text(cell, date1904)
            if shown and _is_number(shown):
                return True
    return False


def _vertical_axis(
    sheet: str, col: int, run: list[tuple[int, str, str]]
) -> tuple[list[int], TimeAxis]:
    raw = [key for _row, key, _text in run]
    grain = infer_grain(raw)
    # Month-ends and year-ends keep the day on the cell. The published key is
    # the period: ``2024-01-31`` is ``2024-01``, ``2021-12-31`` is ``2021``.
    keys = [apply_grain(key, grain) for key in raw]
    periods = [
        AxisPeriod(col=col, row=row_n, text=text, kind="period", period_key=key)
        for (row_n, _raw, text), key in zip(run, keys, strict=True)
    ]
    axis = TimeAxis(
        id=f"{sheet}!c{col}r{run[0][0]}",
        grain=grain,
        header_row=run[0][0],
        periods=periods,
        transposed=True,
    )
    return [row_n for row_n, _key, _text in run], axis
