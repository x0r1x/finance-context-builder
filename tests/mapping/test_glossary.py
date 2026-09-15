from __future__ import annotations

from pathlib import Path

from finance_context.mapping.glossary import learn_from_rows, load_glossary, save_glossary
from finance_context.mapping.models import MappedRow


def test_glossary_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "glossary.json"
    save_glossary(path, {("opening cash", ""): "bs.cash"})
    loaded = load_glossary(path)
    assert loaded[("opening cash", "")] == "bs.cash"


def test_learn_from_high_confidence_rows() -> None:
    rows = [
        MappedRow(
            row_key="a",
            sheet="S",
            row=2,
            block_id="S!r1",
            label="Opening cash",
            concept_id="bs.cash",
            article_role="database_like",
            source="rule",
            confidence="high",
        ),
        MappedRow(
            row_key="b",
            sheet="S",
            row=3,
            block_id="S!r1",
            label="Mystery",
            concept_id="pnl.revenue",
            article_role="database_like",
            source="chat",
            confidence="medium",
        ),
    ]
    learned = learn_from_rows({}, rows)
    assert learned[("opening cash", "")] == "bs.cash"
    assert ("mystery", "") not in learned
