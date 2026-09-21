from __future__ import annotations

from tests.helpers.ports import GrantSlots

from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.glossary import reconcile_glossary
from finance_context.mapping.models import Concept
from finance_context.mapping.semantics import classify_semantics
from finance_context.mapping.taxonomy import load_taxonomy


def _headers() -> list[AxisHeader]:
    return [AxisHeader(col=2, text="Y1", role="forecast", period_key="Y1")]


def _sheet(name: str, rows: list[LayoutRow]) -> SheetLayout:
    return SheetLayout(
        name=name,
        blocks=[
            Block(
                block_id=f"{name}!r1",
                label_col=1,
                axis=Axis(id=f"{name}!r1", row=1, headers=_headers()),
                rows=rows,
            )
        ],
    )


def test_glossary_cannot_collapse_statement_roles() -> None:
    """Learned glossary maps one label to one concept. Skip-patterns keep the statement role."""
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            _sheet(
                "CFS",
                [
                    LayoutRow(
                        row=8,
                        label="Gross Revenues",
                        kind="fact",
                        section_path=["Cashflow Statement"],
                    )
                ],
            ),
            _sheet(
                "Construction",
                [
                    LayoutRow(
                        row=26,
                        label="Equity (k£)",
                        kind="fact",
                        section_path=["Sources"],
                    )
                ],
            ),
        ]
    )
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={
            ("gross revenues", "cash flow statement"): "pnl.revenue",
            ("equity", "sources"): "bs.equity",
        },
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    by_label = {row.label: row for row in doc.rows}
    revenue = by_label["Gross Revenues"]
    equity = by_label["Equity (k£)"]
    assert revenue.concept_id == "cf.receipts"
    assert revenue.semantic_identity is not None
    assert revenue.semantic_identity.family == "revenue"
    assert revenue.semantic_identity.concept_id == "pnl.revenue"
    assert [role.role for role in revenue.reporting_roles if role.selected] == ["cf.receipts"]
    assert "cf.cfads" not in {role.role for role in revenue.reporting_roles}
    assert "cfads_input" in {role.role for role in revenue.reporting_roles}
    assert revenue.cash_semantics is not None
    assert revenue.cash_semantics.recognition == "cash"
    assert revenue.cash_semantics.cash_movement == "inflow"
    assert "pnl.revenue" not in {item[0] for item in revenue.alternatives}

    assert equity.concept_id == "cf.equity_issue"
    assert equity.semantic_identity is not None
    assert equity.semantic_identity.concept_id == "cf.equity_issue"
    assert equity.context_role == "sources"
    assert "cf.sources" in equity.secondary_concepts
    assert "bs.equity" not in {role.role for role in equity.reporting_roles}
    assert equity.cash_semantics is not None
    assert equity.cash_semantics.recognition == "cash"
    assert equity.cash_semantics.cash_movement == "inflow"


def test_uses_lines_keep_identity_and_record_uses_role() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            _sheet(
                "Construction",
                [
                    LayoutRow(
                        row=13,
                        label="Arrangement fee (k£)",
                        kind="fact",
                        section_path=["Uses"],
                    ),
                    LayoutRow(
                        row=15,
                        label="Capitalized Interest (k£)",
                        kind="fact",
                        section_path=["Uses"],
                    ),
                    LayoutRow(
                        row=12,
                        label="Construction Cost (k£)",
                        kind="fact",
                        section_path=["Uses"],
                    ),
                ],
            )
        ]
    )
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={},
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    by_label = {row.label: row for row in doc.rows}
    fee = by_label["Arrangement fee (k£)"]
    interest = by_label["Capitalized Interest (k£)"]
    capex = by_label["Construction Cost (k£)"]

    assert fee.concept_id == "debt.arrangement_fee"
    assert "cf.capex" not in {item[0] for item in fee.alternatives}
    assert fee.semantic_identity is not None
    assert fee.semantic_identity.family == "arrangement_fee"
    assert fee.semantic_identity.concept_id == "debt.arrangement_fee"
    assert "cf.uses" in fee.secondary_concepts
    assert fee.cash_semantics is not None
    assert fee.cash_semantics.recognition == "cash"
    assert fee.cash_semantics.cash_movement == "outflow"

    assert interest.concept_id == "pnl.interest"
    assert interest.semantic_identity is not None
    assert interest.semantic_identity.concept_id == "pnl.interest"
    assert interest.cash_semantics is not None
    assert interest.cash_semantics.recognition == "noncash"
    assert "cf.uses" in interest.secondary_concepts

    assert capex.concept_id == "cf.capex"
    assert capex.semantic_identity is not None
    assert capex.semantic_identity.concept_id == "cf.capex"
    assert "cf.uses" in {role.role for role in capex.reporting_roles}
    assert capex.semantic_identity.concept_id != "cf.uses"


def test_cash_opex_keeps_accrual_identity() -> None:
    identity, roles, cash = classify_semantics(
        label="Total operating costs",
        concept_id="cf.opex_paid",
        score=0.93,
        alternatives=[("cf.opex_paid", 0.93)],
        context_role="cfads_input",
    )
    assert identity is not None
    assert identity.family == "opex"
    assert identity.concept_id == "pnl.opex"
    assert [role.role for role in roles if role.selected] == ["cf.opex_paid"]
    assert cash is not None
    assert cash.recognition == "cash"
    assert cash.cash_movement == "outflow"


def test_inflation_child_is_the_identity_not_the_parent() -> None:
    identity, roles, cash = classify_semantics(
        label="Inflation per year",
        concept_id="ops.inflation_revenue",
        score=1.0,
        alternatives=[("ops.inflation_revenue", 1.0), ("ops.inflation", 0.9)],
        context_role="assumption",
    )
    assert identity is not None
    assert identity.family == "inflation"
    assert identity.concept_id == "ops.inflation_revenue"
    assert "ops.inflation" not in {role.role for role in roles}
    assert cash is not None
    assert cash.recognition == "rate"
    assert cash.cash_movement == "none"


def test_reconcile_keeps_crosswalk_and_rewrites_stale_duration() -> None:
    taxonomy = [
        Concept(id="pnl.revenue", labels=["Gross revenues"]),
        Concept(id="cf.receipts", labels=["Receipts"]),
        Concept(id="ops.lifetime", labels=["Lifetime"]),
        Concept(id="ops.concession_duration", labels=["Concession Duration"]),
    ]
    rewritten = reconcile_glossary(
        {
            ("gross revenues", "cash flow statement"): "cf.receipts",
            ("concession duration", ""): "ops.lifetime",
        },
        taxonomy,
    )
    assert rewritten[("gross revenues", "cash flow statement")] == "cf.receipts"
    assert rewritten[("concession duration", "")] == "ops.concession_duration"
