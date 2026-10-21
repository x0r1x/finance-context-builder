"""Closed lists of format words. The parser does not stem and does not overlay.

Calendar words, month names, totals, stubs, units-as-headers and the scenario
lists that stage 3 still reads live here. A word matches whole. A longer word
matches only when the extra part is one grammatical ending from ``_INFLECTIONS``.
Words of at most three letters stay exact, so ``кв`` is not ``квота`` and ``г``
is not ``график``. There is no stemmer and no user lexicon file.
"""

from __future__ import annotations

import re
from functools import cache

_SHORT = 3
# Citation ending -> the other endings of that closed paradigm.
# ``ый``/``ой`` are adjectives (``базовый`` / ``базовое``). ``ий`` and ``ие`` are
# nouns (``сценарий`` / ``сценария``, ``полугодие`` / ``полугодия``).
_PARADIGMS: tuple[tuple[str, frozenset[str]], ...] = (
    ("ый", frozenset({"ый", "ая", "ое", "ого", "ому", "ым", "ую", "ые", "ых", "ыми"})),
    ("ой", frozenset({"ой", "ая", "ое", "ого", "ому", "ым", "ую", "ые", "ых", "ыми"})),
    ("ий", frozenset({"ий", "ия", "ию", "ием", "ии", "иев", "иям", "иях", "иями"})),
    ("ие", frozenset({"е", "я", "ю", "ем", "и"})),
)
_INFLECTIONS = frozenset(
    {
        "а",
        "я",
        "у",
        "ю",
        "е",
        "и",
        "ы",
        "о",
        "ой",
        "ий",
        "ый",
        "ом",
        "ем",
        "ов",
        "ев",
        "ах",
        "ях",
        "ам",
        "ям",
        "ами",
        "ями",
        "s",
        "es",
        "ed",
        "ing",
    }
)

_HEADER_ROLES: dict[str, tuple[str, ...]] = {
    "label": (
        "parameter",
        "parameters",
        "item",
        "check",
        "assumption",
        "assumptions",
        "параметр",
        "параметры",
        "параметры проекта",
        "показатель",
        "показатели",
        "статья",
        "статья затрат",
        "наименование",
        "наименование показателя",
        "допущение",
        "допущения",
        "предпосылка",
        "предпосылки",
        "проверка",
        "исходные данные",
    ),
    "value": (
        "value",
        "values",
        "active",
        "result",
        "chosen",
        "значение",
        "значения",
        "значение руб",
        "величина",
        "результат",
    ),
    "unit": (
        "unit",
        "units",
        "unit of measure",
        "uom",
        "ед",
        "ед изм",
        "ед измерения",
        "единица",
        "единицы",
        "единица изм",
        "единица измерения",
        "единицы измерения",
        "размерность",
    ),
    "note": (
        "note",
        "notes",
        "comment",
        "comments",
        "comment s",
        "source",
        "sources",
        "примечание",
        "примечания",
        "комментарий",
        "комментарии",
        "источник",
        "описание",
        "мероприятие",
        "мероприятия",
        "вывод",
        "выводы",
    ),
}

_PERIOD_WORDS: dict[str, tuple[str, ...]] = {
    "quarter": ("кв", "квартал", "quarter"),
    "half": ("полугодие", "half", "пол"),
    "year": ("г", "гг", "год", "года", "году"),
}

_TOTAL_WORDS = ("итого", "всего", "total", "sum", "итог")
_STUB_WORDS = ("stub", "частичный", "partial")

_SCENARIO = (
    "base",
    "base case",
    "upside",
    "downside",
    "stress",
    "негативный",
    "позитивный",
    "сценарий",
)
_SCENARIO_ADJECTIVES = (
    "базовый",
    "пессимистичный",
    "оптимистичный",
    "негативный",
    "позитивный",
    "консервативный",
    "стрессовый",
)
_SCENARIO_EVIDENCE = (
    "базовый",
    "консервативный",
    "оптимистичный",
    "пессимистичный",
    "стрессовый",
    "стресс",
    "сценарий",
    "base",
    "upside",
    "downside",
    "case",
    "scenario",
    "stress",
)
_SCENARIO_HEADER = ("case", "scenario")

