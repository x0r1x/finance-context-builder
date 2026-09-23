from __future__ import annotations

from tests.helpers.ports import GrantSlots

from finance_context.layout.models import (
    Axis,
    AxisHeader,
    Block,
    Layout,
    LayoutRow,
    RowCell,
    SheetLayout,
)
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.structure import BookView, analyze_structure, build_row_context
from finance_context.mapping.taxonomy import load_taxonomy


def _layout(*rows: LayoutRow, sheet: str = "P&L") -> Layout:
    headers = [
        AxisHeader(col=2, text="2023", role="historical", period_key="2023"),
        AxisHeader(col=3, text="2024E", role="forecast", period_key="2024"),
    ]
    return Layout(
        sheets=[
            SheetLayout(
                name=sheet,
                blocks=[
                    Block(
                        block_id=f"{sheet}!r1",
                        label_col=1,
                        axis=Axis(id=f"{sheet}!r1", row=1, headers=headers),
                        rows=list(rows),
                    )
                ],
            )
        ]
    )


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
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Gross revenues"] == "pnl.revenue"
    assert by_label["Total revenue"] == "pnl.revenue"
    assert by_label["REVENUE - Passenger Car (PC)"] == "pnl.revenue"
    assert by_label["Drawdowns"] == "cf.drawdown"
    assert by_label["Debt Drawdown (k£)"] == "cf.drawdown"
    assert by_label["Cashflow available for debt service (CFADS)"] == "cf.cfads"
    assert by_label["Operating Income or Loss (EBITDA)"] == "pnl.ebitda"


