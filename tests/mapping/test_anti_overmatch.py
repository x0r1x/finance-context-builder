from __future__ import annotations

from tests.helpers.ports import GrantSlots

from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.models import Concept


def _layout(label: str) -> Layout:
    return Layout(
        sheets=[
            SheetLayout(
                name="Liquidity_Analysis",
                blocks=[
                    Block(
                        block_id="Liquidity_Analysis!r1",
                        label_col=1,
                        axis=Axis(
                            id="Liquidity_Analysis!r1",
                            row=1,
                            headers=[
                                AxisHeader(
                                    col=2, text="W1", role="forecast", period_key="2026-01-11"
                                )
                            ],
                        ),
                        rows=[LayoutRow(row=2, label=label, kind="fact")],
                    )
                ],
            )
        ]
    )


def test_dscr_does_not_map_to_debt() -> None:
    taxonomy = [
        Concept(id="bs.debt", labels=["Debt"], anti_labels=["dscr", "coverage"]),
        Concept(id="cov.dscr", labels=["DSCR"], value_kind="ratio"),
    ]
    doc = map_layout(
        _layout("DSCR (Op CF / Debt Service"),
        taxonomy=taxonomy,
        glossary={},
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    assert doc.rows[0].concept_id == "cov.dscr"
    assert doc.rows[0].concept_id != "bs.debt"


def test_leverage_does_not_map_to_revenue() -> None:
    taxonomy = [
        Concept(id="pnl.revenue", labels=["Revenue"], anti_labels=["leverage"]),
        Concept(id="cov.leverage_limit", labels=["Leverage covenant"], value_kind="ratio"),
    ]
    doc = map_layout(
        _layout("Leverage (TL / Revenue TTM"),
        taxonomy=taxonomy,
        glossary={},
        embed=None,
        chat=None,
        slots=GrantSlots(),
    )
    assert doc.rows[0].concept_id != "pnl.revenue"
