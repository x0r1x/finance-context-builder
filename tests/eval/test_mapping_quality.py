from __future__ import annotations

from finance_context.mapping.eval import mapping_metrics, signal_counts
from finance_context.mapping.facets import prune_candidates
from finance_context.mapping.models import Candidate, Concept, RowContext


def test_coverage_and_selective_risk() -> None:
    predicted = [
        ("a", "bs.cash", "bs.cash"),
        ("b", "cf.receipts", "pnl.revenue"),
        ("c", "cf.net", None),
    ]
    metrics = mapping_metrics(predicted)
    assert metrics["n"] == 3
    assert metrics["mapped"] == 2
    assert metrics["coverage"] == 2 / 3
    assert metrics["selective_risk"] == 0.5
    assert signal_counts(["rule", "rule", "structure"])["rule"] == 2


def test_prune_drops_ratio_concepts_on_money_rows() -> None:
    taxonomy = {
        "cov.llcr": Concept(id="cov.llcr", labels=["LLCR"], value_kind="ratio"),
        "pnl.opex": Concept(id="pnl.opex", labels=["OPEX"], value_kind="money"),
    }
    ctx = RowContext(
        row_key="x",
        sheet="Dash",
        row=2,
        block_id="Dash!r1",
        label="Insurance",
        value_kind="money",
    )
    kept = prune_candidates(
        [
            Candidate(concept_id="cov.llcr", score=0.9, signal="embed", evidence=""),
            Candidate(concept_id="pnl.opex", score=0.4, signal="embed", evidence=""),
        ],
        ctx,
        taxonomy,
    )
    assert [c.concept_id for c in kept] == ["pnl.opex"]
