from __future__ import annotations

import re
from bisect import bisect_left
from collections import defaultdict
from itertools import pairwise
from typing import NamedTuple

from finance_context.formulas.engine import FormulaEngine
from finance_context.layout.cells import (
    _cell_at,
    _is_int_text,
    _is_number,
    _text,
    parse_int,
    parse_number,
)
from finance_context.layout.format_words import (
    LABEL_WORDS,
    NOTE_WORDS,
    UNIT_WORDS,
    VALUE_WORDS,
    accepts,
    is_entry_instruction,
)
from finance_context.layout.models import LayoutRow
from finance_context.layout.params import _is_index_header, attach_stub_cells
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
from finance_context.layout.units import annotated_unit_caption, is_unit_text

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
# Cached formula errors are not names. The formula text itself never is.
_EXCEL_ERROR = re.compile(
    r"^#(?:NULL!|DIV/0!|VALUE!|REF!|NAME\?|NUM!|N/A|GETTING_DATA|"
    r"SPILL!|CALC!|FIELD!|BLOCKED!|CONNECT!|UNKNOWN!|BUSY!)$",
    re.IGNORECASE,
)
_NAME_COLUMN_SKIP = {"index", "note", "unit", "value"}
_OKOF_CODE_QUALIFIER = re.compile(r"(?:^|[.:;\n])\s*код\s+по\s+окоф\s*\.?\s*$", re.IGNORECASE)
# A marker beside a real name is not part of that name. Alone, it still is.
_SERVICE_MARK = re.compile(
    r"^(?:факт|план|budget|forecast|actual|base(?:\s+case)?)$",
    re.IGNORECASE,
)
_PER_BASIS_CAPTION = re.compile(r"\bв\s+пересч[её]те\s+на\s+\S.*:\s*$", re.IGNORECASE)
_TOTAL_LABEL = {"total", "subtotal", "sub total", "sum", "итого", "всего"}
_NAMED_TOTAL = re.compile(r"^(?:итого|всего|total|sub\s*total)\s+(.+)$", re.IGNORECASE)
# Parents come from the parsed formula, not from the letters SUM in its text.
_AGG_ENGINES = (FormulaEngine(locale_hint="en"), FormulaEngine(locale_hint="ru"))
_SUM_FUNCS = {"sum", "сумм"}
_IF_FUNCS = {"if", "если"}
_AggregateSpans = tuple[tuple[int, int], ...]
_FORMULA_STRING_LITERAL = re.compile(r'"(?:[^"]|"")*"')


class _AggregateRows(NamedTuple):
    spans: _AggregateSpans = ()
    single_child: bool = False


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
    texts: list[str] = []
    for cell in row_cells:
        text = _text(cell, date1904)
        if not text:
            continue
        # A number is never a quarter token. Years still count as calendar hits.
        if _is_number(text):
            if is_calendar_hit(classify_header(text)):
                calendar += 1
                if not _is_bare_year_text(text):
                    bare_only = False
            continue
        texts.append(text)
        if not is_calendar_hit(classify_header(text)):
            continue
        calendar += 1
        if not _is_bare_year_text(text):
            bare_only = False
    # A row of "4Q" / "1 кв." is a header even when no year sits in the same cells.
    # Two tokens are a note. CFS keeps a run only from three periods.
    if calendar < 2:
        quarters = 0
        for text in texts:
            atom = classify_atom(text)
            if atom is not None and atom.kind == "quarter_token":
                quarters += 1
        return quarters >= 3
    # Dates, quarters and "2025E" are headers. Bare years are a header when the
    # run does not go backwards, or when that same forward run is copied sideways.
    if not bare_only:
        return True
    return _bare_years_are_header(row_cells, date1904)


def _is_quarter_token_row(row_cells: list[dict], date1904: bool) -> bool:
    """True when the row is quarter tokens such as ``4Q`` or ``1 кв.``."""
    quarters = 0
    for cell in row_cells:
        text = _text(cell, date1904)
        if not text or _is_number(text):
            if text and is_calendar_hit(classify_header(text)):
                return False
            continue
        if is_calendar_hit(classify_header(text)):
            return False
        atom = classify_atom(text)
        if atom is not None and atom.kind == "quarter_token":
            quarters += 1
    return quarters >= 3


def _is_year_banner_row(row_cells: list[dict], date1904: bool) -> bool:
    """Years above a quarter row. A line name left of the first year is not one."""
    years = 0
    first_year: int | None = None
    left_label = False
    for cell in sorted(row_cells, key=lambda item: int(item["col"])):
        text = _text(cell, date1904)
        if not text:
            continue
        col = int(cell["col"])
        if _is_bare_year_text(text):
            years += 1
            if first_year is None or col < first_year:
                first_year = col
            continue
        if _is_number(text):
            return False
        if first_year is None or col < first_year:
            left_label = True
    return years >= 1 and not left_label


