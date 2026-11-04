"""SHA-256 of the published artifacts for the small benchmark books.

``context.json``, ``context.md``, ``graph.json``, and ``layout.json`` are
hashed. A byte change fails the test.

Refresh the checked-in hashes on purpose, then commit
``tests/synthetic/golden/hashes.json``:

    UPDATE_SYNTHETIC_GOLDEN=1 uv run pytest tests/synthetic/test_golden.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tests.fixtures.synthetic import (
    GENERATOR_REV,
    block,
    cached_workbook,
    hops,
    outline,
    rel,
    sumif,
    text,
)
from tests.synthetic.support import artifact_hashes, run_book

_GOLDEN = Path(__file__).resolve().parent / "golden" / "hashes.json"
_UPDATE = os.environ.get("UPDATE_SYNTHETIC_GOLDEN") == "1"

# (builder, source filename). The filename is stored in the artifacts.
_CASES: dict[str, tuple[object, str]] = {
    "rel5k": (lambda path: rel(path, 5_000), "rel5k.xlsx"),
    "hops10k": (lambda path: hops(path, 10_000), "hops10k.xlsx"),
    "sumif10k": (lambda path: sumif(path, 10_000), "sumif10k.xlsx"),
    "outline": (lambda path: outline(path), "outline.xlsx"),
    "text100": (lambda path: text(path, 100), "text100.xlsx"),
    "block8": (lambda path: block(path, 8), "block8.xlsx"),
}


def _load() -> dict:
    if not _GOLDEN.is_file():
        return {"note": _note(), "books": {}}
    return json.loads(_GOLDEN.read_text(encoding="utf-8"))


def _note() -> str:
    return (
        "Regenerate with UPDATE_SYNTHETIC_GOLDEN=1 "
        "uv run pytest tests/synthetic/test_golden.py and commit this file."
    )


def _store(payload: dict) -> None:
    _GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    payload["note"] = _note()
    _GOLDEN.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@pytest.fixture
def synthetic_cache(pytestconfig: pytest.Config) -> Path:
    """Pytest cache directory. A second run reuses the workbook bytes."""
    return Path(pytestconfig.cache.mkdir(f"synthetic-r{GENERATOR_REV}"))


@pytest.mark.parametrize("name", list(_CASES))
def test_published_artifacts_match_golden(
    name: str, tmp_path: Path, synthetic_cache: Path
) -> None:
    build, filename = _CASES[name]
    xlsx = cached_workbook(synthetic_cache, name, build)
    dest = tmp_path / "job"
    run_book(xlsx, dest, filename=filename, job_id=f"synthetic-{name}")
    context = json.loads((dest / "context" / "context.json").read_text(encoding="utf-8"))
    graph = json.loads((dest / "graph" / "graph.json").read_text(encoding="utf-8"))
    assert context["schema_version"] == "1.14.0"
    assert graph["schema_version"] == "1.7.0"
    actual = artifact_hashes(dest)
    payload = _load()
    books = payload.setdefault("books", {})
    if _UPDATE:
        books[name] = actual
        _store(payload)
        return
    assert name in books, f"missing {name}; regenerate with UPDATE_SYNTHETIC_GOLDEN=1"
    assert books[name] == actual
