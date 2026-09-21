from __future__ import annotations

from tests.helpers.ports import GrantSlots

from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.taxonomy import load_taxonomy


def _headers() -> list[AxisHeader]:
    return [AxisHeader(col=2, text="Y1", role="forecast", period_key="Y1")]


def test_cfs_alias_crosswalks_pnl_revenue_to_receipts() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="P&L",
                blocks=[
                    Block(
                        block_id="P&L!r1",
                        label_col=1,
                        axis=Axis(id="P&L!r1", row=1, headers=_headers()),
                        rows=[LayoutRow(row=10, label="Gross revenues", kind="fact")],
                    )
                ],
            ),
            SheetLayout(
                name="CFS",
                blocks=[
                    Block(
                        block_id="CFS!r1",
                        label_col=1,
                        axis=Axis(id="CFS!r1", row=1, headers=_headers()),
                        rows=[
                            LayoutRow(
                                row=4,
                                label="Gross Revenues",
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
            "row": 4,
            "col": 2,
            "addr": "B4",
            "formula_raw": "='P&L'!B10",
            "formula_template": "='P&L'!R[6]C[0]",
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
    assert by_key[("P&L", "Gross revenues")] == "pnl.revenue"
    assert by_key[("CFS", "Gross Revenues")] == "cf.receipts"


def test_sources_equity_does_not_keep_bs_alias() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Balance Sheet",
                blocks=[
                    Block(
                        block_id="BS!r1",
                        label_col=1,
                        axis=Axis(id="BS!r1", row=1, headers=_headers()),
                        rows=[LayoutRow(row=9, label="Equity", kind="fact")],
                    )
                ],
            ),
            SheetLayout(
                name="Construction",
                blocks=[
                    Block(
                        block_id="Construction!r1",
                        label_col=1,
                        axis=Axis(id="Construction!r1", row=1, headers=_headers()),
                        rows=[
                            LayoutRow(
                                row=5,
                                label="Equity (k£)",
                                kind="fact",
                                section_path=["Sources"],
                            )
                        ],
                    )
                ],
            ),
        ]
    )
    cells = [
        {
            "sheet": "Construction",
            "row": 5,
            "col": 2,
            "addr": "B5",
            "formula_raw": "='Balance Sheet'!B9",
            "formula_template": "='Balance Sheet'!R[4]C[0]",
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
    assert by_key[("Balance Sheet", "Equity")] == "bs.equity"
    assert by_key[("Construction", "Equity (k£)")] != "bs.equity"


def test_construction_cost_under_uses_has_secondary_uses() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Construction",
                blocks=[
                    Block(
                        block_id="Construction!r1",
                        label_col=1,
                        axis=Axis(id="Construction!r1", row=1, headers=_headers()),
                        rows=[
                            LayoutRow(
                                row=6,
                                label="Construction Cost (k£)",
                                kind="fact",
                                section_path=["Uses"],
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
    assert row.concept_id == "cf.capex"
    assert row.context_role == "uses"
    assert "cf.uses" in row.secondary_concepts
