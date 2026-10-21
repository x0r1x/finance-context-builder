"""Closed lists of format words.

Calendar words, month names, totals, stubs and header-role hints live here.
A word matches whole. A longer word matches only when the extra part is one
grammatical ending from ``_INFLECTIONS`` or one closed paradigm. Words of at
most three letters stay exact, so ``кв`` is not ``квота`` and ``г`` is not
``график``.
"""

from __future__ import annotations

import re
from functools import cache

_SHORT = 3
# Citation ending -> the other endings of that closed paradigm.
# ``ый``/``ой`` are adjectives (``квартальный`` / ``квартальное``). ``ий`` and ``ие`` are
# nouns (``комментарий`` / ``комментария``, ``полугодие`` / ``полугодия``).
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

LABEL_WORDS: tuple[str, ...] = (
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
)
VALUE_WORDS: tuple[str, ...] = (
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
)
UNIT_WORDS: tuple[str, ...] = (
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
)
NOTE_WORDS: tuple[str, ...] = (
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
)

_HEADER_ROLES: dict[str, tuple[str, ...]] = {
    "label": LABEL_WORDS,
    "value": VALUE_WORDS,
    "unit": UNIT_WORDS,
    "note": NOTE_WORDS,
}

QUARTER_WORDS = ("кв", "квартал", "quarter")
HALF_WORDS = ("полугодие", "half", "пол")
YEAR_WORDS = ("г", "гг", "год", "года", "году")
TOTAL_WORDS = ("итого", "всего", "total", "sum", "итог")
STUB_WORDS = ("stub", "частичный", "partial")

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

QUARTER_LABEL_WORDS = ("квартал", "квартальный", "quarter", "quarterly")
MONTH_LABEL_WORDS = ("месяц", "month")
PERIOD_START_WORDS = (
    "начало периода",
    "начала периода",
    "start of period",
    "period start",
)
PERIOD_END_WORDS = (
    "конец периода",
    "конца периода",
    "end of period",
    "period end",
)

# Surfaces that sit beside a date and are not period words. The residual
# peel rejects them.
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
        "quarterly",
    }
)


def is_total_word(text: str | None) -> bool:
    """A total hint. Arithmetic, not this word, decides the column."""
    folded = _fold(text or "").strip(".")
    return bool(folded) and any(matches_entry(folded, entry) for entry in TOTAL_WORDS)


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


def accepts(entries: tuple[str, ...], surface: str) -> bool:
    """True when ``surface`` matches one word in ``entries``."""
    return any(matches_entry(surface, entry) for entry in entries)


def _paradigm(root: str) -> tuple[str, frozenset[str]] | None:
    """Citation root and endings when ``root`` is a citation form, not a bare suffix."""
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
def pattern(entries: tuple[str, ...]) -> str:
    """Candidate regex for ``entries``. Callers that embed it confirm the hit with ``accepts``."""
    items = sorted(entries, key=len, reverse=True)
    alts = [_candidate(entry) for entry in items]
    return rf"(?:{'|'.join(alts)})(?![^\W\d_])"


class _Hit:
    def __init__(self, text: str) -> None:
        self._text = text

    def group(self, index: int = 0) -> str:
        return self._text

    def __bool__(self) -> bool:
        return True


class _Searcher:
    def __init__(self, entries: tuple[str, ...]) -> None:
        self.entries = entries

    def search(self, text: str) -> _Hit | None:
        folded = _fold(text)
        for entry in self.entries:
            root = _fold(entry)
            if " " not in root:
                continue
            if re.search(rf"(?<!\w){re.escape(root)}(?![^\W\d_])", folded):
                return _Hit(root)
        for match in re.finditer(rf"(?<!\w){pattern(self.entries)}", folded):
            if accepts(self.entries, match.group(0)):
                return _Hit(match.group(0))
        return None


@cache
def searcher(entries: tuple[str, ...]) -> _Searcher:
    """Scan of one normalized label. Built once per word list."""
    return _Searcher(entries)


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
