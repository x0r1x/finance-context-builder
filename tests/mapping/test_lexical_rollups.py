from __future__ import annotations

from tests.helpers.policy import llm_workers, slot_wait, thresholds
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
from finance_context.mapping.taxonomy import load_taxonomy


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
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
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
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
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
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
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
