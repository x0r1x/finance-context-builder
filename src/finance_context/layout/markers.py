"""Geometry that splits duplicate period keys. It does not classify fact or plan.

A marker exists only so two columns with the same calendar key can stay apart.
The suffix is the residual text itself (``2024@факт``). Nothing here decides
that the word means actual or plan.

Candidates:

- tail: text left after the one date in the header;
- banner: the row above the date row. A merged cell keeps its own span.
  Unmerged text fills right until the next text.

A candidate that fails a pitfall is dropped. A 0/1 flag switch or a change
from constants to formulas never creates a suffix. It only confirms a boundary
or picks between two candidates that disagree. An unresolved tie is a warning
and leaves the keys plain.
"""

from __future__ import annotations

import re
from collections import defaultdict

from finance_context.excel.a1 import col_to_index
from finance_context.layout.cells import _cell_at, _text
from finance_context.layout.models import AxisPeriod
from finance_context.layout.periods import is_calendar_key, marker_token, normalize_header
from finance_context.layout.units import is_unit_text

_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_SUM = re.compile(
    r"^=?\s*SUM\(\s*\$?([A-Z]{1,3})\$?(\d+)\s*:\s*\$?([A-Z]{1,3})\$?(\d+)\s*\)$",
    re.IGNORECASE,
)
_DIFF = re.compile(
    r"^=?\s*\$?([A-Z]{1,3})\$?(\d+)\s*-\s*\$?([A-Z]{1,3})\$?(\d+)\s*$",
    re.IGNORECASE,
)
_RATIO = re.compile(
    r"^=?\s*\(?\s*\$?([A-Z]{1,3})\$?(\d+)\s*/\s*\$?([A-Z]{1,3})\$?(\d+)\s*\)?\s*(?:-\s*1)?\s*$",
    re.IGNORECASE,
)
_SHARE = 0.6
_ABS_TOL = 1e-6
_REL_TOL = 1e-4


def apply_axis_markers(
    periods: list[AxisPeriod],
    *,
    sheet: str,
    header_row: int,
    by_row: dict[int, list[dict]],
    date1904: bool,
    banner_row: int | None = None,
) -> tuple[list[AxisPeriod], list[str]]:
    """Return periods whose variants are only the markers that earned a suffix."""
    if len(periods) < 2:
        return [_plain(period) for period in periods], []
    derived = derived_columns(periods, by_row, header_row, date1904)
    tails = _tails(periods, derived)
    tails = _strip_common(tails)
    banner = _banner_labels(
        periods,
        by_row,
        header_row if banner_row is None else banner_row + 1,
        date1904,
        derived,
    )
    tail_marks = _marks_for_duplicates(periods, tails)
    banner_marks = _marks_for_duplicates(periods, banner)
    chosen, ambiguous = _choose(periods, tail_marks, banner_marks, by_row, header_row, date1904)
    notes: list[str] = []
    if ambiguous:
        keys = ", ".join(sorted({periods[index].period_key for index in ambiguous}))
        notes.append(f"Ambiguous period marker on {sheet}!r{header_row}: {keys}")
    updated: list[AxisPeriod] = []
    for index, period in enumerate(periods):
        marker = chosen.get(index, "")
        if not marker or index in derived_index(periods, derived):
            updated.append(_plain(period))
            continue
        updated.append(period.model_copy(update={"variant": marker, "explicit_role": True}))
    return updated, notes


def derived_index(periods: list[AxisPeriod], derived: set[int]) -> set[int]:
    return {index for index, period in enumerate(periods) if period.col in derived}