# Citation forms. ``month_numbers`` adds the closed set of case endings.
_MONTH_CITATIONS: dict[str, tuple[str, ...]] = {
    "1": ("jan", "january", "янв", "январь"),
    "2": ("feb", "february", "фев", "февр", "февраль"),
    "3": ("mar", "march", "мар", "март"),
    "4": ("apr", "april", "апр", "апрель"),
    "5": ("may", "май"),
    "6": ("jun", "june", "июн", "июнь"),
    "7": ("jul", "july", "июл", "июль"),
    "8": ("aug", "august", "авг", "август"),
    "9": ("sep", "sept", "september", "сен", "сент", "сентябрь"),
    "10": ("oct", "october", "окт", "октябрь"),
    "11": ("nov", "november", "ноя", "нояб", "ноябрь"),
    "12": ("dec", "december", "дек", "декабрь"),
}

_QUARTER_LABEL = ("квартал", "квартальный", "quarter", "quarterly")
_MONTH_LABEL = ("месяц", "month")
_PERIOD_START = (
    "начало периода",
    "начала периода",
    "start of period",
    "period start",
)
_PERIOD_END = (
    "конец периода",
    "конца периода",
    "end of period",
    "period end",
)

# Surfaces that sit beside a date and are not period words. The residual
# peel rejects them. This is a closed list, not a stemmer block.
_NOT_PERIOD = frozenset(
    {
        "квт",
        "квота",
        "квот",
        "квм",
        "квартира",
        "график",
        "группа",
        "годовой",
        "based",
        "basement",
        "stressed",
        "quarterly",
    }
)

_LISTS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("header_roles", "label"): _HEADER_ROLES["label"],
    ("header_roles", "value"): _HEADER_ROLES["value"],
    ("header_roles", "unit"): _HEADER_ROLES["unit"],
    ("header_roles", "note"): _HEADER_ROLES["note"],
    ("period_words", "quarter"): _PERIOD_WORDS["quarter"],
    ("period_words", "half"): _PERIOD_WORDS["half"],
    ("period_words", "year"): _PERIOD_WORDS["year"],
    ("structural", "total"): _TOTAL_WORDS,
    ("structural", "stub"): _STUB_WORDS,
    ("structural", "scenario"): _SCENARIO,
    ("structural", "scenario_adjectives"): _SCENARIO_ADJECTIVES,
    ("structural", "scenario_evidence"): _SCENARIO_EVIDENCE,
    ("structural", "scenario_header"): _SCENARIO_HEADER,
    ("structural", "quarter_label"): _QUARTER_LABEL,
    ("structural", "month_label"): _MONTH_LABEL,
    ("structural", "period_start"): _PERIOD_START,
    ("structural", "period_end"): _PERIOD_END,
}


def data_token() -> str:
    """Stable token. Format words are code, so nothing outside the package restales them."""
    return "format-words"


def words(*path: str) -> tuple[str, ...]:
    return _LISTS[path]


def is_total_word(text: str | None) -> bool:
    """A total hint. Arithmetic, not this word, decides the column."""
    folded = _fold(text or "").strip(".")
    return bool(folded) and any(matches_entry(folded, entry) for entry in _TOTAL_WORDS)


def blocked_surface(surface: str) -> bool:
    """True when residual text is a known non-period lookalike."""
    folded = _fold(surface).strip(".")
    return folded in _NOT_PERIOD


def _fold(text: str) -> str:
    return text.casefold().replace("ё", "е")


def matches_entry(surface: str, entry: str) -> bool:
    """True when ``surface`` is ``entry``, or ``entry`` plus one listed ending."""
    token = _fold(surface).strip(".")
    root = _fold(entry).strip(".")
    if not token or not root:
        return False
    if token == root:
        return True
    if token in _NOT_PERIOD or " " in root or len(root) <= _SHORT:
        return False
    if token.startswith(root) and token[len(root) :] in _INFLECTIONS:
        return True
    return _same_paradigm(token, root)


def accepts(*path: str, surface: str) -> bool:
    return any(matches_entry(surface, entry) for entry in words(*path))


def _paradigm(root: str) -> tuple[str, frozenset[str]] | None:
    """Stem and endings when ``root`` is a citation form, not a bare suffix."""
    for ending, forms in _PARADIGMS:
        if not root.endswith(ending) or len(root) - len(ending) < 4:
            continue
        if ending == "ие":
            return root[:-1], forms
        return root[: -len(ending)], forms
    return None


def _same_paradigm(token: str, root: str) -> bool:
    found = _paradigm(root)
    if found is None or not token.startswith(found[0]):
        return False
    return token[len(found[0]) :] in found[1]


