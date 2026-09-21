from __future__ import annotations

from finance_context.context.measure import parse_measure
from finance_context.mapping.eval import (
    QualityRow,
    concept_coverage,
    content_completeness,
    context_report_metrics,
    mapping_metrics,
    mapping_quality_metrics,
    signal_counts,
)
from finance_context.mapping.facets import prune_candidates
from finance_context.mapping.models import Candidate, Concept, RowContext
from finance_context.models.context import CashSemantics, RowHints, SemanticIdentity


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
    assert metrics["concept_coverage"] == 2 / 3
    assert metrics["selective_risk"] == 0.5
    assert signal_counts(["rule", "rule", "structure"])["rule"] == 2


def test_risk_coverage_curve_orders_by_score() -> None:
    from finance_context.mapping.eval import risk_coverage_curve

    curve = risk_coverage_curve(
        [
            ("bs.cash", "bs.cash", 0.99),
            ("cf.net", "pnl.revenue", 0.5),
            ("cf.receipts", None, None),
        ]
    )
    assert curve[0]["k"] == 1
    assert curve[0]["risk"] == 0.0
    assert curve[1]["risk"] == 0.5


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


def test_content_completeness_is_independent_of_concept_coverage() -> None:
    assert content_completeness(10, 10) == 1.0
    assert content_completeness(10, 8) == 0.8
    assert concept_coverage(mapped=6, abstained=4) == 0.6
    report = context_report_metrics(
        layout_rows=10,
        inventory_rows=10,
        mapped=6,
        abstained=4,
    )
    assert report["content_completeness"] == 1.0
    assert report["concept_coverage"] == 0.6


def _identity(concept_id: str, family: str | None = None) -> SemanticIdentity:
    return SemanticIdentity(
        family=family or concept_id.split(".", 1)[-1],
        concept_id=concept_id,
        confidence=1.0,
    )


def _row(**kwargs) -> QualityRow:
    defaults = dict(
        score=0.95,
        confidence="high",
        sheet="P&L",
        block_id="P&L!r1",
    )
    defaults.update(kwargs)
    return QualityRow(**defaults)


def test_quality_passes_for_a_balance_supported_by_its_label() -> None:
    metrics = mapping_quality_metrics(
        [
            _row(
                label="Opening cash",
                concept_id="bs.cash",
                hints=RowHints(
                    nature="balance",
                    time_semantics="bop",
                    statement="bs",
                    unit="money",
                    sign="stock",
                ),
                semantic_identity=_identity("bs.cash", "cash"),
                cash_semantics=CashSemantics(recognition="stock", cash_movement="none"),
            )
        ]
    )
    assert metrics["label_coverage"] == 1.0
    assert metrics["semantic_coverage"] == 1.0
    assert metrics["unit_coverage"] == 1.0
    assert metrics["temporal_coverage"] == 1.0
    assert metrics["formula_coverage"] == 1.0
    assert metrics["confidence_threshold_passed"] is True


def test_cfs_revenue_slot_does_not_count_as_semantic_coverage() -> None:
    metrics = mapping_quality_metrics(
        [
            _row(
                label="Gross Revenues",
                concept_id="pnl.revenue",
                sheet="CFS",
                label_path=["Cashflow Statement"],
                hints=RowHints(
                    nature="flow",
                    time_semantics="flow",
                    statement="cf",
                    unit="money",
                    sign="inflow",
                ),
                semantic_identity=_identity("pnl.revenue", "revenue"),
                cash_semantics=CashSemantics(recognition="accrual", cash_movement="inflow"),
                score=0.99,
            )
        ]
    )
    assert metrics["label_coverage"] == 1.0
    assert metrics["semantic_coverage"] == 0.0
    assert metrics["confidence_threshold_passed"] is False
    assert concept_coverage(mapped=1, abstained=0) == 1.0


