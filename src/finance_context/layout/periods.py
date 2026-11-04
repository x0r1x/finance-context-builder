from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from functools import lru_cache

from finance_context.excel.dates import is_date_format, serial_to_date
from finance_context.layout.format_words import (
    HALF_WORDS,
    MONTH_LABEL_WORDS,
    PERIOD_END_WORDS,
    PERIOD_START_WORDS,
    QUARTER_LABEL_WORDS,
    QUARTER_WORDS,
    STUB_WORDS,
    YEAR_WORDS,
    accepts,
    blocked_surface,
    matches_entry,
    month_numbers,
    pattern,
    searcher,
)
from finance_context.layout.models import ColumnKind, PeriodHit

# One suffix class for a year and for a quarter, half, month or date of that year.
# E/F/P, Cyrillic Е/П/Ф, Cyrillic Р (the lookalike of P), and A/a/А are variant
# markers. They stay letters. The parser does not read them as fact or plan.
# П is the Cyrillic letter, not a P-lookalike.
_MARKER_SUFFIX = r"[EFAPеЕфФпПaAАаРр]"
_H_MARK = r"[HН]"
_YEAR_FLOAT = re.compile(r"^((?:19|20)\d{2})(?:\.0+)?$")
# Quarter and half-year headers: "1 кв. 2025", "I кв. 2025", "1-й квартал 2025", "Кв.1 2025",
# "Q1 2025", "1Q25", "Q2'25", "1 полугодие 2025", "H1 2025", "2H25". The year may be missing
# (a year row above) or two digits; "2025 г." is the same year.
_ORDINAL = r"(?:\s*[-–—−]?\s*(?:й|ый|ой|е|ое))?"
# "2025 Q1" / "2025Q1" / "2025 H1". A second year group cannot sit in the optional year.
_YEAR_FIRST_QUARTER = re.compile(
    rf"^(?P<year>(?:19|20)\d{{2}})\s*[-/]?\s*Q(?P<num>[1-4])(?P<sfx>{_MARKER_SUFFIX})?$",
    re.IGNORECASE,
)
_YEAR_FIRST_HALF = re.compile(
    rf"^(?P<year>(?:19|20)\d{{2}})\s*[-/]?\s*{_H_MARK}(?P<num>[12])(?P<sfx>{_MARKER_SUFFIX})?$",
    re.IGNORECASE,
)
_ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4}
_MONTH_YEAR = re.compile(
    rf"^(0[1-9]|1[0-2])[./]((?:19|20)\d{{2}})({_MARKER_SUFFIX})?$"
)
_FY_SHORT = re.compile(rf"^FY\s*(\d{{2}})({_MARKER_SUFFIX})?$", re.IGNORECASE)
_ISO_YMD = re.compile(
    rf"^((?:19|20)\d{{2}})-(\d{{2}})-(\d{{2}})({_MARKER_SUFFIX})?$"
)
_ISO_YM = re.compile(rf"^((?:19|20)\d{{2}})-(\d{{2}})({_MARKER_SUFFIX})?$")
_DATE_DMY = re.compile(
    rf"^([0-3]?\d)[./]([01]?\d)[./]((?:19|20)\d{{2}}|\d{{2}})({_MARKER_SUFFIX})?$"
)
_NAMED_MONTH = re.compile(
    rf"^([A-Za-zА-Яа-яёЁ]+)\.?\s*[-/.]?\s*((?:19|20)\d{{2}}|\d{{2}})({_MARKER_SUFFIX})?$",
    re.IGNORECASE,
)
_QUARTER_KEY = re.compile(r"^((?:19|20)\d{2})Q([1-4])$")
_HALF_KEY = re.compile(r"^((?:19|20)\d{2})H([12])$")
_MONTH_KEY = re.compile(r"^((?:19|20)\d{2})-(\d{2})$")
_YEAR_KEY = re.compile(r"^((?:19|20)\d{2})$")
_DATE_KEY = re.compile(r"^((?:19|20)\d{2})-(\d{2})-(\d{2})$")
_YEAR_WORD = rf"(?:\s*{pattern(YEAR_WORDS)}\.?)?"
_QUARTER_WORD = rf"{pattern(QUARTER_WORDS)}\.?"
_HALF_WORD = rf"{pattern(HALF_WORDS)}\.?"
_PART_YEAR = (
    rf"(?:(?:\s*[-/]\s*|\s*'?\s*)(?P<year>(?:19|20)\d{{2}}|\d{{2}})"
    rf"{_YEAR_WORD}(?P<sfx>{_MARKER_SUFFIX})?)?"
)
_YEAR = re.compile(
    rf"^(?:FY\s*)?((?:19|20)\d{{2}}){_YEAR_WORD}({_MARKER_SUFFIX})?$",
    re.IGNORECASE,
)
_CYRILLIC_HALF = re.compile(
    rf"^(?P<num>[12])\s*[Пп]\s*(?P<year>(?:19|20)\d{{2}}|\d{{2}})"
    rf"{_YEAR_WORD}(?P<sfx>{_MARKER_SUFFIX})?$",
    re.IGNORECASE,
)


def _compile_forms(bodies: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(rf"^{body}{_PART_YEAR}$", re.IGNORECASE) for body in bodies)