def _candidate(entry: str) -> str:
    root = _fold(entry)
    if " " in root or len(root) <= _SHORT:
        return re.escape(root)
    found = _paradigm(root)
    if found is not None:
        endings = "|".join(re.escape(item) for item in sorted(found[1], key=len, reverse=True))
        return rf"{re.escape(found[0])}(?:{endings})"
    endings = "|".join(re.escape(item) for item in sorted(_INFLECTIONS, key=len, reverse=True))
    return rf"{re.escape(root)}(?:{endings})?"


@cache
def _pattern(path: tuple[str, ...]) -> str:
    items = sorted(words(*path), key=len, reverse=True)
    alts = [_candidate(entry) for entry in items]
    return rf"(?:{'|'.join(alts)})(?![^\W\d_])"


def pattern(*path: str) -> str:
    """Candidate regex. Callers that embed it confirm the hit with ``accepts``."""
    return _pattern(path)


class _Hit:
    def __init__(self, text: str) -> None:
        self._text = text

    def group(self, index: int = 0) -> str:
        return self._text

    def __bool__(self) -> bool:
        return True


class _Searcher:
    def __init__(self, path: tuple[str, ...]) -> None:
        self.path = path

    def search(self, text: str) -> _Hit | None:
        folded = _fold(text)
        for entry in words(*self.path):
            root = _fold(entry)
            if " " not in root:
                continue
            if re.search(rf"(?<!\w){re.escape(root)}(?![^\W\d_])", folded):
                return _Hit(root)
        for match in re.finditer(rf"(?<!\w){_pattern(self.path)}", folded):
            if accepts(*self.path, surface=match.group(0)):
                return _Hit(match.group(0))
        return None


@cache
def _searcher(path: tuple[str, ...]) -> _Searcher:
    return _Searcher(path)


def searcher(*path: str) -> _Searcher:
    """Scan of one normalized label. Built once per word list."""
    return _searcher(path)


def header_role_map() -> dict[str, str]:
    return {word: role for role, items in _HEADER_ROLES.items() for word in items}


def month_numbers() -> dict[str, int]:
    return _months()


def _ru_cases(name: str) -> set[str]:
    folded = _fold(name)
    if folded.endswith(("ь", "й")):
        stem = folded[:-1]
        endings = ("я", "ю", "ем", "е")
    elif re.search(r"[а-я]", folded):
        stem = folded
        endings = ("а", "у", "ом", "е")
    else:
        return set()
    return {folded, *(f"{stem}{ending}" for ending in endings)}


@cache
def _months() -> dict[str, int]:
    found: dict[str, int] = {}
    for key, forms in _MONTH_CITATIONS.items():
        number = int(key)
        for form in forms:
            folded = _fold(form).rstrip(".")
            found[folded] = number
            if not _expand_month(folded):
                continue
            for case in _ru_cases(form):
                found.setdefault(case, number)
    return found


def _expand_month(folded: str) -> bool:
    """Citation forms inflect. A 3-letter abbreviation does not, except ``май``."""
    if not re.search(r"[а-я]", folded):
        return False
    if folded.endswith(("ь", "й")):
        return True
    return len(folded) >= 4


def _scenario_compact(text: str) -> str:
    folded = _fold(text).strip()
    folded = re.sub(r"\s*[:\-–—−]\s*", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def is_scenario_caption(text: str) -> bool:
    """A whole period header that names a case: ``Upside``, ``базовый сценарий``."""
    compact = _scenario_compact(text)
    for entry in words("structural", "scenario"):
        root = _fold(entry)
        if " " in root and compact == root:
            return True
        if " " not in root and matches_entry(compact, entry):
            return True
    parts = compact.split()
    if len(parts) != 2:
        return False
    if matches_entry(parts[0], "сценарий"):
        return True
    adjectives = words("structural", "scenario_adjectives")
    if not any(matches_entry(parts[0], item) for item in adjectives):
        return False
    return matches_entry(parts[1], "сценарий")


def has_scenario_evidence(text: str) -> bool:
    """Positive scenario wording. A bare short caption is not evidence."""
    folded = _fold(text)
    if is_scenario_caption(folded):
        return True
    for token in re.findall(r"[^\W\d_]+", folded):
        if any(matches_entry(token, entry) for entry in words("structural", "scenario_evidence")):
            return True
    return False