def test_cash_classified_as_flow_fails_semantic_and_temporal_checks() -> None:
    metrics = mapping_quality_metrics(
        [
            _row(
                label="Cash balance",
                concept_id="bs.cash",
                hints=RowHints(
                    nature="flow",
                    time_semantics="flow",
                    statement="bs",
                    unit="money",
                    sign="inflow",
                ),
                semantic_identity=_identity("bs.cash", "cash"),
                cash_semantics=CashSemantics(recognition="stock", cash_movement="none"),
            )
        ]
    )
    assert metrics["semantic_coverage"] == 0.0
    assert metrics["temporal_coverage"] == 0.0


def test_repayment_without_outflow_or_debt_stock_fails_semantic_check() -> None:
    metrics = mapping_quality_metrics(
        [
            _row(
                label="Principal Repayment",
                concept_id="cf.repayment",
                block_id="Debt!r1",
                sheet="Debt",
                hints=RowHints(
                    nature="flow",
                    time_semantics="flow",
                    statement="cf",
                    unit="money",
                    sign=None,
                ),
                semantic_identity=_identity("cf.repayment"),
                cash_semantics=CashSemantics(recognition="cash", cash_movement="none"),
            )
        ]
    )
    assert metrics["semantic_coverage"] == 0.0
    assert metrics["confidence_threshold_passed"] is False


def test_repayment_outflow_next_to_debt_stock_passes() -> None:
    debt = _row(
        label="Opening Balance",
        concept_id="bs.debt",
        block_id="Debt!r1",
        sheet="Debt",
        label_path=["Debt Repayment Schedule"],
        hints=RowHints(
            nature="balance",
            time_semantics="bop",
            statement="bs",
            unit="money",
            sign="stock",
        ),
        semantic_identity=_identity("bs.debt", "debt"),
        cash_semantics=CashSemantics(recognition="stock", cash_movement="none"),
    )
    repayment = _row(
        label="Principal Repayment",
        concept_id="cf.repayment",
        block_id="Debt!r1",
        sheet="Debt",
        label_path=["Debt Repayment Schedule"],
        hints=RowHints(
            nature="flow",
            time_semantics="flow",
            statement="cf",
            unit="money",
            sign="outflow",
        ),
        semantic_identity=_identity("cf.repayment"),
        cash_semantics=CashSemantics(recognition="cash", cash_movement="outflow"),
    )
    metrics = mapping_quality_metrics([debt, repayment])
    assert metrics["semantic_coverage"] == 1.0
    assert metrics["confidence_threshold_passed"] is True


def test_pre_tax_income_is_not_an_outflow() -> None:
    measure = parse_measure(
        label="EBT (Taxable Profit)",
        concept_id="pnl.pre_tax_income",
        statement="pnl",
    )
    assert measure.sign != "outflow"


def test_formula_cell_without_text_fails_formula_coverage() -> None:
    metrics = mapping_quality_metrics(
        [
            _row(
                label="Opening cash",
                concept_id="bs.cash",
                hints=RowHints(
                    nature="balance",
                    time_semantics="bop",
                    statement="bs",
                    unit="money",
                    sign="stock",
                ),
                semantic_identity=_identity("bs.cash", "cash"),
                cash_semantics=CashSemantics(recognition="stock", cash_movement="none"),
                formula_fingerprint="=RC[1]",
                values=[type("Cell", (), {"has_formula": True, "formula": None})()],
            )
        ]
    )
    assert metrics["formula_coverage"] == 0.0
    assert metrics["semantic_coverage"] == 1.0


def test_score_below_accept_min_fails_confidence_threshold() -> None:
    metrics = mapping_quality_metrics(
        [
            _row(
                label="Opening cash",
                concept_id="bs.cash",
                hints=RowHints(
                    nature="balance",
                    time_semantics="bop",
                    statement="bs",
                    unit="money",
                    sign="stock",
                ),
                semantic_identity=_identity("bs.cash", "cash"),
                cash_semantics=CashSemantics(recognition="stock", cash_movement="none"),
                score=0.5,
                confidence="high",
            )
        ]
    )
    assert metrics["semantic_coverage"] == 1.0
    assert metrics["confidence_threshold_passed"] is False