def _distinct_year_ruler(row_cells: list[dict], date1904: bool) -> bool:
    """``2020, 2021, 2022, 2023``: each column is the next year.

    A banner repeats one year across the quarters under it, so a zero step
    stays out of this check. A merged year fills the columns it covers, so
    ``2024`` across two quarters is not a two-step ruler.
    """
    banner = _year_banner_by_column(row_cells, date1904)
    if len(banner) >= 2:
        values = [int(banner[col]) for col in sorted(banner)]
    else:
        values = _bare_year_values(row_cells, date1904)
    if not _year_run_is_header(values) or len(set(values)) < 2:
        return False
    steps = [right - left for left, right in zip(values, values[1:], strict=False)]
    # A banner repeats a year, so it has a zero step. A ruler steps every column.
    return bool(steps) and all(step > 0 for step in steps) and len(set(steps)) == 1


def _row_can_supply_year(row_cells: list[dict], date1904: bool) -> bool:
    """A year banner may hold labels. Another number makes it a data row."""
    saw = False
    for cell in row_cells:
        text = _text(cell, date1904)
        if not text:
            continue
        if _is_bare_year_text(text):
            saw = True
            continue
        if _is_number(text):
            return False
    return saw


def _repeated_year_line(row_cells: list[dict], date1904: bool) -> bool:
    """The same year beside a line name. A banner with no name there is a header."""
    if _is_year_banner_row(row_cells, date1904):
        return False
    values = _bare_year_values(row_cells, date1904)
    return len(values) >= 2 and len(set(values)) == 1


def _year_label_section(
    by_row: dict[int, list[dict]],
    header_row: int,
    period_cols: set[int],
    date1904: bool,
    years: set[str],
) -> LayoutRow | None:
    """The word left of the year that actually filled these quarters.

    A note between that word and the quarter row is not the section. A year
    above an ordinary ``2020–2023`` ruler is not one either: ``years`` is the
    set that went into the quarter keys.
    """
    if not period_cols or not years:
        return None
    left = min(period_cols)
    for dist in range(1, 4):
        for delta in (-dist, dist):
            row_n = header_row + delta
            cells = by_row.get(row_n)
            if not cells or not _row_can_supply_year(cells, date1904):
                continue
            found = {
                str(parse_int(text))
                for cell in cells
                if (text := _text(cell, date1904))
                and int(cell["col"]) in period_cols
                and _is_bare_year_text(text)
                and parse_int(text) is not None
            }
            if not found & years:
                continue
            label: str | None = None
            label_col: int | None = None
            for cell in cells:
                text = _text(cell, date1904)
                if not text or _is_number(text):
                    continue
                col = int(cell["col"])
                if col < left and (label_col is None or col > label_col):
                    label = text.strip()
                    label_col = col
            if label is None or label_col is None:
                return None
            return LayoutRow(row=row_n, label=label, kind="abstract", label_col=label_col)
    return None


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
    """Non-decreasing years, or the same forward run copied side by side.

    ``2022, 2022, 2023, 2023`` is a banner. ``2016, 2017, 2018`` is a ruler.
    ``2024…2029`` four times is four copies of one ruler. ``2074, 2035, 1988``
    is a data row.
    """
    if len(values) < 2:
        return False
    if _forward_year_run(values):
        return True
    return _repeated_year_copies(values)


def _forward_year_run(values: list[int]) -> bool:
    """One ruler. A positive step, when one exists, stays constant."""
    steps = [right - left for left, right in zip(values, values[1:], strict=False)]
    if any(step < 0 for step in steps):
        return False
    positive = {step for step in steps if step > 0}
    return len(positive) <= 1


def _repeated_year_copies(values: list[int]) -> bool:
    """True when ``values`` is the same forward ruler repeated across the row."""
    cut = next(
        (index for index in range(1, len(values)) if values[index] < values[index - 1]),
        None,
    )
    if cut is None or cut < 2 or len(values) % cut != 0:
        return False
    pattern = values[:cut]
    if not _forward_year_run(pattern):
        return False
    return all(values[index : index + cut] == pattern for index in range(cut, len(values), cut))


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


# A header that names the row. A longer heading ("Наименование показателя")
# still matches. "Name" alone does not: it collides with ordinary words.
_DEFAULT_NAME_HEADERS = ("наименование", "line item", "row name")


def _header_key(text: str) -> str:
    return " ".join(text.casefold().replace("ё", "е").split()).strip(" .:")


def _is_name_header(text: str, headers: tuple[str, ...]) -> bool:
    folded = _header_key(text)
    if not folded:
        return False
    for raw in headers:
        phrase = _header_key(raw)
        if phrase and (folded == phrase or folded.startswith(phrase + " ")):
            return True
    return False


