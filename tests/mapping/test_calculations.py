from __future__ import annotations

from tests.helpers.ports import GrantSlots

from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.models import CalcTerm, Calculation, Concept, TaxonomyDocument
from finance_context.mapping.structure import (
    BookView,
    StructureSignal,
    analyze_structure,
    build_row_context,
)
from finance_context.mapping.taxonomy import register_document


def test_declared_difference_proposes_parent() -> None:
    taxonomy = [
        Concept(id="cf.receipts", labels=["Inflows"]),
        Concept(id="cf.disbursements", labels=["Outflows"]),
        Concept(id="cf.net", labels=["Net"]),
    ]
    layout = Layout(
        sheets=[
            SheetLayout(
                name="CF",
                blocks=[
                    Block(
                        block_id="CF!r1",
                        label_col=1,
                        axis=Axis(
                            id="CF!r1",
                            row=1,
                            headers=[
                                AxisHeader(
                                    col=2, text="W1", role="forecast", period_key="2026-01-11"
                                )
                            ],
                        ),
                        rows=[
                            LayoutRow(row=2, label="Inflows", kind="fact"),
                            LayoutRow(row=3, label="Outflows", kind="fact"),
                            LayoutRow(row=4, label="Net", kind="fact"),
                        ],
                    )
                ],
            )
        ]
    )
    cells = [
        {
            "sheet": "CF",
            "row": 4,
            "col": 2,
            "addr": "B4",
            "formula_raw": "=B2-B3",
            "cached_value": "1",
        }
    ]
    book = BookView(
        layout,
        cells,
        taxonomy,
        calculations=[
            Calculation(
                parent="cf.net",
                terms=[
                    CalcTerm(concept="cf.receipts", weight=1),
                    CalcTerm(concept="cf.disbursements", weight=-1),
                ],
            )
        ],
    )
    analyze_structure(book)
    book.concepts["CF|2|CF!r1"] = "cf.receipts"
    book.concepts["CF|3|CF!r1"] = "cf.disbursements"
    ctx = build_row_context(
        book, "CF", layout.sheets[0].blocks[0], layout.sheets[0].blocks[0].rows[2], None, []
    )
    proposed = StructureSignal().propose(ctx, book)
    assert any(item.concept_id == "cf.net" for item in proposed)


def test_sum_mapped_to_difference_concept_is_abstained() -> None:
    taxonomy = [
        Concept(id="cf.receipts", labels=["Collections"]),
        Concept(id="cf.disbursements", labels=["Outflows"]),
        Concept(id="cf.net", labels=["Total Inflows", "Net cash flow"]),
    ]
    register_document(
        TaxonomyDocument(
            concepts=taxonomy,
            calculations=[
                Calculation(
                    parent="cf.net",
                    terms=[
                        CalcTerm(concept="cf.receipts", weight=1),
                        CalcTerm(concept="cf.disbursements", weight=-1),
                    ],
                )
            ],
        )
    )
    layout = Layout(
        sheets=[
            SheetLayout(
                name="CF",
                blocks=[
                    Block(
                        block_id="CF!r1",
                        label_col=1,
                        axis=Axis(
                            id="CF!r1",
                            row=1,
                            headers=[
                                AxisHeader(
                                    col=2, text="W1", role="forecast", period_key="2026-01-11"
                                ),
                                AxisHeader(
                                    col=3, text="W2", role="forecast", period_key="2026-01-18"
                                ),
                            ],
                        ),
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
    by_label = {row.label: row for row in doc.rows}
    assert by_label["Total Inflows"].concept_id is None
    assert by_label["Total Inflows"].exclusion_reason == "calculation_conflict"
