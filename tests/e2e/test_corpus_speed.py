"""Build time of corpus books. `scripts/fetch-corpus.py` downloads the public ones.

Each limit is about twice the target on a laptop. Before the stem and header
caches RVI took 22 s and Packt and cashflow 5-6 s, so a lost cache fails here.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from finance_context.app.pipeline import Pipeline
from finance_context.layout import lexicon, periods
from finance_context.settings import Settings

_CORPUS = Path(__file__).resolve().parents[2] / "resources" / "corpus"


@pytest.mark.e2e_models
@pytest.mark.parametrize(
    ("name", "limit"),
    [
        ("rvi-project-finance.xlsx", 6.0),
        ("packt-project-finance.xlsx", 3.0),
        ("cashflow.xlsx", 3.0),
    ],
)
def test_corpus_book_builds_within_limit(tmp_path: Path, name: str, limit: float) -> None:
    workbook = _CORPUS / name
    if not workbook.is_file():
        pytest.skip(f"missing {workbook}; run scripts/fetch-corpus.py")
    dest = tmp_path / workbook.stem
    dest.mkdir()
    (dest / "source.xlsx").symlink_to(workbook.resolve())
    lexicon._stem.cache_clear()
    periods._classify.cache_clear()
    pipeline = Pipeline(Settings(data_dir=tmp_path / "data", _env_file=None))
    start = time.perf_counter()
    doc = pipeline.run(dest, job_id=workbook.stem, source_filename=workbook.name)
    elapsed = time.perf_counter() - start
    assert doc.meta.status == "succeeded"
    assert elapsed < limit, f"{name} built in {elapsed:.1f}s, limit {limit:.0f}s"