def _name_header_column(
    by_row: dict[int, list[dict]],
    header_rows: set[int],
    period_cols: set[int],
    date1904: bool,
    *,
    col_floor: int = 0,
    name_headers: tuple[str, ...] | None = None,
) -> int | None:
    """Leftmost header left of the periods that names the row.

    A translation column («Наименование по-англ.») matches the same prefix and
    sits to the right. The row name is the first of those headers.

    ``name_headers`` replaces the default list. ``None`` keeps it. An empty
    tuple selects nothing, so the leftmost text column stays the label.
    """
    headers = _DEFAULT_NAME_HEADERS if name_headers is None else name_headers
    if not headers or not header_rows:
        return None
    left_edge = min(period_cols) if period_cols else 10**6
    found: list[int] = []
    for row_n in header_rows:
        for cell in by_row.get(row_n, ()):
            if cell.get("hidden"):
                continue
            col = int(cell["col"])
            if col <= col_floor or col >= left_edge or col in period_cols:
                continue
            text = _text(cell, date1904)
            if text and _is_name_header(text, headers):
                found.append(col)
    return min(found) if found else None


def _label_span(
    by_row: dict[int, list[dict]],
    body_rows: list[int],
    header_rows: set[int],
    period_cols: set[int],
    date1904: bool,
    *,
    col_floor: int = 0,
    column_roles: dict[int, str] | None = None,
    name_headers: tuple[str, ...] | None = None,
) -> tuple[int, list[int]]:
    left_edge = min(period_cols) if period_cols else 10**6
    leftmost: dict[int, int] = defaultdict(int)
    roles = column_roles or {}
    for row_n in body_rows:
        if row_n in header_rows:
            continue
        for cell in sorted(by_row[row_n], key=lambda item: int(item["col"])):
            if cell.get("hidden"):
                continue
            col = int(cell["col"])
            if col <= col_floor or col >= left_edge or col in period_cols:
                continue
            if _label_piece(cell, date1904) is None:
                continue
            if roles.get(col) in _NAME_COLUMN_SKIP:
                continue
            leftmost[col] += 1
            break
    if not leftmost:
        visible = [
            int(cell["col"])
            for row_n in body_rows
            for cell in by_row[row_n]
            if not cell.get("hidden")
            and roles.get(int(cell["col"])) not in _NAME_COLUMN_SKIP
        ]
        fallback = min(visible) if visible else 1
        return fallback, [] if roles.get(fallback) in _NAME_COLUMN_SKIP else [fallback]
    primary = max(leftmost.items(), key=lambda item: (item[1], -item[0]))[0]
    span = sorted(col for col in leftmost if col < primary)
    span.append(primary)
    # The leftmost text is often a type ("Коэффициент") filled on every row.
    # A header "Наименование" names the row; columns left of it stay in the
    # span so a section that only fills those columns still has a label.
    name_col = _name_header_column(
        by_row,
        header_rows,
        period_cols,
        date1904,
        col_floor=col_floor,
        name_headers=name_headers,
    )
    if name_col is None or roles.get(name_col) in _NAME_COLUMN_SKIP:
        return primary, span
    if name_col not in span:
        span = sorted([*span, name_col])
    # Only a name header to the right of the filled type column takes over.
    # One to the left stays a section column; the frequency column is the label.
    if name_col > primary:
        return name_col, span
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
    column_roles: dict[int, str] | None = None,
    name_headers: tuple[str, ...] | None = None,
    intervening_rulers: list[int] | None = None,
) -> list[LayoutRow]:
    revealed = reveal_rows or set()
    roles = column_roles or {}
    excluded_names = {col for col, role in roles.items() if role in _NAME_COLUMN_SKIP}
    unit_cols = {col for col, role in roles.items() if role == "unit"}
    note_cols = {col for col, role in roles.items() if role == "note"}
    index_cols = {col for col, role in roles.items() if role == "index"}
    label_cols = {col for col, role in roles.items() if role == "label"}
    extra_names = _extra_name_columns(
        by_row, band_rows, header_rows, label_span, period_cols, date1904, roles
    )
    name_col = _name_header_column(
        by_row,
        header_rows,
        period_cols,
        date1904,
        name_headers=name_headers,
    )
    out: list[LayoutRow] = []
    # Non-unit rows, indents strictly increasing. The nearest shallower row is
    # the top of this stack, so a flat table does not rescan every earlier row.
    parent_stack: list[LayoutRow] = []
    outlines: list[int] = []
    aggregated: list[_AggregateRows] = []
    units: list[bool] = []
    section_stack: list[LayoutRow] = [seed] if seed is not None else []
    previous_name_template: str | None = None
    trailing_col = max(period_cols) + 1 if period_cols else None
    segment_starts = [0]
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
        label, depth, label_cell, name_cols = _composed_label(
            by_row[row_n],
            _name_columns(label_span, extra_names, by_row[row_n]),
            date1904,
            include_hidden=reveal,
            span=label_span,
            prefer_col=name_col,
        )
        if label is None or _PER_BASIS_CAPTION.search(label):
            fallback = _left_of_axis_label(
                by_row[row_n],
                period_cols,
                date1904,
                include_hidden=reveal,
                excluded_cols=excluded_names,
                unit_cols=unit_cols,
            )
            fallback_label = fallback[0]
            if label is None or (fallback_label and not _PER_BASIS_CAPTION.search(fallback_label)):
                label, depth, label_cell, name_cols = fallback
        if label is None or label_cell is None:
            continue
        if out and intervening_rulers and out[-1].row + 1 < row_n:
            ruler = bisect_left(intervening_rulers, row_n)
            if ruler and out[-1].row < intervening_rulers[ruler - 1]:
                # A later ruler on the same columns interrupts this table.
                segment_starts.append(len(out))
                parent_stack.clear()
                section_stack.clear()
                previous_name_template = None
        name_cols.update(
            int(cell["col"])
            for cell in by_row[row_n]
            if (int(cell["col"]) in label_cols or int(cell["col"]) == name_col)
            and (cell.get("formula_raw") or cell.get("formula_template"))
            and (cell.get("cached_value") is None or not str(cell["cached_value"]).strip())
        )
        indent = depth + _indent(label) + _style_indent(label_cell)
        raw_template = label_cell.get("formula_template")
        name_template = _FORMULA_STRING_LITERAL.sub('""', raw_template) if raw_template else None
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
            name_cols=name_cols,
            note_cols=note_cols,
            index_cols=index_cols,
            by_row=by_row,
            floor_row=band_rows[0] if band_rows else None,
        )
        # Repeated label formulas also name unfilled inputs in the same list.
        # Use the preceding fact at this level as evidence for that run.
        if kind == "abstract" and (
            _has_data_stub(probe, by_row[row_n], date1904)
            or (
                out
                and name_template
                and name_template == previous_name_template
                and out[-1].kind == "fact"
                and out[-1].row + 1 == row_n
                and out[-1].label_col == int(label_cell["col"])
                and out[-1].indent == indent
            )
            or _is_blank_data_row(
                by_row[row_n],
                name_cols | note_cols | index_cols | unit_cols | _unit_stub_cols(probe),
                period_cols,
                date1904,
                has_unit=any(cell.role == "unit" for cell in probe.cells),
                trailing_col=trailing_col,
            )
        ):
            in_check = any(_CHECK.search(item.label) for item in section_stack) or any(
                prev.check_row for prev in _ancestors(out, indent, segment_starts[-1])
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
            and label.strip().casefold() in _TOTAL_LABEL
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
            name_cols=name_cols,
            note_cols=note_cols,
            index_cols=index_cols,
        )
        out.append(item)
        previous_name_template = name_template
        outlines.append(outline)
        aggregated.append(_aggregated_child_rows(by_row[row_n], period_cols, row_n))
        units.append(unit_caption)
        if not unit_caption:
            while parent_stack and parent_stack[-1].indent >= indent:
                parent_stack.pop()
            parent_stack.append(item)
        if kind == "abstract" and not is_unit_text(label):
            section_stack.append(item)
    segment_starts.append(len(out))
    for lo, hi in pairwise(segment_starts):
        _rewire_parents(
            out[lo:hi],
            outlines[lo:hi],
            summary_below,
            seed if lo == 0 else None,
            aggregated[lo:hi],
            units[lo:hi],
        )
    return out


