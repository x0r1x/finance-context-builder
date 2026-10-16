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
        source_filename="cashflow.xlsx",
        content_sha256="deadbeef",
    )
    metric = doc.blocks[0].rows[0]
    assert metric.sheet == "CF"
    assert metric.row == 2
    assert metric.values == ["320000", "320000"]
    assert metric.value_statuses == ["cached", "cached"]
    assert metric.scale_factor is None
    assert metric.normalized_values == ["320000", "320000"]
    assert metric.period_position == "beginning"
    assert metric.aggregation == "first"
    assert metric.formula == "=RC[-1]"
    assert metric.unit == "money"
    assert metric.hints.currency == "USD"
    assert metric.hints.scale is None
    assert metric.hints.sign == "stock"


def test_build_warns_on_missing_graph_targets() -> None:
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
        graph=GraphPointer(dangling=12, empty_range_members=718),
    )
    assert "Graph: 12 unresolved formula targets (see graph.json)" in doc.warnings
    assert all("empty range" not in warning for warning in doc.warnings)


def test_build_decodes_excel_date_serials() -> None:
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
        edges=[{"source": "CF!B2", "target": "CF!B3", "kind": "ref"}],
    )
    rows = doc.blocks[0].rows
    assert [row.kind for row in rows] == ["abstract", "fact", "helper"]
    assert len(rows) == 3
    cash = rows[1]
    assert cash.kind == "fact"
    assert cash.disposition is None
    assert cash.label_path == ["Cashflow"]
    assert cash.formula == "=RC[1]"
    assert cash.neighbors == ["CF!3"]
    assert cash.hints.time_semantics in {"bop", "flow", "eop", "stock"}
    assert rows[2].disposition == "excluded"
    assert rows[2].exclusion_reason == "helper"
    assert rows[2].neighbors == ["CF!2"]
    assert not any("Content completeness" in warning for warning in doc.warnings)
    abstract = rows[0]
    assert abstract.disposition is None
    assert abstract.neighbors == []
    assert doc.schema_version == "1.14.0"


def test_neighbors_include_every_formula_row_ref() -> None:
    edges = [
        {"source": "CF!B2", "target": f"CF!B{row}", "kind": "ref"}
        for row in range(3, 15)
    ]
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=[
            {
                "sheet": "CF",
                "row": 2,
                "col": 2,
                "addr": "B2",
                "formula_raw": "=SUM(B3:B14)",
                "formula_template": "=SUM(R[1]C:R[12]C)",
                "cached_value": "10",
            }
        ],
        layout=_layout(),
        edges=edges,
    )
    assert doc.blocks[0].rows[0].neighbors == [f"CF!{row}" for row in range(3, 15)]


def test_fact_time_comes_from_the_label_not_a_concept() -> None:
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
                        rows=[LayoutRow(row=2, label="Site insurance", kind="fact")],
                    )
                ],
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
            "cached_value": "10",
        }
    ]
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "CF"}]},
        cells=cells,
        layout=layout,
    )
    row = doc.blocks[0].rows[0]
    assert row.hints.time_semantics == "flow"
    assert row.hints.sign is None


def test_zero_cached_formula_is_not_missing() -> None:
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
    )
    series = doc.blocks[0].rows[0]
    assert any(cell.role == "value" and cell.addr == "C26" for cell in series.cells)
    assert series.values == ["50"]


def test_axis_inputs_do_not_inherit_the_stub_check_formula() -> None:
    layout = Layout(
        sheets=[
            SheetLayout(
                name="PF",
                blocks=[
                    Block(
                        block_id="PF!r7",
                        label_col=4,
                        axis=Axis(
                            id="PF!r7",
                            row=7,
                            headers=[
                                AxisHeader(
                                    col=13, text="2024", role="forecast", period_key="2024"
                                ),
                                AxisHeader(
                                    col=14, text="2025", role="forecast", period_key="2025"
                                ),
                            ],
                        ),
                        rows=[
                            LayoutRow(
                                row=179,
                                label="Full-wrap EPC",
                                cells=[RowCell(col=7, role="value", header="Check")],
                            )
                        ],
                    )
                ],
            )
        ]
    )
    cells = [
        {
            "sheet": "PF",
            "row": 179,
            "col": 13,
            "addr": "M179",
            "cached_value": "0.2",
        },
        {
            "sheet": "PF",
            "row": 179,
            "col": 14,
            "addr": "N179",
            "cached_value": "0.8",
        },
        {
            "sheet": "PF",
            "row": 179,
            "col": 7,
            "addr": "G179",
            "cached_value": "0",
            "formula_raw": "=IF(AND(H179>0,L179<>1),1,0)",
            "formula_template": "=IF(AND(R[0]C[1]>0,R[0]C[5]<>1),1,0)",
        },
    ]
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "PF"}]},
        cells=cells,
        layout=layout,
    )
    row = doc.blocks[0].rows[0]
    assert row.formula is None
    assert row.series[0].formula is None
    assert row.values == ["0.2", "0.8"]
    check = next(cell for cell in row.cells if cell.addr == "G179")
    assert check.cached_value == "0"
    assert check.role == "value"