def derived_columns(
    periods: list[AxisPeriod],
    by_row: dict[int, list[dict]],
    header_row: int,
    date1904: bool,
) -> set[int]:
    """Columns whose body is the arithmetic of neighbour columns.

    Formulas win: ``SUM`` of a neighbour span, ``Ci-Cj``, ``Ci/Cj`` or
    ``Ci/Cj-1``, on the same row. Without a formula, the cached value has to
    equal that arithmetic within tolerance on enough rows. A sum is the column
    to the right of its inputs, so the inverse (an input equalling the
    difference of the total and another input) is not itself derived. A
    derived column is never a marker.
    """
    cols = [period.col for period in periods]
    formula_hits = dict.fromkeys(cols, 0)
    formula_seen = dict.fromkeys(cols, 0)
    value_hits = dict.fromkeys(cols, 0)
    value_seen = dict.fromkeys(cols, 0)
    for row_n, cells in by_row.items():
        if row_n <= header_row:
            continue
        numbers = _row_numbers(cells, cols, date1904)
        valued = [col for col in cols if _has_formula(cells, col) is False and col in numbers]
        roles = _value_roles(cols, numbers) if numbers else set()
        for col in cols:
            if _has_formula(cells, col) is None:
                continue
            if _has_formula(cells, col):
                formula_seen[col] += 1
                cell = _cell_at(cells, col)
                formula = str(cell.get("formula_raw") or "") if cell else ""
                if formula and _formula_is_derived(formula, col, row_n, cols):
                    formula_hits[col] += 1
                continue
            if col not in valued:
                continue
            value_seen[col] += 1
            if col in roles:
                value_hits[col] += 1
    found: set[int] = set()
    for col in cols:
        if formula_seen[col] and formula_hits[col] / formula_seen[col] >= _SHARE:
            found.add(col)
        elif value_seen[col] >= 2 and value_hits[col] / value_seen[col] >= _SHARE:
            found.add(col)
    return found


def _has_formula(cells: list[dict], col: int) -> bool | None:
    """``None`` when the cell is absent. False when it is a pasted value."""
    cell = _cell_at(cells, col)
    if cell is None:
        return None
    return bool(cell.get("formula_raw"))


def _plain(period: AxisPeriod) -> AxisPeriod:
    if period.variant is None and not period.explicit_role:
        return period
    return period.model_copy(update={"variant": None, "explicit_role": False})


def _tails(periods: list[AxisPeriod], derived: set[int]) -> list[str]:
    tails: list[str] = []
    for period in periods:
        if period.col in derived or not is_calendar_key(period.period_key):
            tails.append("")
            continue
        tails.append(marker_token(period.variant))
    return tails


def _strip_common(labels: list[str]) -> list[str]:
    """Text shared by every column is a title, not a marker."""
    if not labels or any(not label for label in labels):
        return labels
    if len(set(labels)) == 1:
        return [""] * len(labels)
    return labels


def _banner_labels(
    periods: list[AxisPeriod],
    by_row: dict[int, list[dict]],
    header_row: int,
    date1904: bool,
    derived: set[int],
) -> list[str]:
    row = by_row.get(header_row - 1)
    if not row:
        return [""] * len(periods)
    cols = [period.col for period in periods]
    assigned = _fill_banner(row, cols, date1904)
    if _units_row(assigned.values()) or _grain_parent(assigned, periods):
        return [""] * len(periods)
    for period in periods:
        if period.col in derived:
            assigned[period.col] = ""
    labels = [marker_token(assigned.get(period.col)) for period in periods]
    if not _banner_groups_ok(cols, labels, periods):
        return [""] * len(periods)
    return _strip_common(labels)


def _fill_banner(row_cells: list[dict], axis_cols: list[int], date1904: bool) -> dict[int, str]:
    """Merged text stays inside the merge. Unmerged text runs to the next text."""
    anchors: list[tuple[int, str, int | None]] = []
    for cell in sorted(row_cells, key=lambda item: int(item["col"])):
        text = normalize_header(_text(cell, date1904))
        if not text:
            continue
        end = cell.get("merge_end_col")
        anchors.append((int(cell["col"]), text, int(end) if end else None))
    assigned: dict[int, str] = {}
    if not anchors or not axis_cols:
        return assigned
    last_axis = max(axis_cols)
    first_axis = min(axis_cols)
    anchors = [anchor for anchor in anchors if (anchor[2] or anchor[0]) >= first_axis]
    for index, (col, text, end) in enumerate(anchors):
        if end is not None:
            stop = end
        elif index + 1 < len(anchors):
            stop = anchors[index + 1][0] - 1
        else:
            stop = last_axis
        for axis_col in axis_cols:
            if col <= axis_col <= stop:
                assigned[axis_col] = text
    return assigned