def _rewire_parents(
    rows: list[LayoutRow],
    outlines: list[int],
    summary_below: bool,
    seed: LayoutRow | None = None,
    aggregated: list[_AggregateRows] | None = None,
    units: list[bool] | None = None,
) -> None:
    """Section paths come from abstract headers. ``kind`` does not decide parentage.

    A valued row stays a fact and can still own an explicit Excel group. One
    deeper indent is not enough: the formula has to aggregate that child. A
    bare «Прочие» is not a section.
    """
    deeper = _deeper_follower(rows, outlines)
    structural = [
        _is_structural_header(row, has_deeper=deeper[index])
        for index, row in enumerate(rows)
    ]
    section_stack: list[int] = []
    seed_on = seed is not None
    for index, row in enumerate(rows):
        indent = row.indent
        if is_unit_text(row.label):
            row.section_path = [
                *( [seed.label] if seed_on and seed is not None else [] ),
                *[rows[item].label for item in section_stack],
            ]
            row.parent_row = None
            continue
        if seed_on and seed is not None:
            drops = seed.indent >= indent if structural[index] else seed.indent > indent
            if drops:
                seed_on = False
        if structural[index]:
            while section_stack and rows[section_stack[-1]].indent >= indent:
                section_stack.pop()
        else:
            while section_stack and rows[section_stack[-1]].indent > indent:
                section_stack.pop()
        row.section_path = [
            *( [seed.label] if seed_on and seed is not None else [] ),
            *[rows[item].label for item in section_stack],
        ]
        parent_row = _stack_parent(rows, section_stack, indent)
        if parent_row is None and row.kind != "abstract":
            if section_stack:
                parent_row = rows[section_stack[-1]].row
            elif seed_on and seed is not None:
                parent_row = seed.row
        if (
            (section_stack or (seed_on and seed is not None))
            and row.label.strip().casefold() in _TOTAL_LABEL
            and (
                parent_row is None
                or parent_row in {rows[item].row for item in section_stack}
                or (seed is not None and parent_row == seed.row)
            )
        ):
            if section_stack:
                parent_row = rows[section_stack[-1]].row
            elif seed is not None:
                parent_row = seed.row
        row.parent_row = parent_row
        if structural[index]:
            section_stack.append(index)
    _apply_indent_aggregate_parents(
        rows, aggregated or [_AggregateRows()] * len(rows), units or [False for _ in rows]
    )
    if any(level > 0 for level in outlines):
        _apply_outline_parents(rows, outlines, summary_below)


