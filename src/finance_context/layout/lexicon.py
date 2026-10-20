"""RU/EN words the layout reads: param headers, period markers and units.

Words live in ``lexicon.json`` next to this module. A word matches whole. A longer
word matches when its Snowball stem agrees with the entry, or when only a
grammatical ending differs (``кварталу`` / ``квартал``). Words of at most three
letters stay exact, so ``кв`` is not ``кВт`` and ``г`` is not ``график``.
``stem_block`` lists collisions the stemmer must not accept (``кВт``, ``график``,
``History``, ``Histogram``, ``фактор``, ``планета``, ``Budgeting``).
A trailing ``*`` is still read as a root, not as "any ending". A word with a space
is a phrase (``end of``).

``structural`` holds the patterns that used to be hardcoded: totals, stubs,
scenario names, month names, quarter and month axis labels, period bounds, and
scenario-table header words. The package file is validated on load.

``FINANCE_CONTEXT_LEXICON`` may point at a JSON file with the same shape. Its
lists are added to the package lists and its objects are merged. The publisher
hashes that file into the layout stage, so an overlay restales layout.
"""

from __future__ import annotations

import json
import os
import re
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import snowballstemmer
from pydantic import BaseModel, ConfigDict, ValidationError

_RU = snowballstemmer.stemmer("russian")
_EN = snowballstemmer.stemmer("english")
_SHORT = 3
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


class _Structural(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: list[str]
    stub: list[str]
    scenario: list[str]
    scenario_adjectives: list[str]
    scenario_evidence: list[str]
    scenario_header: list[str]
    months: dict[str, list[str]]
    quarter_label: list[str]
    month_label: list[str]
    period_start: list[str]
    period_end: list[str]


class _LexiconModel(BaseModel):
    """Package lexicon. An overlay may add keys; the package file may not."""

    model_config = ConfigDict(extra="forbid")
    header_roles: dict[str, list[str]]
    period_markers: dict[str, list[str]]
    period_words: dict[str, list[str]]
    structural: _Structural
    stem_block: dict[str, list[str]] = {}


def overlay_path() -> Path | None:
    raw = os.environ.get("FINANCE_CONTEXT_LEXICON", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def data_token() -> str:
    path = overlay_path()
    if path is None:
        return "package"
    return f"{path}:{path.stat().st_mtime_ns}"


def _package_text() -> str:
    return resources.files(__package__).joinpath("lexicon.json").read_text(encoding="utf-8")


def _validate(data: dict[str, Any], *, package: bool) -> None:
    model = _LexiconModel if package else _OverlayModel
    try:
        model.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"lexicon schema: {exc}") from exc


class _OverlayModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    header_roles: dict[str, list[str]] | None = None
    period_markers: dict[str, list[str]] | None = None
    period_words: dict[str, list[str]] | None = None
    structural: dict[str, Any] | None = None
    stem_block: dict[str, list[str]] | None = None


def _merge(base: Any, over: Any) -> Any:
    if isinstance(base, dict) and isinstance(over, dict):
        merged = dict(base)
        for key, value in over.items():
            merged[key] = _merge(merged[key], value) if key in merged else value
        return merged
    if isinstance(base, list) and isinstance(over, list):
        seen: list[Any] = []
        for item in [*base, *over]:
            if item not in seen:
                seen.append(item)
        return seen
    return over


@cache
def _load(token: str) -> dict[str, Any]:
    base = json.loads(_package_text())
    _validate(base, package=True)
    path = overlay_path() if token != "package" else None
    if path is None:
        return base
    overlay = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(overlay, dict):
        raise ValueError("lexicon schema: overlay must be an object")
    _validate(overlay, package=False)
    merged = _merge(base, overlay)
    _validate(merged, package=True)
    return merged


def _data() -> dict[str, Any]:
    return _load(data_token())


def words(*path: str) -> tuple[str, ...]:
    node: Any = _data()
    for key in path:
        node = node[key]
    return tuple(node)


def _root(entry: str) -> str:
    return entry[:-1] if entry.endswith("*") else entry


def _fold(text: str) -> str:
    return text.casefold().replace("ё", "е")


def _stem(word: str) -> str:
    folded = _fold(word)
    if re.search(r"[а-я]", folded):
        return _RU.stemWord(folded)
    return _EN.stemWord(folded)


def _block_keys(root: str) -> set[str]:
    table = _data().get("stem_block") or {}
    found: set[str] = set()
    for key in (root, _stem(root)):
        for item in table.get(key) or []:
            found.add(_fold(item).strip("."))
    return found


def _stem_equal(token: str, root: str) -> bool:
    """Snowball agreement, only when the stem still covers most of the root."""
    left = _stem(token)
    right = _stem(root)
    if len(left) < 4 or len(right) < 4 or left != right:
        return False
    return len(right) >= len(root) - 3


def matches_entry(surface: str, entry: str) -> bool:
    """True when ``surface`` is ``entry`` or the same stem, and not a collision."""
    token = _fold(surface).strip(".")
    root = _fold(_root(entry)).strip(".")
    if not token or not root:
        return False
    if " " in root:
        return token == root
    if token == root:
        return True
    if token in _block_keys(root):
        return False
    if len(root) <= _SHORT:
        return False
    if _stem_equal(token, root):
        return True
    return bool(token.startswith(root) and token[len(root) :] in _INFLECTIONS)


def accepts(*path: str, surface: str) -> bool:
    return any(matches_entry(surface, entry) for entry in words(*path))


def _candidate(entry: str) -> str:
    """A regex that may over-match. ``accepts`` is the real test."""
    root = _fold(_root(entry))
    if " " in root or len(root) <= _SHORT:
        return re.escape(root)
    stem = _stem(root)
    if len(stem) >= 4 and root.startswith(stem) and len(root) - len(stem) <= 3:
        return re.escape(stem) + r"\w*"
    endings = "|".join(re.escape(item) for item in sorted(_INFLECTIONS, key=len, reverse=True))
    return rf"{re.escape(root)}(?:{endings})?"


@cache
def _pattern(token: str, path: tuple[str, ...]) -> str:
    del token
    items = sorted(words(*path), key=len, reverse=True)
    alts = [_candidate(entry) for entry in items]
    return rf"(?:{'|'.join(alts)})(?![^\W\d_])"


def pattern(*path: str) -> str:
    """Candidate regex. Callers that embed it must confirm the hit with ``accepts``."""
    return _pattern(data_token(), path)


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
            root = _fold(_root(entry))
            if " " not in root:
                continue
            if re.search(rf"(?<!\w){re.escape(root)}(?![^\W\d_])", folded):
                return _Hit(root)
        for match in re.finditer(rf"(?<!\w){_pattern(data_token(), self.path)}", folded):
            if accepts(*self.path, surface=match.group(0)):
                return _Hit(match.group(0))
        return None


@cache
def _searcher(token: str, path: tuple[str, ...]) -> _Searcher:
    del token
    return _Searcher(path)


def searcher(*path: str) -> _Searcher:
    """Scan of one normalized label. Built once per word list and lexicon token."""
    return _searcher(data_token(), path)


def header_role_map() -> dict[str, str]:
    return _header_roles(data_token())


@cache
def _header_roles(token: str) -> dict[str, str]:
    del token
    return {
        word: role
        for role in ("label", "value", "unit", "note")
        for word in words("header_roles", role)
    }


def month_numbers() -> dict[str, int]:
    return _months(data_token())


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
def _months(token: str) -> dict[str, int]:
    del token
    found: dict[str, int] = {}
    for key, forms in _data()["structural"]["months"].items():
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
        root = _fold(_root(entry))
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