def _units_row(texts) -> bool:
    found = [text for text in texts if text]
    return bool(found) and all(is_unit_text(text) for text in found)


def _grain_parent(labels: dict[int, str], periods: list[AxisPeriod]) -> bool:
    """A year row that contains the periods under it is their parent, not a marker."""
    seen = 0
    for period in periods:
        text = labels.get(period.col)
        if not text:
            continue
        seen += 1
        if _YEAR.fullmatch(text) is None:
            return False
        if not period.period_key.startswith(text):
            return False
    return seen >= 2


def _banner_groups_ok(cols: list[int], labels: list[str], periods: list[AxisPeriod]) -> bool:
    groups = _groups(list(zip(cols, labels, strict=True)))
    if len(groups) < 2:
        return False
    return _separates_duplicates(periods, labels)


def _groups(pairs: list[tuple[int, str]]) -> list[tuple[str, int]]:
    groups: list[tuple[str, int]] = []
    for _col, label in pairs:
        if not label:
            continue
        if groups and groups[-1][0] == label:
            groups[-1] = (label, groups[-1][1] + 1)
            continue
        groups.append((label, 1))
    return groups


def _separates_duplicates(periods: list[AxisPeriod], labels: list[str]) -> bool:
    by_key: dict[str, set[str]] = defaultdict(set)
    for period, label in zip(periods, labels, strict=True):
        if not is_calendar_key(period.period_key):
            continue
        by_key[period.period_key].add(label)
    return any(len(values) >= 2 for values in by_key.values())


def _marks_for_duplicates(periods: list[AxisPeriod], labels: list[str]) -> dict[int, str]:
    """Keep labels that form blocks or a repeat inside each duplicated date."""
    grouped: dict[str, list[int]] = defaultdict(list)
    for index, period in enumerate(periods):
        if is_calendar_key(period.period_key):
            grouped[period.period_key].append(index)
    accepted: dict[int, str] = {}
    for indexes in grouped.values():
        if len(indexes) < 2:
            continue
        tokens = [labels[index] if index < len(labels) else "" for index in indexes]
        if len(set(tokens)) < 2 or not _pattern_ok(tokens):
            continue
        for index, token in zip(indexes, tokens, strict=True):
            if token:
                accepted[index] = token
    return accepted


def _pattern_ok(tokens: list[str]) -> bool:
    return _contiguous(tokens) or _repeating(tokens)


def _contiguous(tokens: list[str]) -> bool:
    """Each distinct label occupies one run. ``факт факт план план`` does."""
    last_at: dict[str, int] = {}
    for index, token in enumerate(tokens):
        previous = last_at.get(token)
        if previous is not None and previous != index - 1:
            return False
        last_at[token] = index
    return len(set(tokens)) >= 2