def _deeper_follower(rows: list[LayoutRow], outlines: list[int]) -> list[bool]:
    """Whether the next row is nested by indent or by a deeper Excel outline.

    Only that next row matters. The tail is not copied.
    """
    count = len(rows)
    found: list[bool] = []
    for index, row in enumerate(rows):
        nxt = index + 1
        if nxt >= count:
            found.append(False)
            continue
        level = outlines[index] if index < len(outlines) else 0
        nxt_level = outlines[nxt] if nxt < len(outlines) else 0
        found.append(rows[nxt].indent > row.indent or nxt_level > level)
    return found


def _is_structural_header(row: LayoutRow, *, has_deeper: bool) -> bool:
    """Abstract section titles. A fact can still be a parent by outline or aggregate."""
    if row.kind != "abstract" or is_unit_text(row.label):
        return False
    if row.label.strip().casefold() == "прочие" and not has_deeper:
        return False
    return True


def _stack_parent(rows: list[LayoutRow], stack: list[int], indent: int) -> int | None:
    for index in reversed(stack):
        if rows[index].indent < indent:
            return rows[index].row
    return None


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


def _aggregated_child_rows(
    row_cells: list[dict], period_cols: set[int], row_n: int
) -> _AggregateRows:
    """Spans named by a vertical sum in a period column. A same-row total is not one."""
    sheet = str(next((cell.get("sheet") for cell in row_cells if cell.get("sheet")), ""))
    spans: list[tuple[int, int]] = []
    single_child = False
    for cell in row_cells:
        if int(cell["col"]) not in period_cols:
            continue
        formula = cell.get("formula_raw") or cell.get("formula_template")
        if not formula:
            continue
        aggregate = _vertical_aggregate_rows(str(formula), row_n, sheet, int(cell["col"]))
        spans.extend(aggregate.spans)
        single_child |= aggregate.single_child
    return _AggregateRows(tuple(spans), single_child)


def _vertical_aggregate_rows(
    formula: str, row_n: int, sheet: str, formula_col: int
) -> _AggregateRows:
    """Local rows a total owns, from the parsed formula.

    ``SUM`` inside an ``IF`` test is not the value. Every ``SUM`` argument in
    this period column counts, including a list of refs and a range beside a
    cell on another sheet. A sum down another column is a different table.
    A range stays a pair of bounds.
    """
    ast = _aggregate_ast(formula, sheet)
    if ast is None:
        return _AggregateRows()
    spans: list[tuple[int, int]] = []
    _collect_aggregate_spans(ast, sheet, row_n, formula_col, spans, plus=False)
    if _combines_rows(spans):
        return _AggregateRows(tuple(spans))
    # An explicit one-cell range can be a subtotal inside a larger roll-up.
    # Keep it separate from multi-row evidence and from aliases such as =C4.
    args = ast.get("args") or []
    single_child = (
        spans == [(row_n + 1, row_n + 1)]
        and ast.get("op") == "func"
        and str(ast.get("name") or "").casefold() in _SUM_FUNCS
        and len(args) == 1
        and isinstance(args[0], dict)
        and args[0].get("op") == "range"
    )
    return _AggregateRows(single_child=single_child)


def _combines_rows(spans: list[tuple[int, int]]) -> bool:
    """An aggregate combines distinct local rows, even with scalar adjustments."""
    return bool(spans) and min(lo for lo, _ in spans) < max(hi for _, hi in spans)


def _aggregate_ast(formula: str, sheet: str) -> dict | None:
    for engine in _AGG_ENGINES:
        parsed = engine.parse(formula, sheet=sheet, addr="A1")
        if parsed.ast is not None:
            return parsed.ast
    return None


def _collect_aggregate_spans(
    node: object,
    sheet: str,
    row_n: int,
    formula_col: int,
    spans: list[tuple[int, int]],
    *,
    plus: bool,
) -> None:
    """Value structure only. An ``IF`` condition is not part of the value."""
    if not isinstance(node, dict):
        return
    op = node.get("op")
    if op == "func":
        name = str(node.get("name") or "").casefold()
        args = node.get("args") or []
        if name in _IF_FUNCS:
            for arg in args[1:]:
                branch: list[tuple[int, int]] = []
                _collect_aggregate_spans(arg, sheet, row_n, formula_col, branch, plus=False)
                if _combines_rows(branch):
                    spans.extend(branch)
            return
        if name in _SUM_FUNCS:
            for arg in args:
                _collect_sum_argument(arg, sheet, row_n, formula_col, spans)
            return
        return
    if op == "bin" and node.get("kind") == "+":
        _collect_aggregate_spans(node.get("left"), sheet, row_n, formula_col, spans, plus=True)
        _collect_aggregate_spans(node.get("right"), sheet, row_n, formula_col, spans, plus=True)
        return
    if op == "unary":
        _collect_aggregate_spans(node.get("expr"), sheet, row_n, formula_col, spans, plus=False)
        return
    if plus:
        _append_local_span(node, sheet, row_n, formula_col, spans)


