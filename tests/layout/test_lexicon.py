from __future__ import annotations

import os

import pytest

from finance_context.layout.lexicon import accepts, matches_entry, searcher


def test_label_searcher_is_compiled_once() -> None:
    first = searcher("structural", "period_start")
    second = searcher("structural", "period_start")
    assert first is second
    assert first.search("начало периода")


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
        "Budgeting",
    ],
)
def test_stem_block_rejects_lookalike_words(surface: str) -> None:
    assert not accepts("period_words", "quarter", surface=surface)
    assert not accepts("period_words", "year", surface=surface)
    assert not accepts("period_markers", "actual", surface=surface)
    assert not accepts("period_markers", "plan", surface=surface)


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