_QUARTER_WORD_FORMS = _compile_forms(
    (
        rf"(?P<num>[1-4]|IV|I{{1,3}}){_ORDINAL}\s*{_QUARTER_WORD}",
        rf"{_QUARTER_WORD}\s*(?P<num>[1-4]|IV|I{{1,3}})",
    )
)
_QUARTER_CODE_FORMS = _compile_forms((r"Q(?P<num>[1-4])", r"(?P<num>[1-4])\s*Q"))
_HALF_WORD_FORMS = _compile_forms(
    (
        rf"(?P<num>[12]|I{{1,2}}){_ORDINAL}\s*{_HALF_WORD}",
        rf"{_HALF_WORD}\s*(?P<num>[12]|I{{1,2}})",
    )
)
_HALF_CODE_FORMS = _compile_forms(
    (
        r"(?P<num>[12])\s*п\s*/\s*г\.?",
        rf"{_H_MARK}(?P<num>[12])",
        rf"(?P<num>[12])\s*{_H_MARK}",
    )
)


LAYER_DATE = "date"
LAYER_YEAR = "year"
LAYER_QUARTER = "quarter"
LAYER_MONTH = "month"
LAYER_HALF = "half"
LAYER_BOUNDS = "bounds"


@dataclass(frozen=True)
class HeaderAtom:
    kind: str
    year: str | None = None
    quarter: int | None = None
    month: int | None = None
    day: int | None = None
    column_kind: ColumnKind | None = None
    variant: str | None = None
    period_key: str | None = None
    explicit_role: bool = False
    half: int | None = None


def marker_token(variant: str | None) -> str:
    """Casefolded variant marker used in ``<period_key>@<marker>``.

    ``variant`` is the marker as recognized after ``normalize_header`` (NFKC,
    collapsed whitespace, lookalike folding on a period token). ``casefold``
    and ``ё``→``е`` are the further steps: ``Факт`` and ``факт`` share ``факт``,
    ``Расчёт`` and ``Расчет`` share ``расчет``, and a suffix ``E`` becomes ``e``.
    """
    if not variant:
        return ""
    return variant.casefold().replace("ё", "е")


# Lookalikes folded only inside a token that already contains a digit.
# Ф/П stay themselves: both are variant markers, and П is the Russian plan letter.
_LOOKALIKE_TO_LATIN = str.maketrans(
    {
        "А": "A",
        "а": "a",
        "В": "B",
        "в": "b",
        "С": "C",
        "с": "c",
        "Е": "E",
        "е": "e",
        "Н": "H",
        "н": "h",
        "К": "K",
        "к": "k",
        "М": "M",
        "м": "m",
        "О": "O",
        "о": "o",
        "Р": "P",
        "р": "p",
        "Т": "T",
        "т": "t",
        "Х": "X",
        "х": "x",
    }
)
_LOOKALIKE_TO_CYRILLIC = str.maketrans({value: key for key, value in _LOOKALIKE_TO_LATIN.items()})
_ORDINAL_TOKEN = re.compile(r"^[1-4]\s*[-–—−]?\s*(?:[еЕйЙ]|[оеОЕ]{1,2})$")
_MARKER_GLUE = re.compile(rf"((?:19|20)\d{{2}})\s+({_MARKER_SUFFIX})(?!\S)")