def _collect_sum_argument(
    node: object,
    sheet: str,
    row_n: int,
    formula_col: int,
    spans: list[tuple[int, int]],
) -> None:
    if not isinstance(node, dict):
        return
    if node.get("op") in {"ref", "range"}:
        _append_local_span(node, sheet, row_n, formula_col, spans)
        return
    _collect_aggregate_spans(node, sheet, row_n, formula_col, spans, plus=False)


def _append_local_span(
    node: dict,
    sheet: str,
    row_n: int,
    formula_col: int,
    spans: list[tuple[int, int]],
) -> None:
    if node.get("external"):
        return
    target = str(node.get("sheet") or sheet)
    if target.casefold() != sheet.casefold():
        return
    op = node.get("op")
    if op == "ref":
        child = node.get("row")
        if isinstance(child, int) and child != row_n and node.get("col") == formula_col:
            spans.append((child, child))
        return
    if op != "range" or node.get("col_only") or node.get("row_only"):
        return
    start = node.get("start") or {}
    end = node.get("end") or {}
    if not isinstance(start, dict) or not isinstance(end, dict):
        return
    if start.get("col") != formula_col or end.get("col") != formula_col:
        return
    if "row" not in start or "row" not in end:
        return
    lo = int(start["row"])
    hi = int(end["row"])
    if lo > hi:
        lo, hi = hi, lo
    if lo == hi == row_n:
        return
    spans.append((lo, hi))


def _span_contains(spans: _AggregateSpans, row_n: int) -> bool:
    return any(lo <= row_n <= hi for lo, hi in spans)


def _unit_stub_cols(row: LayoutRow) -> set[int]:
    """Unit columns already recognised on the row, including a formula stub."""
    return {cell.col for cell in row.cells if cell.role == "unit"}


def _fact_sections_with_named_totals(rows: list[LayoutRow], units: list[bool]) -> set[int]:
    """A valued section can open a run that closes with ``Total <section>``."""
    stack: list[int] = []
    sections: set[int] = set()
    for index in range(len(rows) - 1, -1, -1):
        if units[index]:
            continue
        row = rows[index]
        while stack and rows[stack[-1]].indent > row.indent:
            stack.pop()
        if stack and rows[stack[-1]].indent == row.indent:
            end = rows[stack.pop()]
            if row.kind == end.kind == "fact" and rows[index + 1].indent > row.indent:
                match = _NAMED_TOTAL.fullmatch(end.label.strip())
                title = row.label.strip().rstrip(":").casefold()
                if match and match[1].rstrip(":").casefold() == title:
                    sections.add(index)
        stack.append(index)
    return sections


def _apply_indent_aggregate_parents(
    rows: list[LayoutRow], aggregated: list[_AggregateRows], units: list[bool]
) -> None:
    """A fact parents a deeper row only when its formula aggregates that row.

    The nearest shallower row has to name the child. A single indent step, or a
    sum of some other rows, leaves the section parent in place. The stack is
    popped and pushed once per row. A one-child subtotal needs a containing
    multi-row aggregate and a single adjacent indented detail row. An explicit
    matching closing total also confirms a valued section's boundary.
    """
    stack: list[int] = []
    aggregate_children: set[int] = set()
    sections = _fact_sections_with_named_totals(rows, units)
    for index, row in enumerate(rows):
        if units[index]:
            continue
        while stack and rows[stack[-1]].indent >= row.indent:
            stack.pop()
        if stack:
            prev = stack[-1]
            earlier = rows[prev]
            if row.parent_row is None or earlier.row > row.parent_row:
                aggregate = aggregated[prev]
                if _span_contains(aggregate.spans, row.row):
                    row.parent_row = earlier.row
                    aggregate_children.add(index)
                elif (
                    prev in sections
                    or (
                        prev in aggregate_children
                        and aggregate.single_child
                        and row.row == earlier.row + 1
                        and (index + 1 == len(rows) or rows[index + 1].indent <= earlier.indent)
                    )
                ):
                    row.parent_row = earlier.row
        stack.append(index)


def _apply_outline_parents(
    rows: list[LayoutRow],
    levels: list[int],
    summary_below: bool,
) -> None:
    """A nested outline level hangs off the summary row inside the group.

    Level 1 under a level 0 section is the collapsible band indent already
    models, so that parent stays. Level 2 under level 1 (IaaS under CAPEX)
    replaces the indent parent when the summary sits closer. The summary keeps
    its own ``kind``: a CAPEX fact remains a fact and still owns the group.
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


def _ancestors(rows: list[LayoutRow], indent: int, start: int) -> list[LayoutRow]:
    """Nearest preceding row at each shallower indent: the row's outline parents."""
    found: list[LayoutRow] = []
    if indent == 0:
        return found
    level = indent
    for index in range(len(rows) - 1, start - 1, -1):
        prev = rows[index]
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
    label, depth, cell, _name_cols = _row_span_label(cells, label_span, date1904)
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


