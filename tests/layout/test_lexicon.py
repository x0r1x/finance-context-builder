from __future__ import annotations

import os

import pytest

from finance_context.layout.lexicon import accepts, matches_entry, searcher


def test_label_searcher_is_compiled_once() -> None:
    first = searcher("phase_events")
    second = searcher("phase_events")
    assert first is second
    assert first.search("дата начала")


@pytest.mark.parametrize(
    ("surface", "entry"),
    [
        ("кварталу", "квартал"),
        ("фактическая", "фактический"),
        ("строительная", "строительный"),
        ("cases", "case"),
    ],
)
def test_stem_or_ending_accepts_an_inflection(surface: str, entry: str) -> None:
    assert matches_entry(surface, entry)


@pytest.mark.parametrize(
    "surface",
    [
        "кВт",
        "квота",
        "график",
        "History",
        "Histogram",
        "фактор",
        "планета",
        "начальный",
        "Budgeting",
    ],
)
def test_stem_block_rejects_lookalike_words(surface: str) -> None:
    assert not accepts("period_words", "quarter", surface=surface)
    assert not accepts("period_words", "year", surface=surface)
    assert not accepts("period_roles", "historical", surface=surface)
    assert not accepts("period_roles", "forecast", surface=surface)
    assert not accepts("phase_events", surface=surface)


def test_package_lexicon_rejects_an_unknown_key(tmp_path, monkeypatch) -> None:
    from finance_context.layout import lexicon

    overlay = tmp_path / "overlay.json"
    overlay.write_text('{"not_a_key": ["x"]}', encoding="utf-8")
    monkeypatch.setenv("FINANCE_CONTEXT_LEXICON", str(overlay))
    with pytest.raises(ValueError, match="lexicon schema"):
        lexicon.words("period_words", "year")


def test_overlay_word_is_visible_and_keyed_by_mtime(tmp_path, monkeypatch) -> None:
    from finance_context.layout import lexicon

    overlay = tmp_path / "overlay.json"
    overlay.write_text('{"period_words": {"year": ["annum"]}}', encoding="utf-8")
    monkeypatch.setenv("FINANCE_CONTEXT_LEXICON", str(overlay))
    assert "annum" in lexicon.words("period_words", "year")
    first = lexicon.data_token()
    overlay.write_text('{"period_words": {"year": ["annum", "fyword"]}}', encoding="utf-8")
    stat = overlay.stat()
    os.utime(overlay, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert lexicon.data_token() != first
    assert "fyword" in lexicon.words("period_words", "year")


class _CountingStemmer:
    def __init__(self, inner, calls: list[str]) -> None:
        self._inner = inner
        self._calls = calls

    def stemWord(self, word: str) -> str:  # noqa: N802 - snowballstemmer API
        self._calls.append(word)
        return self._inner.stemWord(word)


@pytest.fixture
def stem_calls(monkeypatch) -> list[str]:
    from finance_context.layout import lexicon

    calls: list[str] = []
    monkeypatch.setattr(lexicon, "_RU", _CountingStemmer(lexicon._RU, calls))
    monkeypatch.setattr(lexicon, "_EN", _CountingStemmer(lexicon._EN, calls))
    return calls


def test_each_word_is_stemmed_once(stem_calls: list[str]) -> None:
    from collections import Counter

    from finance_context.layout.periods import classify_header

    for text in ("Выручка от продаж", "Opening cash balance", "Капитальные затраты"):
        classify_header(text)
    repeated = {word: n for word, n in Counter(stem_calls).items() if n > 1}
    assert repeated == {}


def test_repeated_header_is_not_stemmed_again(stem_calls: list[str]) -> None:
    from finance_context.layout.periods import classify_header

    classify_header("Debt service reserve")
    stem_calls.clear()
    for _ in range(20):
        classify_header("Debt service reserve")
    assert stem_calls == []


def test_overlay_reaches_a_cached_header(tmp_path, monkeypatch) -> None:
    from finance_context.layout.periods import classify_header

    assert classify_header("Прикидка") is None
    overlay = tmp_path / "overlay.json"
    overlay.write_text('{"period_roles": {"forecast": ["прикидка"]}}', encoding="utf-8")
    monkeypatch.setenv("FINANCE_CONTEXT_LEXICON", str(overlay))
    hit = classify_header("Прикидка")
    assert hit is not None and (hit.kind, hit.period_key) == ("period", "plan")
