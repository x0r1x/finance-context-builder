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


def test_build_decodes_excel_date_serials() -> None:
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
            "cached_value": "45291",
            "number_format": "dd/mm/yyyy",
        },
        {
            "sheet": "CF",
            "row": 2,
            "col": 3,
            "addr": "C2",
            "formula_raw": None,
            "cached_value": "45291",
            "number_format": "dd/mm/yyyy",
        },
    ]
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=cells,
        layout=_layout(),
        mapping=mapping,
    )
    assert doc.blocks[0].metrics[0].values[0].cached_value == "31.12.2023"


def test_build_keeps_inventory_for_every_layout_row() -> None:
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
                                    col=2, text="2023", role="historical", period_key="2023"
                                )
                            ],
                        ),
                        rows=[
                            LayoutRow(row=1, label="Cashflow", kind="abstract"),
                            LayoutRow(
                                row=2,
                                label="Opening cash",
                                kind="fact",
                                section_path=["Cashflow"],
                            ),
                            LayoutRow(row=3, label="Spare", kind="helper"),
                        ],
                    )
                ],
            )
        ]
    )
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
                alternatives=[("bs.cash", 1.0), ("liq.cash", 0.4)],
            ),
            MappedRow(
                row_key="CF|3|CF!r1",
                sheet="CF",
                row=3,
                block_id="CF!r1",
                label="Spare",
                concept_id=None,
                article_role="check",
                source="rule",
                disposition="excluded",
                exclusion_reason="helper",
            ),
        ]
    )
    cells = [
        {
            "sheet": "CF",
            "row": 2,
            "col": 2,
            "addr": "B2",
            "formula_raw": "=C2",
            "formula_template": "=RC[1]",
            "cached_value": "10",
        }
    ]
    edges = [{"source": "CF!B2", "target": "CF!C3", "kind": "ref"}]
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=cells,
        layout=layout,
        mapping=mapping,
        edges=edges,
    )
    assert [row.kind for row in doc.inventory] == ["abstract", "fact", "helper"]
    assert len(doc.inventory) == 3
    assert len(doc.blocks[0].metrics) == 1
    assert len(doc.excluded) == 1
    cash = doc.blocks[0].metrics[0]
    assert cash.kind == "fact"
    assert cash.label_path == ["Cashflow"]
    assert cash.formula_fingerprint == "=RC[1]"
    assert cash.candidates[0].concept_id == "bs.cash"
    assert cash.hints.time_semantics in {"bop", "flow", "eop"}
    assert "CF!3" in cash.precedents_rows
    assert not any("Content completeness" in warning for warning in doc.warnings)
    abstract = doc.inventory[0]
    assert abstract.concept_id is None
    assert abstract.neighbors == ["Opening cash", "Spare"]


def test_zero_cached_formula_is_not_missing() -> None:
    mapping = MappingDocument(rows=[])
    cells = [
        {
            "sheet": "CF",
            "row": 2,
            "col": 2,
            "addr": "B2",
            "formula_raw": "=C2-C2",
            "cached_value": "0",
        },
        {
            "sheet": "CF",
            "row": 2,
            "col": 3,
            "addr": "C2",
            "formula_raw": "=1/0",
            "cached_value": None,
        },
        {
            "sheet": "CF",
            "row": 2,
            "col": 4,
            "addr": "D2",
            "formula_raw": "=2/0",
            "cached_value": None,
        },
    ]
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=cells,
        layout=_layout(),
        mapping=mapping,
    )
    assert doc.workbook.missing_cached_values == 2
    assert not any(w.startswith("Formula without cached value at") for w in doc.warnings)
    summary = [w for w in doc.warnings if "missing cached values" in w]
    assert len(summary) == 1
    assert summary[0].startswith("2 formula cell(s) missing cached values")
    assert "CF!C2" in summary[0]
