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
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
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
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
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
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    row = doc.rows[0]
    assert row.concept_id == "cf.equity_cashflow"
    assert row.alternatives, "top candidates must remain even when mapping is close"


def test_income_tax_on_cfs_is_cash_tax_not_pnl() -> None:
    taxonomy = load_taxonomy()
    cfs = _layout(LayoutRow(row=2, label="Income Tax"), sheet="CFS")
    pnl = _layout(LayoutRow(row=2, label="Income Tax"), sheet="P&L")
    cfs_doc = map_layout(
        cfs, taxonomy=taxonomy, glossary={}, embed=None, chat=None, slots=GrantSlots(),
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    pnl_doc = map_layout(
        pnl, taxonomy=taxonomy, glossary={}, embed=None, chat=None, slots=GrantSlots(),
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
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
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    by_key = {(row.sheet, row.label): row.concept_id for row in doc.rows}
    assert by_key[("P&L", "Income Tax")] == "pnl.tax"
    assert by_key[("CFS", "Income Tax")] == "cf.tax_paid"
