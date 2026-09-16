from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from finance_context.mapping.models import Concept
from finance_context.mapping.taxonomy import TaxonomyError, load_taxonomy, validate_taxonomy


def test_default_taxonomy_is_internally_consistent() -> None:
    concepts = load_taxonomy()
    ids = [c.id for c in concepts]
    assert len(ids) == len(set(ids))
    by_id = {c.id: c for c in concepts}
    for concept in concepts:
        if concept.broader:
            assert concept.broader in by_id
        assert concept.facets.unit is not None
        assert concept.value_kind == concept.facets.unit


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(TaxonomyError, match="duplicate"):
        validate_taxonomy(
            [
                Concept(id="pnl.revenue", labels=["Revenue"]),
                Concept(id="pnl.revenue", labels=["Sales"]),
            ]
        )


def test_dangling_broader_is_rejected() -> None:
    with pytest.raises(TaxonomyError, match="broader"):
        validate_taxonomy(
            [Concept(id="cf.receipts.other", labels=["X"], broader="cf.missing")]
        )


def test_broader_cycle_is_rejected() -> None:
    with pytest.raises(TaxonomyError, match="cycle"):
        validate_taxonomy(
            [
                Concept(id="a", labels=["A"], broader="b"),
                Concept(id="b", labels=["B"], broader="a"),
            ]
        )


def test_deprecated_requires_replacement(tmp_path: Path) -> None:
    path = tmp_path / "tax.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "concepts": [
                    {"id": "old", "labels": ["Old"], "deprecated": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(TaxonomyError, match="replaced_by"):
        load_taxonomy(path)
