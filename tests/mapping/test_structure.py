from __future__ import annotations

from tests.helpers.ports import GrantSlots

from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.models import Concept


def _headers() -> list[AxisHeader]:
    return [
        AxisHeader(col=2, text="W1", role="forecast", period_key="2026-01-11"),
        AxisHeader(col=3, text="W2", role="forecast", period_key="2026-01-18"),
    ]


def test_alias_copies_concept_from_source_row() -> None:
    taxonomy = [
        Concept(id="bs.cash", labels=["Closing cash"]),
        Concept(id="cf.receipts", labels=["Receipts"]),
    ]
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Weekly_Forecast",
                blocks=[
                    Block(
                        block_id="Weekly_Forecast!r1",
                        label_col=1,
                        axis=Axis(id="Weekly_Forecast!r1", row=1, headers=_headers()),
                        rows=[LayoutRow(row=10, label="Closing cash", kind="fact")],
                    )
                ],
            ),
            SheetLayout(
                name="Dashboard",
                blocks=[
                    Block(
                        block_id="Dashboard!r1",
                        label_col=1,
                        axis=Axis(id="Dashboard!r1", row=1, headers=_headers()),
                        rows=[LayoutRow(row=5, label="Dash close", kind="fact")],
                    )
                ],
            ),
        ]
    )
    cells = [
        {
            "sheet": "Dashboard",
            "row": 5,
            "col": 2,
            "addr": "B5",
            "formula_raw": "=Weekly_Forecast!B10",
            "formula_template": "=Weekly_Forecast!R[5]C[0]",
            "ast_json": None,
            "cached_value": "1",
        },
        {
            "sheet": "Dashboard",
            "row": 5,
            "col": 3,
            "addr": "C5",
            "formula_raw": "=Weekly_Forecast!C10",
            "formula_template": "=Weekly_Forecast!R[5]C[0]",
            "cached_value": "2",
        },
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
    assert by_label["Closing cash"].concept_id == "bs.cash"
    assert by_label["Dash close"].concept_id == "bs.cash"
    assert by_label["Dash close"].source == "structure"
    assert any(rel.kind == "alias" for rel in doc.relations)


def test_sum_of_receipts_is_receipts_not_net() -> None:
    taxonomy = [
        Concept(id="cf.receipts", labels=["Collections", "Receipts"]),
        Concept(id="cf.net", labels=["Net cash flow"]),
    ]
    layout = Layout(
        sheets=[
            SheetLayout(
                name="CF",
                blocks=[
                    Block(
                        block_id="CF!r1",
                        label_col=1,
                        axis=Axis(id="CF!r1", row=1, headers=_headers()),
                        rows=[
                            LayoutRow(row=11, label="Collections", kind="fact"),
                            LayoutRow(row=12, label="Other collections", kind="fact"),
                            LayoutRow(row=13, label="Total Inflows", kind="fact"),
                        ],
                    )
                ],
            )
        ]
    )
    cells = [
        {
            "sheet": "CF",
            "row": 13,
            "col": 2,
            "addr": "B13",
            "formula_raw": "=SUM(B11:B12)",
            "cached_value": "30",
        },
        {
            "sheet": "CF",
            "row": 13,
            "col": 3,
            "addr": "C13",
            "formula_raw": "=SUM(C11:C12)",
            "cached_value": "40",
        },
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
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Collections"] == "cf.receipts"
    assert by_label["Total Inflows"] == "cf.receipts"
    assert by_label["Total Inflows"] != "cf.net"


def test_weekly_proration_is_money_not_ratio() -> None:
    taxonomy = [
        Concept(id="cf.receipts.product", labels=["Product collections"], broader="cf.receipts"),
        Concept(id="cf.receipts", labels=["Collections"]),
    ]
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Weekly_Forecast",
                blocks=[
                    Block(
                        block_id="Weekly_Forecast!r1",
                        label_col=1,
                        axis=Axis(id="Weekly_Forecast!r1", row=1, headers=_headers()),
                        rows=[LayoutRow(row=8, label="Product Collections", kind="fact")],
                    )
                ],
            )
        ]
    )
    cells = [
        {
            "sheet": "Weekly_Forecast",
            "row": 8,
            "col": 2,
            "addr": "B8",
            "formula_raw": "=Product_Collections/Weeks_Per_Month",
            "cached_value": "10",
        },
        {
            "sheet": "Weekly_Forecast",
            "row": 8,
            "col": 3,
            "addr": "C8",
            "formula_raw": "=Product_Collections/Weeks_Per_Month",
            "cached_value": "10",
        },
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
    assert doc.rows[0].concept_id == "cf.receipts.product"


def test_percent_row_does_not_map_to_headcount() -> None:
    taxonomy = [
        Concept(id="ops.headcount", labels=["Headcount"], value_kind="count"),
        Concept(id="cov.llcr", labels=["LLCR"], value_kind="ratio"),
    ]
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Ops",
                blocks=[
                    Block(
                        block_id="Ops!r1",
                        label_col=1,
                        axis=Axis(id="Ops!r1", row=1, headers=_headers()),
                        rows=[LayoutRow(row=2, label="Occupancy", kind="fact")],
                    )
                ],
            )
        ]
    )
    cells = [
        {
            "sheet": "Ops",
            "row": 2,
            "col": 2,
            "addr": "B2",
            "cached_value": "0.8",
            "number_format": "0%",
            "formula_raw": None,
        },
        {
            "sheet": "Ops",
            "row": 2,
            "col": 3,
            "addr": "C2",
            "cached_value": "0.81",
            "number_format": "0%",
            "formula_raw": None,
        },
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
    assert doc.rows[0].concept_id is None
    assert doc.rows[0].source == "question"