def test_parent_section_rolls_up_capex_opex_da() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Construction",
                blocks=[
                    Block(
                        block_id="Construction!r1",
                        label_col=1,
                        axis=Axis(
                            id="Construction!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="1", role="relative", period_key="Y1"),
                                AxisHeader(col=3, text="2", role="relative", period_key="Y2"),
                            ],
                        ),
                        rows=[
                            LayoutRow(row=5, label="CAPEX", kind="abstract"),
                            LayoutRow(
                                row=6,
                                label="Construction Cost (k£)",
                                kind="fact",
                                parent_row=5,
                                section_path=["CAPEX"],
                            ),
                            LayoutRow(
                                row=8,
                                label="Total Costs (k£)",
                                kind="fact",
                                parent_row=5,
                                section_path=["CAPEX"],
                            ),
                            LayoutRow(
                                row=20,
                                label="Maintenance & SPV costs",
                                kind="fact",
                                section_path=["COSTS"],
                            ),
                            LayoutRow(
                                row=13,
                                label="Amortization (k£)",
                                kind="fact",
                                section_path=["D&A"],
                            ),
                            LayoutRow(row=25, label="Net Profit", kind="fact"),
                            LayoutRow(
                                row=9,
                                label="Cashflow available for equity (FCFE)",
                                kind="fact",
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
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Construction Cost (k£)"] == "cf.capex"
    assert by_label["Total Costs (k£)"] == "cf.capex"
    assert by_label["Maintenance & SPV costs"] == "pnl.opex"
    assert by_label["Amortization (k£)"] == "pnl.da"
    assert by_label["Net Profit"] == "pnl.net_income"
    assert by_label["Cashflow available for equity (FCFE)"] == "cf.fcf"


def test_parent_rollup_skips_lifetime_capacity_inflation_balance() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Assumptions",
                blocks=[
                    Block(
                        block_id="Assumptions!r1",
                        label_col=1,
                        axis=Axis(
                            id="Assumptions!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="1", role="relative", period_key="Y1"),
                            ],
                        ),
                        rows=[
                            LayoutRow(
                                row=2,
                                label="Operating lifetime",
                                kind="fact",
                                section_path=["Operating costs"],
                            ),
                            LayoutRow(
                                row=3,
                                label="Number of wind turbines",
                                kind="fact",
                                section_path=["Revenue"],
                            ),
                            LayoutRow(
                                row=4,
                                label="Installed capacity MW",
                                kind="fact",
                                section_path=["Revenue"],
                            ),
                            LayoutRow(
                                row=5,
                                label="PPA escalation",
                                kind="fact",
                                section_path=["Revenue"],
                            ),
                            LayoutRow(
                                row=6,
                                label="Share premium",
                                kind="fact",
                                section_path=["Uses"],
                            ),
                            LayoutRow(
                                row=7,
                                label="Balance b/f",
                                kind="fact",
                                section_path=["CAPEX"],
                            ),
                            LayoutRow(
                                row=8,
                                label="Straight line depreciation",
                                kind="fact",
                                section_path=["D&A"],
                            ),
                            LayoutRow(
                                row=9,
                                label="CPI",
                                kind="fact",
                                section_path=["Revenue"],
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
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Operating lifetime"] == "ops.operating_period"
    assert by_label["Number of wind turbines"] == "ops.asset_count"
    assert by_label["Installed capacity MW"] == "ops.capacity"
    assert by_label["PPA escalation"] == "ops.inflation"
    assert by_label["Share premium"] == "bs.share_premium"
    assert by_label["Balance b/f"] not in {"cf.capex", "pnl.opex", "pnl.revenue"}
    assert by_label["Straight line depreciation"] != "pnl.da"
    assert by_label["CPI"] == "ops.cpi"


def test_packt_leftovers_and_cash_not_balance() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Ratios",
                blocks=[
                    Block(
                        block_id="Ratios!r1",
                        label_col=1,
                        axis=Axis(
                            id="Ratios!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="1", role="relative", period_key="Y1"),
                            ],
                        ),
                        rows=[
                            LayoutRow(row=2, label="Cash Flow", kind="fact"),
                            LayoutRow(row=3, label="Total Cash in", kind="fact"),
                            LayoutRow(row=4, label="Total Cash out", kind="fact"),
                            LayoutRow(row=5, label="Arrangement fee", kind="fact"),
                            LayoutRow(row=6, label="Capitalized interests", kind="fact"),
                            LayoutRow(row=7, label="TRAFFIC - Passenger Car (PC)", kind="fact"),
                            LayoutRow(row=8, label="Dividends earned", kind="fact"),
                            LayoutRow(row=9, label="Total Investment", kind="fact"),
                            LayoutRow(row=10, label="Debt up-front fee", kind="fact"),
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
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Cash Flow"] == "cf.net"
    assert by_label["Cash Flow"] != "bs.cash"
    assert by_label["Total Cash in"] == "cf.receipts"
    assert by_label["Total Cash out"] == "cf.disbursements"
    assert by_label["Arrangement fee"] == "debt.arrangement_fee"
    assert by_label["Capitalized interests"] == "pnl.interest"
    assert by_label["TRAFFIC - Passenger Car (PC)"] == "pnl.volume"
    assert by_label["Dividends earned"] == "cf.dividends"
    assert by_label["Total Investment"] == "val.total_investment"
    assert by_label["Debt up-front fee"] != "bs.debt"


def test_cash_in_hand_and_injected_equity() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Construction",
                blocks=[
                    Block(
                        block_id="Construction!r1",
                        label_col=1,
                        axis=Axis(
                            id="Construction!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="1", role="relative", period_key="Y1"),
                            ],
                        ),
                        rows=[
                            LayoutRow(
                                row=2,
                                label="Equity (k£)",
                                kind="fact",
                                section_path=["Sources"],
                            ),
                            LayoutRow(row=3, label="Equity Injected", kind="fact"),
                            LayoutRow(
                                row=4,
                                label="Cash in hand",
                                kind="fact",
                                section_path=["Balance Sheet"],
                            ),
                            LayoutRow(row=5, label="Cash in hands", kind="fact"),
                            LayoutRow(row=6, label="Cash out End of Concession", kind="fact"),
                            LayoutRow(
                                row=7,
                                label="Fixed land lease period 1",
                                kind="fact",
                                section_path=["Operational expenditures (Opex)"],
                            ),
                            LayoutRow(
                                row=8,
                                label="Variable land lease",
                                kind="fact",
                                section_path=["Cashflow Statement"],
                            ),
                        ],
                    )
                ],
            )
        ]
    )
    cells = [
        {
            "sheet": "Construction",
            "row": 8,
            "col": 2,
            "addr": "B8",
            "cached_value": "1200",
            "number_format": "#,##0",
        }
    ]
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={},
        cells=cells,
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    by_label = {row.label: row for row in doc.rows}
    assert by_label["Equity (k£)"].concept_id == "cf.equity_issue"
    assert by_label["Equity Injected"].concept_id == "cf.equity_issue"
    assert by_label["Cash in hand"].concept_id == "bs.cash"
    assert by_label["Cash in hands"].concept_id == "bs.cash"
    assert by_label["Cash out End of Concession"].concept_id == "cf.disbursements"
    assert by_label["Fixed land lease period 1"].concept_id == "pnl.opex"
    assert by_label["Variable land lease"].concept_id != "ops.lease_rate"
    assert by_label["Cash in hand"].alternatives


def test_total_cash_in_cash_out_is_equity_cashflow() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Ratios",
                blocks=[
                    Block(
                        block_id="Ratios!r1",
                        label_col=1,
                        axis=Axis(
                            id="Ratios!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="1", role="relative", period_key="Y1"),
                            ],
                        ),
                        rows=[
                            LayoutRow(
                                row=27,
                                label="Total Cash in/Cash out",
                                kind="fact",
                                section_path=["Equity IRR"],
                            )
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
    )
    row = doc.rows[0]
    assert row.concept_id == "cf.equity_cashflow"
    assert row.alternatives, "top candidates must remain even when mapping is close"


def test_unit_column_sets_value_kind() -> None:
    taxonomy = load_taxonomy()
    rows = [
        LayoutRow(
            row=8,
            label="Concession Duration",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
        LayoutRow(
            row=9,
            label="Tax Rate",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
        LayoutRow(
            row=10,
            label="Toll Rate",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
    ]
    block = Block(
        block_id="Input Assumptions!r5",
        label_col=2,
        kind="params",
        axis=Axis(
            id="Input Assumptions!r5",
            row=5,
            headers=[
                AxisHeader(col=4, text="Values", role="value", period_key="value"),
            ],
        ),
        rows=rows,
    )
    layout = Layout(
        sheets=[SheetLayout(name="Input Assumptions", blocks=[block])]
    )
    cells = [
        {
            "sheet": "Input Assumptions",
            "row": 8,
            "col": 3,
            "addr": "C8",
            "cached_value": "years",
        },
        {
            "sheet": "Input Assumptions",
            "row": 9,
            "col": 3,
            "addr": "C9",
            "cached_value": "%",
        },
        {
            "sheet": "Input Assumptions",
            "row": 10,
            "col": 3,
            "addr": "C10",
            "cached_value": "£",
        },
    ]
    book = BookView(layout, cells, taxonomy)
    analyze_structure(book)
    kinds = {
        row.label: build_row_context(book, "Input Assumptions", block, row, None, []).value_kind
        for row in rows
    }
    assert kinds["Concession Duration"] == "count"
    assert kinds["Tax Rate"] == "rate"
    assert kinds["Toll Rate"] == "money"
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={},
        cells=cells,
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    by_label = {row.label: row for row in doc.rows}
    assert by_label["Tax Rate"].concept_id == "pnl.tax_rate"
    assert by_label["Toll Rate"].concept_id == "pnl.price"
    assert by_label["Concession Duration"].concept_id == "ops.concession_duration"
    assert by_label["Concession Duration"].article_role == "assumption"

def test_income_tax_on_cfs_is_cash_tax_not_pnl() -> None:
    taxonomy = load_taxonomy()
    cfs = _layout(LayoutRow(row=2, label="Income Tax"), sheet="CFS")
    pnl = _layout(LayoutRow(row=2, label="Income Tax"), sheet="P&L")
    cfs_doc = map_layout(
        cfs, taxonomy=taxonomy, glossary={}, embed=None, chat=None, slots=GrantSlots()
    )
    pnl_doc = map_layout(
        pnl, taxonomy=taxonomy, glossary={}, embed=None, chat=None, slots=GrantSlots()
    )
    assert cfs_doc.rows[0].concept_id == "cf.tax_paid"
    assert pnl_doc.rows[0].concept_id == "pnl.tax"


def test_cfs_income_tax_alias_does_not_copy_pnl() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="P&L",
                blocks=[
                    Block(
                        block_id="P&L!r1",
                        label_col=1,
                        axis=Axis(
                            id="P&L!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="2024", role="forecast", period_key="2024"),
                            ],
                        ),
                        rows=[LayoutRow(row=23, label="Income Tax", kind="fact")],
                    )
                ],
            ),
            SheetLayout(
                name="CFS",
                blocks=[
                    Block(
                        block_id="CFS!r1",
                        label_col=1,
                        axis=Axis(
                            id="CFS!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="2024", role="forecast", period_key="2024"),
                            ],
                        ),
                        rows=[
                            LayoutRow(
                                row=12,
                                label="Income Tax",
                                kind="fact",
                                section_path=["Cashflow Statement"],
                            )
                        ],
                    )
                ],
            ),
        ]
    )
    cells = [
        {
            "sheet": "CFS",
            "row": 12,
            "col": 2,
            "addr": "B12",
            "formula_raw": "=P&L!B23",
            "cached_value": "1",
        }
    ]
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={},
        cells=cells,
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    by_key = {(row.sheet, row.label): row.concept_id for row in doc.rows}
    assert by_key[("P&L", "Income Tax")] == "pnl.tax"
    assert by_key[("CFS", "Income Tax")] == "cf.tax_paid"


def test_dscr_minimum_is_limit_not_observed_dscr() -> None:
    taxonomy = load_taxonomy()
    layout = _layout(
        LayoutRow(row=2, label="DSCR minimum"),
        LayoutRow(row=3, label="Average Debt Service Coverage Ratio (DSCR)"),
        LayoutRow(row=4, label="Minimum Debt Service Coverage Ratio (DSCR)"),
        sheet="Funding assumptions",
    )
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={},
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["DSCR minimum"] == "cov.dscr_limit"
    assert by_label["Average Debt Service Coverage Ratio (DSCR)"] == "cov.dscr"
    assert by_label["Minimum Debt Service Coverage Ratio (DSCR)"] == "cov.dscr"


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


def test_concession_and_operations_duration_are_not_the_same_concept() -> None:
    taxonomy = load_taxonomy()
    rows = [
        LayoutRow(
            row=8,
            label="Concession Duration",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
        LayoutRow(
            row=9,
            label="Construction Duration",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
        LayoutRow(
            row=10,
            label="Operations Duration",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
    ]
    block = Block(
        block_id="Input Assumptions!r5",
        label_col=2,
        kind="params",
        axis=Axis(
            id="Input Assumptions!r5",
            row=5,
            headers=[AxisHeader(col=4, text="Values", role="value", period_key="value")],
        ),
        rows=rows,
    )
    layout = Layout(sheets=[SheetLayout(name="Input Assumptions", blocks=[block])])
    cells = [
        {"sheet": "Input Assumptions", "row": row, "col": 3, "addr": f"C{row}", "cached_value": "years"}
        for row in (8, 9, 10)
    ]
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={
            ("concession duration", ""): "ops.lifetime",
            ("operations duration", ""): "ops.lifetime",
        },
        cells=cells,
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Concession Duration"] == "ops.concession_duration"
    assert by_label["Operations Duration"] == "ops.operating_period"
    assert by_label["Construction Duration"] == "ops.construction_period"
    assert by_label["Concession Duration"] != by_label["Operations Duration"]


def test_inflation_fees_and_cfs_opex_split() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Input Assumptions",
                blocks=[
                    Block(
                        block_id="IA!r1",
                        label_col=1,
                        axis=Axis(
                            id="IA!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="1", role="relative", period_key="Y1"),
                            ],
                        ),
                        rows=[
                            LayoutRow(row=2, label="Inflation per year", kind="fact"),
                            LayoutRow(
                                row=3,
                                label="Inflation per year (costs) from beginning of concession",
                                kind="fact",
                            ),
                            LayoutRow(row=4, label="Engagement fee", kind="fact"),
                        ],
                    )
                ],
            ),
            SheetLayout(
                name="CFS",
                blocks=[
                    Block(
                        block_id="CFS!r1",
                        label_col=1,
                        axis=Axis(
                            id="CFS!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="1", role="relative", period_key="Y1"),
                            ],
                        ),
                        rows=[
                            LayoutRow(
                                row=5,
                                label="OPEX",
                                kind="fact",
                                section_path=["Cashflow Statement"],
                            ),
                            LayoutRow(
                                row=6,
                                label="Interest",
                                kind="fact",
                                section_path=["Cashflow Statement"],
                            ),
                        ],
                    )
                ],
            ),
        ]
    )
    cells = [
        {
            "sheet": "Input Assumptions",
            "row": 4,
            "col": 2,
            "addr": "B4",
            "cached_value": "0.01",
            "number_format": "0%",
        }
    ]
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={},
        cells=cells,
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    by_key = {(row.sheet, row.label): row.concept_id for row in doc.rows}
    assert by_key[("Input Assumptions", "Inflation per year")] == "ops.inflation_revenue"
    assert (
        by_key[
            (
                "Input Assumptions",
                "Inflation per year (costs) from beginning of concession",
            )
        ]
        == "ops.inflation_cost"
    )
    assert by_key[("Input Assumptions", "Engagement fee")] == "debt.engagement_fee_rate"
    assert by_key[("CFS", "OPEX")] == "cf.opex_paid"
    assert by_key[("CFS", "Interest")] == "cf.interest_paid"


def test_rvi_section_totals_cpi_and_cash_opex() -> None:
    taxonomy = load_taxonomy()
    headers = [
        AxisHeader(col=3, text="2024", role="historical", period_key="2024"),
        AxisHeader(col=4, text="2025", role="forecast", period_key="2025"),
    ]
    layout = Layout(
        sheets=[
            SheetLayout(
                name="PF Model",
                blocks=[
                    Block(
                        block_id="PF Model!r1",
                        label_col=1,
                        axis=Axis(id="PF Model!r1", row=1, headers=headers),
                        rows=[
                            LayoutRow(row=2, label="Balance Sheet", kind="abstract"),
                            LayoutRow(
                                row=4,
                                label="Total",
                                parent_row=2,
                                section_path=["Balance Sheet", "Non-current assets"],
                            ),
                            LayoutRow(
                                row=6,
                                label="Total",
                                parent_row=2,
                                section_path=["Balance Sheet", "Current assets"],
                            ),
                            LayoutRow(row=10, label="Linear repayment", kind="abstract"),
                            LayoutRow(
                                row=11,
                                label="Balance b/f",
                                parent_row=10,
                                section_path=["Senior Debt", "Linear repayment"],
                            ),
                            LayoutRow(
                                row=12,
                                label="Principal repayment",
                                parent_row=10,
                                section_path=["Senior Debt", "Linear repayment"],
                            ),
                            LayoutRow(
                                row=13,
                                label="Balance c/f",
                                parent_row=10,
                                section_path=["Senior Debt", "Linear repayment"],
                            ),
                            LayoutRow(
                                row=165,
                                label="CPI",
                                section_path=["Inflation profiles (annually)"],
                                cells=[RowCell(col=2, role="unit")],
                            ),
                            LayoutRow(
                                row=172,
                                label="CPI",
                                section_path=["Inflation profiles (annually)", "Indexation"],
                                cells=[RowCell(col=2, role="unit")],
                            ),
                        ],
                    )
                ],
            ),
            SheetLayout(
                name="Cashflow Statement",
                blocks=[
                    Block(
                        block_id="Cashflow Statement!r1",
                        label_col=1,
                        axis=Axis(id="Cashflow Statement!r1", row=1, headers=headers),
                        rows=[
                            LayoutRow(
                                row=216,
                                label="Commercial Management",
                                section_path=["Cashflow Statement", "Operating costs"],
                            ),
                            LayoutRow(
                                row=218,
                                label="O&M period 1",
                                section_path=["Cashflow Statement", "Operating costs"],
                            ),
                            LayoutRow(
                                row=221,
                                label="Technical Management",
                                section_path=["Cashflow Statement", "Operating costs"],
                            ),
                            LayoutRow(
                                row=223,
                                label="Balancing costs",
                                section_path=["Cashflow Statement", "Operating costs"],
                            ),
                            LayoutRow(
                                row=230,
                                label="Variable land lease",
                                section_path=["Cashflow Statement", "Operating costs"],
                            ),
                        ],
                    )
                ],
            ),
        ]
    )
    cells = [
        {"sheet": "PF Model", "row": 165, "col": 2, "cached_value": "%"},
        {"sheet": "PF Model", "row": 165, "col": 3, "cached_value": "0.02", "number_format": "0%"},
        {"sheet": "PF Model", "row": 172, "col": 2, "cached_value": "Index"},
        {"sheet": "PF Model", "row": 172, "col": 3, "cached_value": "1.02"},
        {"sheet": "Cashflow Statement", "row": 216, "col": 3, "cached_value": "10"},
        {"sheet": "Cashflow Statement", "row": 230, "col": 3, "cached_value": "4"},
    ]
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={("Total", "Balance Sheet"): "bs.assets_noncurrent"},
        cells=cells,
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    by_key = {(row.sheet, row.row): row.concept_id for row in doc.rows}
    assert by_key[("PF Model", 4)] == "bs.assets_noncurrent"
    assert by_key[("PF Model", 6)] == "bs.assets_current"
    assert by_key[("PF Model", 11)] == "bs.debt"
    assert by_key[("PF Model", 12)] == "cf.repayment"
    assert by_key[("PF Model", 13)] == "bs.debt"
    assert by_key[("PF Model", 165)] == "ops.inflation"
    assert by_key[("PF Model", 172)] == "ops.cpi"
    for row_n in (216, 218, 221, 223, 230):
        assert by_key[("Cashflow Statement", row_n)] == "cf.opex_paid"