def _has_data_stub(probe: LayoutRow, row_cells: list[dict], date1904: bool) -> bool:
    """A scalar value or a line item's code can identify a fact with empty periods."""
    for item in probe.cells:
        if item.role not in {"value", "index"}:
            continue
        text = _text(_cell_at(row_cells, item.col) or {}, date1904)
        if text and (item.role == "index" or _is_number(text)):
            return True
    return False


def _is_blank_data_row(
    row_cells: list[dict],
    non_value_cols: set[int],
    period_cols: set[int],
    date1904: bool,
    *,
    has_unit: bool = False,
    trailing_col: int | None = None,
) -> bool:
    """A data line with nothing to show, not a section title.

    A formula beside the label (`=IF(ISERROR(E9),"",E9)` returning blank) or a dash
    placeholder (`-`) in a period column marks a list item whose values are empty.
    Name, note, index and unit columns cannot supply that evidence. A unit formula
    such as ``="тыс. руб."`` stays on the section. A numeric formula immediately
    after the axis can be a terminal result even when the series is empty.
    """
    for cell in row_cells:
        col = int(cell["col"])
        if col in non_value_cols:
            continue
        text = _text(cell, date1904) or ""
        if is_entry_instruction(text):
            if has_unit:
                return True
            continue
        if cell.get("formula_raw") and (not text or (col == trailing_col and _is_number(text))):
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
) -> tuple[str | None, int, dict | None, set[int]]:
    return _composed_label(
        row_cells,
        list(label_span),
        date1904,
        include_hidden=include_hidden,
        span=label_span,
    )


def _name_columns(
    label_span: list[int], extra: set[int], row_cells: list[dict]
) -> list[int]:
    """Span columns, plus a nearby per-row name that the span itself does not hold."""
    present = {int(cell["col"]) for cell in row_cells}
    return sorted(col for col in [*label_span, *extra] if col in present)


def _extra_name_columns(
    by_row: dict[int, list[dict]],
    band_rows: list[int],
    header_rows: set[int],
    label_span: list[int],
    period_cols: set[int],
    date1904: bool,
    column_roles: dict[int, str],
) -> set[int]:
    """A confirmed ``label`` column just to the right of the label span.

    ``Коэффициент`` plus the indicator sits under a ``Показатель`` header.
    A neighbour is not a name, including one note on a single row of a short
    table. A comment, a year caption and a far scenario column do not.
    """
    if not period_cols or not label_span:
        return set()
    left = min(period_cols)
    start = max(label_span) + 1
    limit = max(label_span) + 4
    found: dict[int, list[str]] = defaultdict(list)
    for row_n in band_rows:
        if row_n in header_rows:
            continue
        seen: set[int] = set()
        for cell in by_row[row_n]:
            col = int(cell["col"])
            if col < start or col > limit or col >= left or col in period_cols or col in seen:
                continue
            if cell.get("hidden") or column_roles.get(col) in _NAME_COLUMN_SKIP:
                continue
            text = _label_piece(cell, date1904)
            if text is None or is_entry_instruction(text) or _calendar_caption(text):
                continue
            seen.add(col)
            found[col].append(text.strip().casefold())
    return {col for col in found if column_roles.get(col) == "label"}


def _calendar_caption(text: str) -> bool:
    hit = classify_header(text)
    return is_calendar_hit(hit)


def _column_headers(
    by_row: dict[int, list[dict]],
    band_rows: list[int],
    owned_titles: set[int],
    period_cols: set[int],
    date1904: bool,
) -> tuple[set[int], dict[int, str]]:
    """Collect roles from the ruler and its column captions above the periods."""
    headers = set(band_rows)
    roles: dict[int, str] = {}
    if not period_cols:
        return headers, roles
    left = min(period_cols)
    top = min(band_rows)
    for row_n in sorted(headers | {row for row in owned_titles if row < top}, reverse=True):
        captions: dict[int, str] = {}
        for cell in by_row[row_n]:
            col = int(cell["col"])
            if col >= left or cell.get("hidden"):
                continue
            text = _text(cell, date1904)
            if text and not _is_number(text):
                captions[col] = text
        row_roles = {col: role for col, text in captions.items() if (role := _caption_role(text))}
        # A section title above the column captions remains a row of the table.
        if row_n < top and (len(captions) < 2 or not row_roles):
            break
        headers.add(row_n)
        for col, role in row_roles.items():
            roles.setdefault(col, role)
    return headers, roles


def _caption_role(text: str) -> str | None:
    if _OKOF_CODE_QUALIFIER.search(text):
        return "note"
    if _is_index_header(text):
        return "index"
    caption = re.sub(r"[\W_]+", " ", text).strip()
    for role, words in (
        ("note", (*NOTE_WORDS, "прим", "окоф", "код окоф", "окоф код")),
        ("unit", UNIT_WORDS),
        ("value", VALUE_WORDS),
        ("label", LABEL_WORDS),
    ):
        if accepts(words, caption):
            return role
    return None


