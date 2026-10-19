from __future__ import annotations

from finance_context.layout.lexicon import searcher


def test_label_searcher_is_compiled_once() -> None:
    first = searcher("phase_events")
    second = searcher("phase_events")
    assert first is second
    assert first.search("дата начала")
