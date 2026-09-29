from __future__ import annotations

from tests.helpers.policy import accept_min

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
        ],
        concept_accept_min=accept_min(),
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
        ],
        concept_accept_min=accept_min(),
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
                    nature="balance",
                    time_semantics="flow",
                    statement="bs",
                    unit="money",
                    sign="inflow",
                ),
                semantic_identity=_identity("bs.cash", "cash"),
                cash_semantics=CashSemantics(recognition="stock", cash_movement="none"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    assert metrics["semantic_coverage"] == 0.0
    assert metrics["temporal_coverage"] == 0.0


def test_roll_forward_movement_keeps_stock_concept() -> None:
    metrics = mapping_quality_metrics(
        [
            _row(
                label="Retained earnings",
                concept_id="bs.retained_earnings",
                hints=RowHints(
                    nature="flow",
                    time_semantics="flow",
                    statement="bs",
                    unit="money",
                    sign="inflow",
                ),
                semantic_identity=_identity("bs.retained_earnings", "equity"),
                cash_semantics=CashSemantics(recognition="stock", cash_movement="none"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    assert metrics["semantic_coverage"] == 1.0
    assert metrics["temporal_coverage"] == 1.0


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
        ],
        concept_accept_min=accept_min(),
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
    metrics = mapping_quality_metrics([debt, repayment], concept_accept_min=accept_min())
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
        ],
        concept_accept_min=accept_min(),
    )
    assert metrics["formula_coverage"] == 0.0
    assert metrics["semantic_coverage"] == 1.0


def test_section_heading_supports_the_wider_concept() -> None:
    assert _coverage(
        label="Commercial Management",
        concept_id="pnl.opex",
        label_path=["Operational Expenditures"],
    ) == 1.0


def test_balance_brought_forward_follows_its_section() -> None:
    assert _coverage(label="Balance b/f", concept_id="bs.equity", label_path=["Equity"]) == 1.0
    assert _coverage(label="Balance b/f", concept_id="bs.debt", label_path=["Debt"]) == 1.0


def test_dividend_stem_matches_the_label() -> None:
    assert _coverage(label="Dividend distribution", concept_id="cf.dividends") == 1.0


def test_total_sum_of_children_is_a_presentation_role() -> None:
    assert (
        _coverage(
            label="Total",
            concept_id="cf.disbursements",
            evidence="sum of 3 child rows",
            label_path=["Senior debt"],
        )
        == 1.0
    )


def test_parenthetical_heading_keeps_the_inner_word() -> None:
    assert (
        _coverage(
            label="Charge",
            concept_id="bs.goodwill",
            label_path=["No depreciation (goodwill)"],
        )
        == 1.0
    )


def test_standard_name_of_another_concept_is_not_supported() -> None:
    assert (
        _coverage(label="CPI", concept_id="ops.inflation", label_path=["Inflation profiles"])
        == 0.0
    )
    assert (
        _coverage(
            label="Share premium",
            concept_id="cf.uses",
            label_path=["Cashflow Uses of funds"],
        )
        == 0.0
    )


def test_statement_pair_label_is_supported() -> None:
    assert (
        _coverage(label="Equity", concept_id="cf.equity_issue", label_path=["Sources of funds"])
        == 1.0
    )


def test_ratio_and_instant_periods_keep_their_time() -> None:
    index = mapping_quality_metrics(
        [
            _row(
                label="CPI",
                concept_id="ops.cpi",
                hints=RowHints(time_semantics="stock", statement="ops", unit="ratio"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    minimum = mapping_quality_metrics(
        [
            _row(
                label="DSCR minimum",
                concept_id="cov.dscr_limit",
                hints=RowHints(time_semantics="instant", statement="cov", unit="ratio"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    series = mapping_quality_metrics(
        [
            _row(
                label="DSCR",
                concept_id="cov.dscr",
                hints=RowHints(time_semantics="flow", statement="cov", unit="ratio"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    instant_rate = mapping_quality_metrics(
        [
            _row(
                label="Cash-on-Cash (CoC)",
                concept_id="val.coc",
                hints=RowHints(time_semantics="instant", statement="val", unit="ratio"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    assert index["temporal_coverage"] == 1.0
    assert minimum["temporal_coverage"] == 1.0
    assert series["temporal_coverage"] == 1.0
    assert instant_rate["temporal_coverage"] == 1.0
    assert instant_rate["unit_coverage"] == 1.0


def test_rate_and_ratio_share_a_unit_family() -> None:
    money_on_ratio = mapping_quality_metrics(
        [
            _row(
                label="CPI",
                concept_id="ops.cpi",
                hints=RowHints(time_semantics="stock", statement="ops", unit="money"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    percent_on_ratio = mapping_quality_metrics(
        [
            _row(
                label="CPI",
                concept_id="ops.cpi",
                hints=RowHints(time_semantics="stock", statement="ops", unit="rate"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    blank = mapping_quality_metrics(
        [
            _row(
                label="CPI",
                concept_id="ops.cpi",
                hints=RowHints(time_semantics="stock", statement="ops", unit=None),
            )
        ],
        concept_accept_min=accept_min(),
    )
    assert money_on_ratio["unit_coverage"] == 0.0
    assert percent_on_ratio["unit_coverage"] == 1.0
    assert blank["unit_coverage"] == 0.0


def test_rate_without_instant_period_still_needs_rate_time() -> None:
    metrics = mapping_quality_metrics(
        [
            _row(
                label="Inflation",
                concept_id="ops.inflation",
                hints=RowHints(time_semantics="flow", statement="ops", unit="rate"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    assert metrics["temporal_coverage"] == 0.0


def test_years_hint_satisfies_count_and_a_bare_date_is_not_money() -> None:
    life = mapping_quality_metrics(
        [
            _row(
                label="Straight line depreciation",
                concept_id="ops.depreciation_life",
                hints=RowHints(time_semantics="instant", statement="ops", unit="years"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    started = mapping_quality_metrics(
        [
            _row(
                label="Model start / Construction start",
                concept_id="ops.model_start",
                hints=RowHints(time_semantics="instant", statement="ops", unit="date"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    money_on_duration = mapping_quality_metrics(
        [
            _row(
                label="Development & construction",
                concept_id="ops.construction_period",
                hints=RowHints(time_semantics="flow", statement="ops", unit="money"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    count_on_duration = mapping_quality_metrics(
        [
            _row(
                label="Construction Duration",
                concept_id="ops.construction_period",
                hints=RowHints(time_semantics="instant", statement="ops", unit="count"),
            )
        ],
        concept_accept_min=accept_min(),
    )
    assert life["unit_coverage"] == 1.0
    assert started["unit_coverage"] == 1.0
    assert money_on_duration["unit_coverage"] == 0.0
    assert count_on_duration["unit_coverage"] == 1.0


def test_cashflow_plural_keeps_the_dictionary_pass() -> None:
    assert (
        _coverage(
            label="Total equity cashflows",
            concept_id="cf.equity_cashflow",
            evidence="signed equity irr cash line; cosine=0.817",
        )
        == 1.0
    )


def test_loose_section_total_stays_unsupported() -> None:
    assert (
        _coverage(
            label="Total",
            concept_id="cf.capex",
            label_path=["Construction & Development costs"],
            evidence="parent capex or uses section",
        )
        == 0.0
    )


def _coverage(**kwargs: object) -> float:
    metrics = mapping_quality_metrics([_row(**kwargs)], concept_accept_min=accept_min())
    return float(metrics["label_coverage"])


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
        ],
        concept_accept_min=accept_min(),
    )
    assert metrics["semantic_coverage"] == 1.0
    assert metrics["confidence_threshold_passed"] is False