def _collapse_header(text: str | None) -> str | None:
    """NFKC, one space, joined digits. Lookalike folding comes after the prose check."""
    if text is None:
        return None
    raw = unicodedata.normalize("NFKC", str(text))
    raw = raw.replace("Ё", "Е").replace("ё", "е").replace("\xa0", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return None
    raw = _join_digits_inside_one_number(raw)
    return _MARKER_GLUE.sub(r"\1\2", raw)


def normalize_header(text: str | None) -> str | None:
    raw = _collapse_header(text)
    if not raw:
        return None
    return " ".join(_fold_period_token(token) for token in raw.split(" "))


# A finished dd.mm.yy / dd.mm.yyyy. The space after it belongs to the next date.
_SHORT_DATE_EDGE = re.compile(r"(?:^|[^\d./])\d{1,2}[./]\d{1,2}[./](?:\d{4}|\d{2})$")
_SHORT_DATE = re.compile(r"(?<!\d)\d{1,2}[./]\d{1,2}[./](?:\d{4}|\d{2})(?!\d)")


def _join_digits_inside_one_number(text: str) -> str:
    """Drop spaces that split one number. ``1.20 24`` becomes ``1.2024``.

    A finished short date keeps the gap, so ``01.02.24 03.04.24`` stays two dates.
    """

    def join(match: re.Match[str]) -> str:
        if _SHORT_DATE_EDGE.search(text[: match.start()]):
            return match.group(0)
        return ""

    return re.sub(r"(?<=\d)\s+(?=\d)", join, text)


def _has_two_short_dates(text: str) -> bool:
    """True when two pieces are real ``dd.mm.yy`` or ``dd.mm.yyyy`` dates."""
    found = 0
    for match in _SHORT_DATE.finditer(text):
        hit = _match_calendar_text(match.group(0))
        if hit is None or not is_calendar_key(hit.period_key):
            continue
        found += 1
        if found == 2:
            return True
    return False


def _fold_period_token(token: str) -> str:
    """Fold Н/H, А/A, Р/P, Е/E and the other lookalike pairs on a period code."""
    if _ORDINAL_TOKEN.match(token) or not re.search(r"\d", token):
        return token
    letters = [char for char in token if char.isalpha()]
    if not letters:
        return token
    cyrillic = [char for char in letters if "\u0400" <= char <= "\u04FF"]
    latin = [char for char in letters if char.isascii()]
    if len(letters) == 1:
        return token.translate(_LOOKALIKE_TO_LATIN)
    if cyrillic and latin and len(latin) == 1 and len(cyrillic) >= 2:
        return token.translate(_LOOKALIKE_TO_CYRILLIC)
    if cyrillic and latin and len(cyrillic) == 1 and len(latin) >= 2:
        return token.translate(_LOOKALIKE_TO_LATIN)
    return token


# Distinct normalized headers. A sheet asks for the same header from bands,
# rows and atoms, often thousands of times.
_CLASSIFY_CACHE = 1 << 16


# A repeated header is short. A unique row label is not, and caching it evicts
# the headers the next row looks up again.
_CLASSIFY_CACHED_LEN = 40


def _stable_letters(text: str) -> bool:
    """ASCII plus Cyrillic. NFKC does not change those, so the word split is final."""
    for char in text:
        code = ord(char)
        if code < 128 or 0x0400 <= code <= 0x04FF:
            continue
        return False
    return True


def _too_many_outside_words(text: str) -> bool:
    """The cheap form of ``_prose_blocks_calendar`` for an already-stable string.

    ``str.split`` collapses whitespace the way the header fold does. A token
    with a digit, or a month or quarter word, can sit in the date. The rest is
    the residual, and a residual stops at three words or 24 characters.
    """
    outside = 0
    length = 0
    for token in text.split():
        if _HAS_DIGIT.search(token) or "/" in token:
            continue
        stem = token.casefold().replace("ё", "е").strip(".,;:[]«»\"'()")
        if not stem or _calendar_stem(stem):
            continue
        outside += 1
        length += len(stem) + (outside > 1)
        if outside > 3 or length > 24:
            return True
    return False


def _prose_blocks_calendar(collapsed: str) -> bool:
    """True when the leftover words cannot be the short tail of one date.

    A hit is one calendar span plus a residual of at most three words and 24
    characters. A token that cannot sit in that span stays in the residual, so
    too many of them (or too long a tail) is not a header. Folding does not
    change this: a word without a digit is not a lookalike period code.
    """
    outside = [token for token in _scan_tokens(collapsed) if not _can_sit_in_calendar(token)]
    if len(outside) > 3:
        return True
    return len(_clean_residual(" ".join(outside))) > 24


def classify_header(text: str | None) -> PeriodHit | None:
    if isinstance(text, str) and _stable_letters(text) and _too_many_outside_words(text):
        return None
    collapsed = _collapse_header(text)
    if not collapsed or _prose_blocks_calendar(collapsed):
        return None
    raw = " ".join(_fold_period_token(token) for token in collapsed.split(" "))
    if len(raw) > _CLASSIFY_CACHED_LEN:
        return _match_header(raw)
    return _classify(raw)


def _match_header(raw: str) -> PeriodHit | None:
    """One normalized header. ``_classify`` stores the short ones."""
    if any(matches_entry(raw, entry) for entry in STUB_WORDS):
        return PeriodHit(kind="stub", period_key="stub", explicit_role=True)
    return _one_calendar(raw)


@lru_cache(maxsize=_CLASSIFY_CACHE)
def _classify(raw: str) -> PeriodHit | None:
    """``classify_header`` of one short normalized header."""
    return _match_header(raw)


# Every pattern reachable from _match_calendar_text requires a digit (a year or a date).
# test_calendar_match_requires_a_digit
_HAS_DIGIT = re.compile(r"\d")


def _match_calendar_text(raw: str) -> PeriodHit | None:
    """A string that is exactly one calendar date. A suffix letter stays on it."""
    if _HAS_DIGIT.search(raw) is None:
        return None
    iso_d = _ISO_YMD.fullmatch(raw)
    if iso_d:
        hit = _ymd_hit(
            int(iso_d.group(1)),
            int(iso_d.group(2)),
            int(iso_d.group(3)),
            iso_d.group(4),
        )
        if hit is not None:
            return hit
    iso = _ISO_YM.fullmatch(raw)
    if iso:
        month = int(iso.group(2))
        if 1 <= month <= 12:
            return _suffixed_hit(f"{iso.group(1)}-{month:02d}", iso.group(3))
    dmy = _DATE_DMY.fullmatch(raw)
    if dmy:
        year = _full_year(dmy.group(3))
        hit = _ymd_hit(
            int(year),
            int(dmy.group(2)),
            int(dmy.group(1)),
            dmy.group(4),
        )
        if hit is not None:
            return hit
    month_year = _MONTH_YEAR.fullmatch(raw)
    if month_year:
        return _suffixed_hit(
            f"{month_year.group(2)}-{month_year.group(1)}",
            month_year.group(3),
        )
    named = _month_hit(raw)
    if named is not None:
        return named
    part = _part_of_year(raw)
    if part is not None and part[2] is not None:
        grain, num, year, sfx = part
        return _suffixed_hit(f"{_full_year(year)}{grain}{num}", sfx)
    fy = _FY_SHORT.fullmatch(raw)
    if fy:
        return _suffixed_hit(_full_year(fy.group(1)), fy.group(2))
    year = _YEAR.fullmatch(raw)
    if year and _year_words_ok(raw):
        return _suffixed_hit(year.group(1), year.group(2))
    as_year = _YEAR_FLOAT.fullmatch(raw)
    if as_year:
        return PeriodHit(kind="period", period_key=as_year.group(1))
    return None


def unparsed_period_shape(text: str | None) -> bool:
    """A header that looks like a period and did not parse. Not a silent default."""
    raw = normalize_header(text)
    if not raw or len(raw) > 40 or classify_header(raw) is not None:
        return False
    if many_dates(raw):
        return True
    if re.fullmatch(r"(?:0?\d|1\d)[./](?:19|20)\d{2}", raw):
        return True
    if re.search(r"(?i)(?:^|\s)[QHН]\s*[0-9]|[0-9]\s*[QHН](?:\s|$)", raw):
        return True
    grain = re.search(
        r"(?i)(?<![^\W\d_])(?:q|h|кв|квартал\w*|полугод\w*)(?![^\W\d_])",
        raw,
    )
    return bool(grain and re.search(r"(?:19|20)\d{2}", raw))


# A fiscal year, a year span, or an N-month header. A bare year in other text is not one.
_UNPARSED_YEAR_SHAPE = re.compile(
    r"(?i)(?:fy\s*(?:19|20)?\d{2}(?:\s*/\s*\d{2,4})?"
    r"|(?:19|20)\d{2}\s*/\s*\d{2,4}"
    r"|\d{1,2}\s*[mм]\s*(?:19|20)\d{2})"
)
_MODEL_PERIOD = re.compile(
    r"(?:год|year|yr|период|period|квартал|quarter|месяц|month)\s*№?\s*\d{1,3}",
    re.IGNORECASE,
)


def period_like_header(text: str | None) -> bool:
    """A column header with a year or a model-period number that is not a period.

    ``9М 2025``, ``FY2025/26``, ``Estimate 2025``, ``2025/26``, ``Год 1``. A
    header that parses (``2025``, ``2025E``) is a period, not period-like.
    A year sitting in other text (``2025 график``, ``Model2024``) is not enough.
    """
    raw = normalize_header(text)
    if not raw or len(raw) > 40 or classify_header(raw) is not None:
        return False
    if unparsed_period_shape(raw) or _UNPARSED_YEAR_SHAPE.search(raw):
        return True
    return _MODEL_PERIOD.fullmatch(raw) is not None


# A token with no digit can still sit inside a date: a month name, a quarter
# or half or year word, a roman quarter, an ordinal, or the dash between them.
# ``п/г`` has neither a digit nor a word from those lists.
_CALENDAR_TOKEN = frozenset(
    {
        "fy",
        "q",
        "h",
        "н",
        "п",
        "i",
        "ii",
        "iii",
        "iv",
        "й",
        "ый",
        "ой",
        "е",
        "ое",
        "-",
        "–",
        "—",
        "−",
    }
)


@lru_cache(maxsize=1)
def _month_stems() -> frozenset[str]:
    return frozenset(month_numbers())


def _can_sit_in_calendar(token: str) -> bool:
    """True when ``token`` can occur inside one ``_match_calendar_text`` hit.

    A window that contains anything else cannot be that hit, so the scan
    does not try it. The check is the same word list the matcher confirms.
    Digits are not cached: a row number is unique, a word repeats.
    """
    if _HAS_DIGIT.search(token) or "/" in token:
        return True
    stem = token.casefold().replace("ё", "е").strip(".")
    return _calendar_stem(stem)


@lru_cache(maxsize=128)
def _calendar_stem(stem: str) -> bool:
    """Repeated prose words miss the date list once, then stay a lookup."""
    if stem in _CALENDAR_TOKEN or stem in _month_stems():
        return True
    return bool(stem) and (
        accepts(QUARTER_WORDS, stem)
        or accepts(HALF_WORDS, stem)
        or accepts(YEAR_WORDS, stem)
    )


def _one_calendar(raw: str) -> PeriodHit | None:
    """One date, plus any short residual around it. Two dates are a comparison."""
    tokens = _scan_tokens(raw)
    if not tokens:
        return None
    usable = [_can_sit_in_calendar(token) for token in tokens]
    # The whole string is a date only when every token can sit in one.
    if all(usable):
        direct = _match_calendar_text(raw)
        if direct is not None and is_calendar_key(direct.period_key):
            return direct
    spans: list[tuple[int, int, PeriodHit]] = []
    index = 0
    limit = len(tokens)
    while index < limit:
        if not usable[index]:
            index += 1
            continue
        end_limit = index + 1
        while end_limit < limit and usable[end_limit]:
            end_limit += 1
        last_digit = -1
        for cursor in range(index, end_limit):
            if _HAS_DIGIT.search(tokens[cursor]):
                last_digit = cursor
        # A run of words has no date. _match_calendar_text rejects it.
        if last_digit < index:
            index = end_limit
            continue
        found: tuple[int, int, PeriodHit] | None = None
        # ``end`` is exclusive and must keep the rightmost digit in the window.
        for end in range(end_limit, last_digit, -1):
            hit = _match_calendar_text(" ".join(tokens[index:end]))
            if hit is not None and is_calendar_key(hit.period_key):
                found = (index, end, hit)
                break
        if found is None:
            index += 1
            continue
        spans.append(found)
        index = found[1]
    if len(spans) != 1:
        return None
    start, end, hit = spans[0]
    residual = _clean_residual(" ".join(tokens[:start] + tokens[end:]))
    return _with_residual(hit, residual)


def _scan_tokens(raw: str) -> list[str]:
    text = re.sub(r"[()\[\]«»]", " ", raw)
    return [token for token in text.split() if token]


def _clean_residual(text: str) -> str:
    parts: list[str] = []
    for token in text.split():
        token = token.strip(".,;:[]«»\"'")
        if token:
            parts.append(token)
    return " ".join(parts)


def _residual_ok(text: str) -> bool:
    """A short leftover. A digit is a duration or a comparison, not a marker."""
    if not text or len(text) > 24 or len(text.split()) > 3:
        return False
    if re.search(r"\d", text):
        return False
    return not _residual_collides(text)


def _residual_collides(text: str) -> bool:
    """Calendar-word collisions and broken period codes are not tails.

    ``график``, ``полка`` and ``V кв`` stay out. ``факт`` does not: it is not a
    calendar word.
    """
    for token in re.findall(r"[^\W\d_]+", text):
        if blocked_surface(token):
            return True
        folded = token.casefold()
        if folded in {"q", "h"}:
            return True
        if any(accepts(entries, token) for entries in (YEAR_WORDS, QUARTER_WORDS, HALF_WORDS)):
            return True
        for root in _period_roots():
            if len(root) >= 3 and folded.startswith(root) and len(folded) > len(root):
                return True
    return False


def _period_roots() -> tuple[str, ...]:
    found: list[str] = []
    for entries in (YEAR_WORDS, QUARTER_WORDS, HALF_WORDS):
        for entry in entries:
            root = entry.casefold().strip(".")
            if root:
                found.append(root)
    return tuple(found)


def _with_residual(hit: PeriodHit, residual: str) -> PeriodHit | None:
    parts = [part for part in (hit.variant, residual) if part]
    text = _clean_residual(" ".join(parts))
    if residual and not _residual_ok(text):
        return None
    return PeriodHit(
        kind=hit.kind,
        period_key=hit.period_key,
        variant=text or None,
        explicit_role=bool(text),
    )


def many_dates(text: str | None) -> bool:
    """A comparison header: more than one date, so it is not a period key."""
    raw = normalize_header(text)
    if not raw:
        return False
    direct = _match_calendar_text(raw)
    if direct is not None and is_calendar_key(direct.period_key):
        return False
    if _has_two_short_dates(raw):
        return True
    tokens = _scan_tokens(raw)
    count = 0
    index = 0
    while index < len(tokens):
        found = 0
        for end in range(len(tokens), index, -1):
            hit = _match_calendar_text(" ".join(tokens[index:end]))
            if hit is not None and is_calendar_key(hit.period_key):
                found = end
                break
        if not found:
            index += 1
            continue
        count += 1
        index = found
    if count > 1:
        return True
    return len(re.findall(r"(?:19|20)\d{2}", raw)) > 1


def classify_atom(
    text: str | None,
    *,
    allow_quarter_num: bool = False,
    allow_month_num: bool = False,
) -> HeaderAtom | None:
    raw = normalize_header(text)
    if not raw:
        return None
    if _is_time_factor(raw):
        return HeaderAtom(kind="noise")
    hit = classify_header(raw)
    if hit is not None:
        parsed = parse_period_key(hit.period_key)
        half = _HALF_KEY.fullmatch(hit.period_key)
        return HeaderAtom(
            kind="full",
            year=parsed[0] if parsed else None,
            quarter=parsed[1] if parsed else None,
            month=parsed[2] if parsed else None,
            day=parsed[3] if parsed else None,
            column_kind=hit.kind,
            variant=hit.variant,
            period_key=hit.period_key,
            explicit_role=hit.explicit_role,
            half=int(half.group(2)) if half else None,
        )
    token = _part_token(raw)
    if token is not None:
        return token
    tailed = _part_with_residual(raw)
    if tailed is not None:
        return tailed
    if allow_quarter_num and _is_int_token(raw):
        num = int(raw)
        if 1 <= num <= 4:
            return HeaderAtom(kind="quarter_token", quarter=num)
    if allow_month_num and _is_int_token(raw):
        num = int(raw)
        if 1 <= num <= 12:
            return HeaderAtom(kind="month_num", month=num)
    return None


def compose_period(
    atoms: list[HeaderAtom],
    *,
    year_hint: str | None = None,
) -> PeriodHit | None:
    calendar = [atom for atom in atoms if atom.kind != "noise"]
    if not calendar:
        return None
    years = {atom.year for atom in calendar if atom.year}
    if len(years) > 1:
        return None
    structural = next(
        (
            atom
            for atom in calendar
            if atom.period_key in {"total", "stub"}
        ),
        None,
    )
    if structural is not None and structural.period_key:
        return PeriodHit(
            kind=structural.column_kind or "total",
            period_key=structural.period_key,
            variant=structural.variant,
            explicit_role=True,
        )
    temporal = [
        atom
        for atom in calendar
        if atom.kind != "marker"
        and (
            atom.kind in {"full", "quarter_token", "half_token", "month_num"}
            or atom.year
            or atom.quarter
            or atom.half
            or atom.month
        )
    ]
    if not temporal:
        return None
    mark = _compose_mark(calendar)
    date_atom = next(
        (atom for atom in calendar if atom.day is not None and atom.month and atom.year),
        None,
    )
    if date_atom is not None and date_atom.year and date_atom.month and date_atom.day:
        return _marked(
            f"{date_atom.year}-{date_atom.month:02d}-{date_atom.day:02d}",
            mark,
        )
    year = next(iter(years), None) or year_hint
    quarter = next((atom.quarter for atom in calendar if atom.quarter), None)
    half = next((atom.half for atom in calendar if atom.half), None)
    month = next((atom.month for atom in calendar if atom.month and atom.day is None), None)
    if year and quarter:
        return _marked(f"{year}Q{quarter}", mark)
    if year and half:
        return _marked(f"{year}H{half}", mark)
    if year and month:
        return _marked(f"{year}-{month:02d}", mark)
    full = next((atom for atom in calendar if atom.kind == "full" and atom.period_key), None)
    if full is not None and full.period_key and is_calendar_key(full.period_key):
        return _marked(full.period_key, mark)
    if year:
        return _marked(year, mark)
    return None


def is_calendar_key(key: str | None) -> bool:
    if not key:
        return False
    return bool(
        _YEAR_KEY.fullmatch(key)
        or _MONTH_KEY.fullmatch(key)
        or _DATE_KEY.fullmatch(key)
        or _QUARTER_KEY.fullmatch(key)
        or _HALF_KEY.fullmatch(key)
    )


_ORDINAL_KEY = re.compile(r"^([YQMP])(\d+)$")


def ordinal_position(key: str) -> tuple[str, int] | None:
    """Absolute index of an ordinal period key inside its letter grain.

    The axis parser emits only ``Y<n>``, ``Q<n>``, ``M<n>``, and ``P<n>``
    (a Year / Год / Quarter / Month / Period label picks the letter). The
    index is that number, so a missing step stays in the count. The letter is
    the grain: ``Y`` and ``Q`` do not share an index, and neither shares one
    with a calendar year.
    """
    if not key:
        return None
    match = _ORDINAL_KEY.fullmatch(key)
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def period_position(key: str) -> tuple[str, int] | None:
    """Grain and absolute index for a calendar or ordinal period key."""
    ordinal = ordinal_position(key)
    if ordinal is not None:
        return ordinal
    return calendar_position(key)


def calendar_position(key: str) -> tuple[str, int] | None:
    """Absolute index of a calendar key inside its grain.

    ``None`` when ``key`` is not a calendar period. The index is the year,
    ``2*year+half``, ``4*year+quarter``, ``12*year+month``, or the date's
    ordinal day. A missing period stays in the count. Two grains do not share
    an index.
    """
    if not key:
        return None
    year = _YEAR_KEY.fullmatch(key)
    if year:
        return "year", int(year.group(1))
    half = _HALF_KEY.fullmatch(key)
    if half:
        return "half", int(half.group(1)) * 2 + int(half.group(2))
    quarter = _QUARTER_KEY.fullmatch(key)
    if quarter:
        return "quarter", int(quarter.group(1)) * 4 + int(quarter.group(2))
    month = _MONTH_KEY.fullmatch(key)
    if month:
        return "month", int(month.group(1)) * 12 + int(month.group(2))
    day = _DATE_KEY.fullmatch(key)
    if day is None:
        return None
    try:
        ordinal = date(int(day.group(1)), int(day.group(2)), int(day.group(3))).toordinal()
    except ValueError:
        return None
    return "day", ordinal


def is_calendar_hit(hit: PeriodHit | None) -> bool:
    return hit is not None and is_calendar_key(hit.period_key)


def parse_period_key(key: str) -> tuple[str | None, int | None, int | None, int | None]:
    ymd = _DATE_KEY.fullmatch(key)
    if ymd:
        return ymd.group(1), None, int(ymd.group(2)), int(ymd.group(3))
    ym = _MONTH_KEY.fullmatch(key)
    if ym:
        return ym.group(1), None, int(ym.group(2)), None
    qk = _QUARTER_KEY.fullmatch(key)
    if qk:
        return qk.group(1), int(qk.group(2)), None, None
    yk = _YEAR_KEY.fullmatch(key)
    if yk:
        return yk.group(1), None, None, None
    hk = _HALF_KEY.fullmatch(key)
    if hk:
        return hk.group(1), None, None, None
    return None, None, None, None


def infer_grain(keys: list[str]) -> str | None:
    relative = _relative_grain(keys)
    if relative is not None:
        return relative
    calendar = [key for key in keys if is_calendar_key(key)]
    if len(calendar) < 2:
        return None
    if all(_HALF_KEY.fullmatch(key) for key in calendar):
        return "half_year"
    quarters = all(_QUARTER_KEY.fullmatch(key) for key in calendar)
    dates = [_key_as_date(key) for key in calendar]
    if all(item is not None for item in dates):
        ordered = sorted(zip(dates, calendar, strict=True), key=lambda item: item[0] or date.min)
        day_steps: list[int] = []
        month_steps: list[int] = []
        for (prev, _), (cur, _) in zip(ordered, ordered[1:], strict=False):
            if prev is None or cur is None:
                continue
            days = (cur - prev).days
            if days > 0:
                day_steps.append(days)
            delta = (cur.year - prev.year) * 12 + (cur.month - prev.month)
            if delta > 0:
                month_steps.append(delta)
        if day_steps:
            med_days = sorted(day_steps)[len(day_steps) // 2]
            if 5 <= med_days <= 9:
                return "week"
            if 13 <= med_days <= 17:
                return "biweek"
            if 25 <= med_days <= 35:
                return "month"
        if month_steps:
            unique = set(month_steps)
            if len(unique) == 1 or (max(month_steps) - min(month_steps) <= 1):
                med = sorted(month_steps)[len(month_steps) // 2]
                if med <= 1:
                    return "month"
                if 2 <= med <= 4:
                    return "quarter"
                if 5 <= med <= 7 and not quarters:
                    # Semiannual month ends become halves; sparse "2025Q1, 2025Q3" stay quarters.
                    return "half_year"
                if 11 <= med <= 13:
                    return "year"
            return None
    if all(_QUARTER_KEY.fullmatch(key) for key in calendar):
        return "quarter"
    if all(_MONTH_KEY.fullmatch(key) for key in calendar):
        return "month"
    if all(_YEAR_KEY.fullmatch(key) for key in calendar):
        return "year"
    return None


_RELATIVE_GRAIN = {
    "Y": "model_year",
    "Q": "model_quarter",
    "M": "model_month",
    "P": "model_period",
}


def _relative_grain(keys: list[str]) -> str | None:
    if not keys:
        return None
    grains: set[str] = set()
    for key in keys:
        if not key or key[0] not in _RELATIVE_GRAIN or not key[1:].isdigit():
            return None
        grains.add(_RELATIVE_GRAIN[key[0]])
    if len(grains) == 1:
        return next(iter(grains))
    return "model_period"


def apply_grain(key: str, grain: str | None) -> str:
    if not grain or not is_calendar_key(key):
        return key
    year, quarter, month, _day = parse_period_key(key)
    if year is None:
        return key
    if grain in {"week", "biweek"}:
        return key
    if grain == "year":
        return year
    if grain == "quarter":
        if quarter:
            return f"{year}Q{quarter}"
        if month:
            return f"{year}Q{(month - 1) // 3 + 1}"
        return key
    if grain == "month":
        if _DATE_KEY.fullmatch(key) or _MONTH_KEY.fullmatch(key):
            return f"{year}-{month:02d}" if month else key
        return key
    if grain == "half_year":
        if quarter:
            return f"{year}H{1 if quarter <= 2 else 2}"
        if month:
            return f"{year}H{1 if month <= 6 else 2}"
        return key
    return key


def display_cell_text(
    value: str | None,
    number_format: str | None,
    *,
    date1904: bool = False,
) -> str | None:
    if value is None:
        return None
    text = str(value)
    if not text.strip():
        return None
    if is_date_format(number_format):
        parsed = serial_to_date(text, date1904=date1904)
        if parsed is not None:
            return parsed.strftime("%d.%m.%Y")
    return text


def is_quarter_label(text: str | None) -> bool:
    return _label_has(text, QUARTER_LABEL_WORDS)


def is_month_label(text: str | None) -> bool:
    return _label_has(text, MONTH_LABEL_WORDS)


def is_start_period_label(text: str | None) -> bool:
    return _label_has(text, PERIOD_START_WORDS)


def is_end_period_label(text: str | None) -> bool:
    return _label_has(text, PERIOD_END_WORDS)


def _label_has(text: str | None, entries: tuple[str, ...]) -> bool:
    raw = normalize_header(text)
    return bool(raw and searcher(entries).search(raw))


def _year_words_ok(raw: str) -> bool:
    for token in re.findall(r"[^\W\d_]+", raw):
        if token.casefold() == "fy":
            continue
        if len(token) == 1 and re.fullmatch(_MARKER_SUFFIX, token, re.IGNORECASE):
            continue
        if not accepts(YEAR_WORDS, token):
            return False
    return True


def _part_token(raw: str) -> HeaderAtom | None:
    """A quarter or half with no year ("I кв.", "H2"); the year comes from a row above."""
    part = _part_of_year(raw)
    if part is None or part[2] is not None:
        return None
    if part[0] == "Q":
        return HeaderAtom(kind="quarter_token", quarter=part[1])
    return HeaderAtom(kind="half_token", half=part[1])


def _part_of_year(raw: str) -> tuple[str, int, str | None, str | None] | None:
    """Quarter ("Q") or half ("H") header: grain, its number, the year if any, a marker suffix."""
    if _PART_MARKER.search(raw) is None:
        return None
    cyr = _CYRILLIC_HALF.fullmatch(raw)
    if cyr is not None:
        return "H", int(cyr.group("num")), cyr.group("year"), cyr.group("sfx")
    for grain, form, top in (("Q", _YEAR_FIRST_QUARTER, 4), ("H", _YEAR_FIRST_HALF, 2)):
        match = form.fullmatch(raw)
        if match is not None:
            num = int(match.group("num"))
            if 1 <= num <= top:
                return grain, num, match.group("year"), match.group("sfx")
    found = _word_or_code_part(raw)
    if found is not None:
        return found
    return None


def _word_or_code_part(raw: str) -> tuple[str, int, str | None, str | None] | None:
    groups = (
        ("Q", _QUARTER_CODE_FORMS, 4, None),
        ("H", _HALF_CODE_FORMS, 2, None),
        ("Q", _QUARTER_WORD_FORMS, 4, QUARTER_WORDS),
        ("H", _HALF_WORD_FORMS, 2, HALF_WORDS),
    )
    for grain, forms, top, entries in groups:
        for form in forms:
            match = form.fullmatch(raw)
            if match is None:
                continue
            if entries is not None and not _confirmed(raw, entries):
                continue
            token = match.group("num").upper()
            num = _ROMAN[token] if token in _ROMAN else int(token)
            if 1 <= num <= top:
                return grain, num, match.group("year"), match.group("sfx")
    return None


def _confirmed(raw: str, entries: tuple[str, ...]) -> bool:
    return any(accepts(entries, token) for token in re.findall(r"[^\W\d_]+", raw))


def _month_hit(raw: str) -> PeriodHit | None:
    match = _NAMED_MONTH.fullmatch(raw)
    if match is None:
        return None
    month = _month_number(match.group(1))
    if month is None:
        return None
    year = _full_year(match.group(2))
    return _suffixed_hit(f"{year}-{month:02d}", match.group(3))


def _ymd_hit(year: int, month: int, day: int, suffix: str | None) -> PeriodHit | None:
    try:
        date(year, month, day)
    except ValueError:
        return None
    return _suffixed_hit(f"{year:04d}-{month:02d}-{day:02d}", suffix)


def _month_number(token: str) -> int | None:
    stem = token.casefold().replace("ё", "е").rstrip(".")
    return month_numbers().get(stem)


def _full_year(raw: str) -> str:
    if len(raw) == 2:
        return f"20{raw}"
    return raw


def _suffixed_hit(period_key: str, suffix: str | None) -> PeriodHit:
    return PeriodHit(
        kind="period",
        period_key=period_key,
        variant=suffix or None,
        explicit_role=bool(suffix),
    )


def _marked(period_key: str, mark: tuple[ColumnKind, str | None, bool]) -> PeriodHit:
    kind, variant, explicit = mark
    return PeriodHit(
        kind=kind,
        period_key=period_key,
        variant=variant,
        explicit_role=explicit,
    )


def _compose_mark(atoms: list[HeaderAtom]) -> tuple[ColumnKind, str | None, bool]:
    for atom in atoms:
        if atom.explicit_role and atom.variant and atom.column_kind in {"period", "stub", None}:
            return atom.column_kind or "period", atom.variant, True
    for atom in atoms:
        if atom.column_kind in {"total", "stub"}:
            return atom.column_kind, atom.variant, True
    return "period", None, False


# Every form in ``_part_of_year`` contains one of these. Prose that does not
# is not a quarter or a half, so the token-span scan has nothing to find.
_PART_MARKER = re.compile(
    r"q\s*[1-4]|[1-4]\s*q|[hн]\s*[12]|[12]\s*[hн]|кв|quarter|полугод|half|пол|[12]\s*п",
    re.IGNORECASE,
)


def _part_with_residual(raw: str) -> HeaderAtom | None:
    """``План I кв.`` keeps the quarter and the leftover text. The year comes later."""
    if _PART_MARKER.search(raw) is None:
        return None
    tokens = _scan_tokens(raw)
    if len(tokens) < 2:
        return None
    best: tuple[int, HeaderAtom] | None = None
    for start in range(len(tokens)):
        for end in range(len(tokens), start, -1):
            token = _part_token(" ".join(tokens[start:end]))
            if token is None:
                continue
            residual = _clean_residual(" ".join(tokens[:start] + tokens[end:]))
            if not _residual_ok(residual):
                continue
            if best is None or end - start > best[0]:
                best = (
                    end - start,
                    replace(
                        token,
                        column_kind="period",
                        variant=residual,
                        explicit_role=True,
                    ),
                )
            break
    return None if best is None else best[1]


def _key_as_date(key: str) -> date | None:
    half = _HALF_KEY.fullmatch(key)
    if half:
        return date(int(half.group(1)), 1 if half.group(2) == "1" else 7, 1)
    year, quarter, month, day = parse_period_key(key)
    if year is None:
        return None
    y = int(year)
    if day and month:
        try:
            return date(y, month, day)
        except ValueError:
            return None
    if month:
        try:
            return date(y, month, 1)
        except ValueError:
            return None
    if quarter:
        return date(y, (quarter - 1) * 3 + 1, 1)
    return date(y, 1, 1)


def _is_time_factor(raw: str) -> bool:
    from finance_context.layout.cells import parse_number

    value = parse_number(raw)
    return value is not None and 0 < value < 1


def _is_int_token(raw: str) -> bool:
    from finance_context.layout.cells import parse_int

    return parse_int(raw) is not None
