from __future__ import annotations

from tests.helpers.ports import GrantSlots

from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping.cascade import map_layout
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
