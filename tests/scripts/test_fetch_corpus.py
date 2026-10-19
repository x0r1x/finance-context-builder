"""The corpus lock can name a book that was never published."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "scripts" / "fetch-corpus.py"
_SPEC = importlib.util.spec_from_file_location("fetch_corpus", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
fetch_corpus = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fetch_corpus)


def test_unpublished_three_statement_book_is_skipped(tmp_path: Path, monkeypatch) -> None:
    def refuse(url: str, timeout: int = 60) -> None:
        del timeout
        raise AssertionError(f"should not fetch {url}")

    monkeypatch.setattr(fetch_corpus.urllib.request, "urlopen", refuse)
    spec = json.loads(fetch_corpus.LOCK.read_text(encoding="utf-8"))
    only = {"books": [book for book in spec["books"] if book["slug"] == "three-statement"]}
    assert only["books"][0]["available"] is False
    assert only["books"][0]["sha256"] is None
    assert fetch_corpus.fetch_books(only, tmp_path) == 0
    assert not (tmp_path / "three-statement.xlsx").exists()


def test_a_failed_download_is_an_error_when_nothing_was_written(
    tmp_path: Path, monkeypatch
) -> None:
    def missing(url: str, timeout: int = 60) -> None:
        del url, timeout
        raise OSError("404")

    monkeypatch.setattr(fetch_corpus.urllib.request, "urlopen", missing)
    code = fetch_corpus.fetch_books(
        {"books": [{"slug": "x", "filename": "x.xlsx", "urls": ["http://example.invalid/x"]}]},
        tmp_path,
    )
    assert code == 1
