from __future__ import annotations

from tests.helpers.policy import llm_workers, slot_wait, thresholds
from tests.helpers.ports import GrantSlots
from tests.mapping.conftest import _layout

from finance_context.layout.models import (
    Axis,
    AxisHeader,
    Block,
    Layout,
    LayoutRow,
    SheetLayout,
)
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.taxonomy import load_taxonomy


def test_rvi_leftovers_map_dividends_balances_and_rates() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="PF Model",
                blocks=[
                    Block(
                        block_id="PF Model!r1",
                        label_col=1,
                        axis=Axis(
                            id="PF Model!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="1", role="relative", period_key="Y1"),
                            ],
                        ),
                        rows=[
                            LayoutRow(row=2, label="Dividend paid", kind="fact"),
                            LayoutRow(
                                row=3,
                                label="Cashflow available for dividend",
                                kind="fact",
                                section_path=["Equity funding"],
                            ),
                            LayoutRow(
                                row=4,
                                label="Total equity returns (dividends)",
                                kind="fact",
                            ),
                            LayoutRow(
                                row=5,
                                label="Straight line depreciation base",
                                kind="fact",
                                section_path=["Straight line depreciation"],
                            ),
                            LayoutRow(
                                row=6,
                                label="Straight line depreciation",
                                kind="fact",
                            ),
                            LayoutRow(row=7, label="Dividend payout ratio period 1", kind="fact"),
                            LayoutRow(row=8, label="PPA hedged volume", kind="fact"),
                            LayoutRow(
                                row=9,
                                label="Variable land lease",
                                kind="fact",
                                section_path=["Operational expenditures (Opex)"],
                            ),
                            LayoutRow(row=10, label="Uncertainty", kind="fact"),
                            LayoutRow(row=11, label="Development & Construction", kind="fact"),
                            LayoutRow(
                                row=12,
                                label="Model start / Construction start",
                                kind="fact",
                            ),
                            LayoutRow(
                                row=13,
                                label="Balance b/f",
                                kind="fact",
                                section_path=["Linear repayment"],
                            ),
                            LayoutRow(
                                row=14,
                                label="Balance c/f",
                                kind="fact",
                                section_path=["Equity funding"],
                            ),
                            LayoutRow(
                                row=15,
                                label="Balance b/f",
                                kind="fact",
                                section_path=["No depreciation (goodwill)"],
                            ),
                            LayoutRow(
                                row=16,
                                label="Balance b/f",
                                kind="fact",
                                section_path=["Straight line depreciation"],
                            ),
                            LayoutRow(
                                row=17,
                                label="Full-wrap EPC",
                                kind="fact",
                                section_path=["Construction & development cost"],
                            ),
                        ],
                    )
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
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    by_key = {(row.label, row.parent_label): row.concept_id for row in doc.rows}
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Dividend paid"] == "cf.dividends"
    assert by_label["Cashflow available for dividend"] == "cf.fcf"
    assert by_label["Total equity returns (dividends)"] == "cf.dividends"
    assert by_label["Straight line depreciation base"] == "pnl.da"
    assert by_label["Straight line depreciation"] == "ops.depreciation_life"
    assert by_label["Dividend payout ratio period 1"] == "val.payout_ratio"
    assert by_label["PPA hedged volume"] == "ops.hedge_ratio"
    assert by_label["Variable land lease"] == "ops.lease_rate"
    assert by_label["Uncertainty"] == "ops.uncertainty"
    assert by_label["Development & Construction"] == "ops.construction_period"
    assert by_label["Model start / Construction start"] == "ops.model_start"
    assert by_key[("Balance b/f", "Linear repayment")] == "bs.debt"
    assert by_key[("Balance c/f", "Equity funding")] == "bs.equity"
    assert by_key[("Balance b/f", "No depreciation (goodwill)")] == "bs.goodwill"
    assert by_key[("Balance b/f", "Straight line depreciation")] == "bs.ppe"
    assert by_label["Full-wrap EPC"] is None


def test_new_pf_concepts_map() -> None:
    taxonomy = load_taxonomy()
    layout = _layout(
        LayoutRow(row=2, label="Cost of capital"),
        LayoutRow(row=3, label="CoC"),
        LayoutRow(row=4, label="Electricity generation"),
        LayoutRow(row=5, label="Loan amount"),
        LayoutRow(row=6, label="Goodwill"),
        LayoutRow(row=7, label="Share capital"),
        LayoutRow(row=8, label="Total debt service"),
        LayoutRow(row=9, label="FCFE / Equity"),
        LayoutRow(row=10, label="Long term assets"),
        LayoutRow(row=11, label="Net Assets"),
        LayoutRow(row=12, label="Availability"),
        sheet="PF Model",
    )
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={},
        embed=None,
        chat=None,
        slots=GrantSlots(),
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Cost of capital"] == "val.coc"
    assert by_label["CoC"] == "val.coc"
    assert by_label["Electricity generation"] == "ops.generation"
    assert by_label["Loan amount"] == "debt.facility_amount"
    assert by_label["Goodwill"] == "bs.goodwill"
    assert by_label["Share capital"] == "bs.share_capital"
    assert by_label["Total debt service"] == "cf.debt_service"
    assert by_label["FCFE / Equity"] == "val.fcfe_equity"
    assert by_label["Long term assets"] == "bs.ppe"
    assert by_label["Net Assets"] == "bs.equity"
    assert by_label["Availability"] == "ops.availability"


def test_pf_labels_hit_drawdown_revenue_cfads_ebitda() -> None:
    taxonomy = load_taxonomy()
    layout = _layout(
        LayoutRow(row=2, label="Gross revenues"),
        LayoutRow(row=3, label="Total revenue"),
        LayoutRow(row=8, label="REVENUE - Passenger Car (PC)"),
        LayoutRow(row=4, label="Drawdowns"),
        LayoutRow(row=5, label="Debt Drawdown (k£)"),
        LayoutRow(row=6, label="Cashflow available for debt service (CFADS)"),
        LayoutRow(row=7, label="Operating Income or Loss (EBITDA)"),
    )
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={},
        embed=None,
        chat=None,
        slots=GrantSlots(),
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Gross revenues"] == "pnl.revenue"
    assert by_label["Total revenue"] == "pnl.revenue"
    assert by_label["REVENUE - Passenger Car (PC)"] == "pnl.revenue"
    assert by_label["Drawdowns"] == "cf.drawdown"
    assert by_label["Debt Drawdown (k£)"] == "cf.drawdown"
    assert by_label["Cashflow available for debt service (CFADS)"] == "cf.cfads"
    assert by_label["Operating Income or Loss (EBITDA)"] == "pnl.ebitda"