def _left_of_axis_label(
    row_cells: list[dict],
    period_cols: set[int],
    date1904: bool,
    *,
    include_hidden: bool = False,
    excluded_cols: set[int],
    unit_cols: set[int],
) -> tuple[str | None, int, dict | None, set[int]]:
    """Text left of the period axis when the label span is empty."""
    if not period_cols:
        return None, 0, None, set()
    left_edge = min(period_cols)
    columns = sorted(
        {int(cell["col"]) for cell in row_cells if int(cell["col"]) < left_edge} - excluded_cols
    )
    label = _composed_label(
        row_cells,
        columns,
        date1904,
        include_hidden=include_hidden,
        span=None,
    )
    if label[0] is not None or not unit_cols:
        return label

    # A short continuation can put its names in the former unit column.
    # Require a period value and a single name candidate in those columns.
    candidates = [
        (cell, text)
        for cell in row_cells
        if int(cell["col"]) in unit_cols
        and (include_hidden or not cell.get("hidden"))
        and (text := _label_piece(cell, date1904)) is not None
        and not is_unit_text(text)
        and not is_entry_instruction(text)
        and not _SERVICE_MARK.fullmatch(text.strip())
    ]
    if len(candidates) == 1 and any(
        int(cell["col"]) in period_cols
        and (
            cell.get("formula_raw")
            or cell.get("formula_template")
            or _is_number(_text(cell, date1904) or "")
        )
        for cell in row_cells
    ):
        cell, text = candidates[0]
        return text, 0, cell, {int(cell["col"])}
    return label


def _composed_label(
    row_cells: list[dict],
    columns: list[int],
    date1904: bool,
    *,
    include_hidden: bool,
    span: list[int] | None,
    prefer_col: int | None = None,
) -> tuple[str | None, int, dict | None, set[int]]:
    """Join the useful name cells in ``columns``, left to right.

    A formula contributes its cached text. An empty cache, an Excel error and a
    numeric cache contribute nothing, and the formula string is never used.
    Units, periods and a service mark beside a real name stay out. Repeated
    text is kept once. The anchor cell is the leftmost piece, so indent does
    not jump to a nearer column.
    """
    pieces: list[tuple[str, dict, int]] = []
    piece_cols: dict[str, list[int]] = {}
    preceding = set()
    for depth, col in enumerate(columns):
        cell = _cell_at(row_cells, col)
        if cell is None or (cell.get("hidden") and not include_hidden):
            continue
        text = _label_piece(cell, date1904)
        if text is None:
            continue
        if span and prefer_col == col and col in span and not is_entry_instruction(text):
            # Filled type cells stay at the same level. An empty hierarchy
            # column leaves room for a section header on another row.
            indent = sum(c < col and c not in preceding for c in span)
            return text, indent, cell, {col}
        preceding.add(col)
        key = text.casefold()
        if key in piece_cols:
            piece_cols[key].append(col)
            continue
        piece_cols[key] = [col]
        pieces.append((text, cell, depth))
    if not pieces:
        return None, 0, None, set()
    content = [
        item
        for item in pieces
        if not is_unit_text(item[0]) and not _SERVICE_MARK.fullmatch(item[0].strip())
    ]
    names = [item for item in content if not is_entry_instruction(item[0])]
    if len(names) > 1 and not _PER_BASIS_CAPTION.search(names[-1][0]):
        # A basis caption can introduce several lines or sit beside one of
        # them. When the same row has a name to its right, use that name.
        names = [item for item in names if not _PER_BASIS_CAPTION.search(item[0])]
    units = [item for item in pieces if is_unit_text(item[0])]
    chosen = names or content or units or pieces
    anchor_text = chosen[0][0]
    lead = anchor_text[: len(anchor_text) - len(anchor_text.lstrip(" \t"))]
    body = " ".join(text.strip() for text, _cell, _depth in chosen)
    label = f"{lead}{body}"
    _text0, cell, depth = chosen[0]
    anchor = int(cell["col"])
    if span and anchor in span:
        depth = span.index(anchor)
    name_cols = {col for text, _cell, _depth in chosen for col in piece_cols[text.casefold()]}
    return label, depth, cell, name_cols


def _label_piece(cell: dict, date1904: bool) -> str | None:
    formula = cell.get("formula_raw") or cell.get("formula_template")
    cached = cell.get("cached_value")
    if formula:
        if cached is None or str(cached).strip() == "":
            return None
        text = _text(cell, date1904)
        if text is None or _EXCEL_ERROR.fullmatch(text.strip()):
            return None
        if _is_number(text):
            return None
    else:
        text = _text(cell, date1904)
    if (
        not text
        or _is_number(text)
        or annotated_unit_caption(text)
        or _is_column_header_text(text)
    ):
        return None
    return text


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
