from __future__ import annotations

from finance_context.context.build import build_context
from finance_context.layout.models import (
    Axis,
    AxisHeader,
    Block,
    Layout,
    LayoutRow,
    RowCell,
    SheetLayout,
)
from finance_context.mapping.models import MappedRow, MappingDocument
from finance_context.models.context import GraphPointer


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
    metric = doc.blocks[0].rows[0]
    assert metric.concept_id == "bs.cash"
    assert metric.sheet == "CF"
    assert metric.row == 2
    assert metric.values == ["320000", "320000"]
    assert metric.value_statuses == ["cached", "cached"]
    assert metric.scale_factor == 1
    assert metric.normalized_values == ["320000", "320000"]
    assert metric.period_position == "beginning"
    assert metric.aggregation == "first"
    assert metric.formula == "=RC[-1]"
    assert metric.mapping is not None
    assert metric.mapping.method == "rule"
    assert metric.unit == "money"
    assert metric.hints.currency == "USD"
    assert metric.hints.scale == "unit"
    assert metric.hints.sign == "stock"
    assert doc.mapping_stats.unmapped_series == 0


def test_build_warns_on_missing_graph_targets() -> None:
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
            "cached_value": "1",
        }
    ]
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=cells,
        layout=_layout(),
        mapping=mapping,
        graph=GraphPointer(dangling=12, empty_range_members=718),
    )
    assert "Graph: 12 unresolved formula targets (see graph.json)" in doc.warnings
    assert all("empty range" not in warning for warning in doc.warnings)


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
    assert doc.blocks[0].rows[0].values[0] == "31.12.2023"


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
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=cells,
        layout=layout,
        mapping=mapping,
    )
    rows = doc.blocks[0].rows
    assert [row.kind for row in rows] == ["abstract", "fact", "helper"]
    assert len(rows) == 3
    cash = rows[1]
    assert cash.kind == "fact"
    assert cash.disposition == "mapped"
    assert cash.label_path == ["Cashflow"]
    assert cash.formula == "=RC[1]"
    assert cash.candidates[0].concept_id == "bs.cash"
    assert cash.hints.time_semantics in {"bop", "flow", "eop", "stock"}
    assert rows[2].disposition == "excluded"
    assert not any("Content completeness" in warning for warning in doc.warnings)
    abstract = rows[0]
    assert abstract.concept_id is None
    assert abstract.disposition == "header"
    assert abstract.neighbors == ["Opening cash", "Spare"]
    assert doc.mapping_stats.inventory_rows == 3
    assert doc.mapping_stats.mapped == 1
    assert doc.mapping_stats.excluded == 1
    assert doc.mapping_stats.abstract == 1
    assert doc.mapping_stats.unmapped_series == 0
    assert doc.mapping_stats.concept_coverage == 1.0
    assert doc.schema_version == "1.11.0"
    quality = doc.mapping_stats.mapping_quality
    assert quality.label_coverage == 1.0
    assert quality.semantic_coverage == 1.0
    assert quality.confidence_threshold_passed is True


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


def test_build_exports_role_cells_not_graph_samples() -> None:
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
                                AxisHeader(
                                    col=5, text="2023", role="historical", period_key="2023"
                                )
                            ],
                        ),
                        rows=[
                            LayoutRow(
                                row=26,
                                label="Equity (k£)",
                                kind="fact",
                                cells=[RowCell(col=3, role="value")],
                            )
                        ],
                    )
                ],
            )
        ]
    )
    mapping = MappingDocument(
        rows=[
            MappedRow(
                row_key="Construction|26|Construction!r1",
                sheet="Construction",
                row=26,
                block_id="Construction!r1",
                label="Equity (k£)",
                concept_id="cf.equity_issue",
                article_role="assumption",
                source="rule",
                confidence="high",
                score=0.94,
                alternatives=[("cf.equity_issue", 0.94), ("bs.equity", 0.5), ("cf.fcf", 0.4)],
            )
        ]
    )
    cells = [
        {
            "sheet": "Construction",
            "row": 26,
            "col": 3,
            "addr": "C26",
            "cached_value": "115",
            "formula_raw": "=$C$20*0.35",
        },
        {
            "sheet": "Construction",
            "row": 26,
            "col": 5,
            "addr": "E26",
            "cached_value": "50",
            "formula_raw": "=C26",
        },
        {
            "sheet": "Debt",
            "row": 3,
            "col": 6,
            "addr": "F3",
            "cached_value": "0.065",
        },
    ]
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "Construction"}, {"name": "Debt"}]},
        cells=cells,
        layout=layout,
        mapping=mapping,
    )
    series = doc.blocks[0].rows[0]
    assert any(cell.role == "value" and cell.addr == "C26" for cell in series.cells)
    assert len(series.candidates) == 3
    assert series.values == ["50"]
