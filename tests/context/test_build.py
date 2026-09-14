from __future__ import annotations

from finance_context.context.build import build_context
from finance_context.layout.models import Axis, AxisHeader, Block, Layout, LayoutRow, SheetLayout
from finance_context.mapping.models import MappedRow, MappingDocument


def _layout() -> Layout:
    return Layout(
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
                                    col=2, text="2023", role="historical", period_key="2023"
                                ),
                                AxisHeader(
                                    col=3, text="2024E", role="forecast", period_key="2024E"
                                ),
                            ],
                        ),
                        rows=[LayoutRow(row=2, label="Opening cash")],
                    )
                ],
            )
        ]
    )


def test_build_keeps_cached_value_and_cell_ref() -> None:
    mapping = MappingDocument(
        rows=[
            MappedRow(
                row_key="CF|2|CF!r1",
                sheet="CF",
                row=2,
                block_id="CF!r1",
                label="Opening cash",
                concept_id="bs.cash",
                article_role="database_like",
                source="glossary",
                confidence="high",
                score=1.0,
            )
        ]
    )
    cells = [
        {
            "sheet": "CF",
            "row": 2,
            "col": 2,
            "addr": "B2",
            "formula_raw": None,
            "formula_template": None,
            "unparsed": False,
            "cached_value": "320000",
            "number_format": '"$"#,##0',
        },
        {
            "sheet": "CF",
            "row": 2,
            "col": 3,
            "addr": "C2",
            "formula_raw": "=B2",
            "formula_template": "=RC[-1]",
            "unparsed": False,
            "cached_value": "320000",
            "number_format": '"$"#,##0',
        },
    ]
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=cells,
        layout=_layout(),
        mapping=mapping,
        source_filename="cashflow.xlsx",
        content_sha256="deadbeef",
    )
    metric = doc.blocks[0].metrics[0]
    assert metric.concept_id == "bs.cash"
    assert metric.source.cell_ref == "CF!A2"
    assert metric.values[0].cached_value == "320000"
    assert metric.values[0].source.cell_ref == "CF!B2"
    assert metric.values[1].formula == "=B2"
    assert metric.mapping.method == "rule"
    assert metric.unit == "currency"
    assert doc.unmapped == []