def _repeating(tokens: list[str]) -> bool:
    """``факт план факт план`` repeats. A broken cycle does not."""
    count = len(tokens)
    for period in range(1, count // 2 + 1):
        if count % period:
            continue
        block = tokens[:period]
        if len(set(block)) < 2:
            continue
        if block * (count // period) == tokens:
            return True
    return False


def _choose(
    periods: list[AxisPeriod],
    tails: dict[int, str],
    banners: dict[int, str],
    by_row: dict[int, list[dict]],
    header_row: int,
    date1904: bool,
) -> tuple[dict[int, str], set[int]]:
    if not tails and not banners:
        return {}, set()
    if not _conflict(periods, tails, banners):
        return _merge(tails, banners), set()
    signals = _flag_switches(periods, by_row, header_row, date1904)
    signals.extend(_profile_switches(periods, by_row, header_row, date1904))
    tail_hit = _confirmed(_boundaries(periods, tails), signals)
    banner_hit = _confirmed(_boundaries(periods, banners), signals)
    if tail_hit and not banner_hit:
        return tails, set()
    if banner_hit and not tail_hit:
        return banners, set()
    return {}, set(_conflict_indexes(periods, tails, banners))


def _conflict(periods: list[AxisPeriod], tails: dict[int, str], banners: dict[int, str]) -> bool:
    return bool(_conflict_indexes(periods, tails, banners))


def _conflict_indexes(
    periods: list[AxisPeriod], tails: dict[int, str], banners: dict[int, str]
) -> set[int]:
    found: set[int] = set()
    for index, period in enumerate(periods):
        if not is_calendar_key(period.period_key):
            continue
        left = tails.get(index, "")
        right = banners.get(index, "")
        if left and right and left != right:
            found.add(index)
    return found


def _merge(tails: dict[int, str], banners: dict[int, str]) -> dict[int, str]:
    merged = dict(tails)
    for index, token in banners.items():
        if token:
            merged[index] = token
    return merged


def _boundaries(periods: list[AxisPeriod], marks: dict[int, str]) -> set[int]:
    bounds: set[int] = set()
    previous = ""
    started = False
    for index, period in enumerate(periods):
        token = marks.get(index, "")
        if started and token != previous:
            bounds.add(period.col)
        previous = token
        started = True
    return bounds


def _confirmed(bounds: set[int], signals: list[set[int]]) -> bool:
    return any(bounds and bounds == signal for signal in signals)


def _flag_switches(
    periods: list[AxisPeriod],
    by_row: dict[int, list[dict]],
    header_row: int,
    date1904: bool,
) -> list[set[int]]:
    cols = [period.col for period in periods]
    found: list[set[int]] = []
    for row_n in sorted(by_row):
        if row_n <= header_row:
            continue
        bits: list[int] = []
        usable = True
        for col in cols:
            cell = _cell_at(by_row[row_n], col)
            number = _number(_text(cell, date1904) if cell else None)
            if number not in {0.0, 1.0}:
                usable = False
                break
            bits.append(int(number))
        if not usable or len(set(bits)) < 2:
            continue
        bounds = {cols[index] for index in range(1, len(bits)) if bits[index] != bits[index - 1]}
        if bounds:
            found.append(bounds)
    return found


def _profile_switches(
    periods: list[AxisPeriod],
    by_row: dict[int, list[dict]],
    header_row: int,
    date1904: bool,
) -> list[set[int]]:
    profiles = [_column_profile(period.col, by_row, header_row, date1904) for period in periods]
    if any(not profile for profile in profiles) or len(set(profiles)) < 2:
        return []
    bounds = {
        periods[index].col
        for index in range(1, len(profiles))
        if profiles[index] != profiles[index - 1]
    }
    return [bounds] if bounds else []


def _column_profile(
    col: int, by_row: dict[int, list[dict]], header_row: int, date1904: bool
) -> str:
    formulas = 0
    constants = 0
    for row_n, cells in by_row.items():
        if row_n <= header_row:
            continue
        cell = _cell_at(cells, col)
        if cell is None:
            continue
        if cell.get("formula_raw") or cell.get("formula_template"):
            formulas += 1
            continue
        if _number(_text(cell, date1904)) is not None:
            constants += 1
    if formulas == 0 and constants == 0:
        return ""
    return "formula" if formulas > constants else "constant"


def _formula_is_derived(formula: str, col: int, row: int, cols: list[int]) -> bool:
    text = formula.strip()
    summed = _SUM.fullmatch(text)
    if summed:
        start = col_to_index(summed.group(1))
        end = col_to_index(summed.group(3))
        if int(summed.group(2)) != row or int(summed.group(4)) != row:
            return False
        span = set(range(min(start, end), max(start, end) + 1))
        neighbours = [item for item in cols if item != col]
        return col not in span and span.issubset(neighbours) and len(span) >= 2
    diff = _DIFF.fullmatch(text)
    if diff:
        return _pair_is_neighbours(
            diff.group(1), diff.group(2), diff.group(3), diff.group(4), col, row, cols
        )
    ratio = _RATIO.fullmatch(text)
    if ratio:
        return _pair_is_neighbours(
            ratio.group(1), ratio.group(2), ratio.group(3), ratio.group(4), col, row, cols
        )
    return False


def _pair_is_neighbours(
    left_col: str,
    left_row: str,
    right_col: str,
    right_row: str,
    col: int,
    row: int,
    cols: list[int],
) -> bool:
    if int(left_row) != row or int(right_row) != row:
        return False
    left = col_to_index(left_col)
    right = col_to_index(right_col)
    if left == col or right == col:
        return False
    if left not in cols or right not in cols:
        return False
    return abs(cols.index(left) - cols.index(right)) == 1


def _row_numbers(cells: list[dict], cols: list[int], date1904: bool) -> dict[int, float]:
    found: dict[int, float] = {}
    for col in cols:
        cell = _cell_at(cells, col)
        if cell is None or cell.get("formula_raw"):
            continue
        number = _number(_text(cell, date1904))
        if number is not None:
            found[col] = number
    if found and len(found) == len(cols) and set(found.values()) <= {0.0, 1.0}:
        return {}
    return found


def _value_roles(cols: list[int], numbers: dict[int, float]) -> set[int]:
    """Columns explained as a sum of the columns to their left, or a difference.

    An input of a sum is not also marked derived when it equals that sum minus
    another input.
    """
    sums: dict[int, set[int]] = {}
    diffs: dict[int, set[int]] = {}
    for col in cols:
        if col not in numbers:
            continue
        added = _left_sum_operands(col, cols, numbers)
        if added:
            sums[col] = added
            continue
        subtracted = _diff_operands(col, cols, numbers)
        if subtracted:
            diffs[col] = subtracted
    addends: set[int] = set()
    for operands in sums.values():
        addends |= operands
    derived = set(sums)
    for col in diffs:
        if col not in addends:
            derived.add(col)
    return derived


def _left_sum_operands(col: int, cols: list[int], numbers: dict[int, float]) -> set[int] | None:
    index = cols.index(col)
    span: list[int] = []
    for item in reversed(cols[:index]):
        if item not in numbers:
            break
        span.append(item)
    if len(span) < 2:
        return None
    if _close(numbers[col], sum(numbers[item] for item in span)):
        return set(span)
    return None


def _diff_operands(col: int, cols: list[int], numbers: dict[int, float]) -> set[int] | None:
    index = cols.index(col)
    own = numbers[col]
    if index >= 2:
        left_col, right_col = cols[index - 2], cols[index - 1]
        if left_col in numbers and right_col in numbers:
            if _diff_or_ratio(own, numbers[left_col], numbers[right_col]):
                return {left_col, right_col}
    if 0 < index < len(cols) - 1:
        left_col, right_col = cols[index - 1], cols[index + 1]
        if left_col in numbers and right_col in numbers:
            if _diff_or_ratio(own, numbers[left_col], numbers[right_col]):
                return {left_col, right_col}
    return None


def _diff_or_ratio(own: float, left: float, right: float) -> bool:
    if _close(own, left - right) or _close(own, right - left):
        return True
    if right and _close(own, left / right - 1):
        return True
    return bool(left) and _close(own, right / left - 1)


def _number(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text.replace("\xa0", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _close(left: float, right: float) -> bool:
    scale = max(abs(left), abs(right), 1.0)
    return abs(left - right) <= max(_ABS_TOL, _REL_TOL * scale)
