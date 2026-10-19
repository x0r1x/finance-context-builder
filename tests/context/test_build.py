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


def test_hidden_rows_and_columns_warn_once_per_sheet() -> None:
    doc = build_context(
        job_id="abc",
        workbook_meta={
            "sheets": [
                {
                    "name": "Предпосылки",
                    "hidden_rows": [17, 18, 20, 21, 22],
                    "hidden_cols": [],
                },
                {
                    "name": "Вход",
                    "hidden_rows": [],
                    "hidden_cols": [23, 24, 25, 26, 27],
                },
                {"name": "CF", "hidden": True, "hidden_rows": [2]},
            ]
        },
        cells=[
            {"sheet": "Предпосылки", "row": row, "col": 1, "cached_value": "x"}
            for row in (17, 18, 20, 21, 22)
        ]
        + [
            {"sheet": "Вход", "row": 1, "col": col, "cached_value": "x"}
            for col in (23, 24, 25, 26, 27)
        ]
        + [{"sheet": "CF", "row": 2, "col": 1, "cached_value": "Opening"}],
        layout=Layout(sheets=[]),
    )
    assert doc.warnings == [
        "Hidden rows Предпосылки!17:18,20:22 are included",
        "Hidden columns Вход!W:AA are included",
        "Hidden sheet CF is included",
        "Hidden rows CF!2 are included",
    ]


def test_empty_hidden_rows_and_columns_do_not_warn() -> None:
    doc = build_context(
        job_id="abc",
        workbook_meta={
            "sheets": [
                {
                    "name": "Input Assumptions",
                    "hidden_rows": [40, 41],
                    "hidden_cols": [19, *range(20, 16385)],
                }
            ]
        },
        cells=[
            {"sheet": "Input Assumptions", "row": 1, "col": 1, "cached_value": "Year"},
            {"sheet": "Input Assumptions", "row": 2, "col": 19, "cached_value": "  "},
            {
                "sheet": "Input Assumptions",
                "row": 3,
                "col": 20,
                "addr": "T3",
                "cached_value": None,
                "formula_raw": "=B3",
            },
            {"sheet": "Input Assumptions", "row": 40, "col": 2, "cached_value": ""},
            {"sheet": "Input Assumptions", "row": 8, "col": 4, "cached_value": 0},
        ],
        layout=Layout(sheets=[]),
    )
    assert doc.warnings == [
        "Hidden columns Input Assumptions!T are included",
        "1 formula cell(s) missing cached values (e.g. Input Assumptions!T3)",
    ]


def test_hidden_period_and_row_stay_flagged_in_context() -> None:
    from finance_context.layout.detect import detect_layout

    cells = [
        {
            "sheet": "CF",
            "row": 1,
            "col": 1,
            "addr": "A1",
            "cached_value": "Item",
            "hidden": False,
        },
        {
            "sheet": "CF",
            "row": 1,
            "col": 2,
            "addr": "B1",
            "cached_value": "2023",
            "hidden": False,
        },
        {
            "sheet": "CF",
            "row": 1,
            "col": 3,
            "addr": "C1",
            "cached_value": "2024",
            "hidden": True,
        },
        {
            "sheet": "CF",
            "row": 1,
            "col": 4,
            "addr": "D1",
            "cached_value": "2025E",
            "hidden": False,
        },
        {
            "sheet": "CF",
            "row": 2,
            "col": 1,
            "addr": "A2",
            "cached_value": "Revenue",
            "hidden": False,
        },
        {
            "sheet": "CF",
            "row": 2,
            "col": 2,
            "addr": "B2",
            "cached_value": "1",
            "hidden": False,
        },
        {
            "sheet": "CF",
            "row": 2,
            "col": 3,
            "addr": "C2",
            "cached_value": "2",
            "hidden": True,
        },
        {
            "sheet": "CF",
            "row": 2,
            "col": 4,
            "addr": "D2",
            "cached_value": "3",
            "hidden": False,
        },
        {
            "sheet": "CF",
            "row": 3,
            "col": 1,
            "addr": "A3",
            "cached_value": "Продажи продукта 2",
            "hidden": True,
        },
        {
            "sheet": "CF",
            "row": 3,
            "col": 2,
            "addr": "B3",
            "cached_value": "4",
            "hidden": True,
        },
        {
            "sheet": "CF",
            "row": 3,
            "col": 3,
            "addr": "C3",
            "cached_value": "5",
            "hidden": True,
        },
        {
            "sheet": "CF",
            "row": 3,
            "col": 4,
            "addr": "D3",
            "cached_value": "6",
            "hidden": True,
        },
    ]
    layout = detect_layout(cells)
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "CF", "hidden_cols": [3]}]},
        cells=cells,
        layout=layout,
    )
    periods = {period.period_key: period for period in doc.axes[0].periods}
    assert periods["2024"].hidden is True
    assert "hidden" not in periods["2023"].model_dump(mode="json")
    assert periods["2024"].model_dump(mode="json")["hidden"] is True
    rows = {row.label: row for row in doc.blocks[0].rows}
    assert rows["Revenue"].hidden is False
    assert rows["Продажи продукта 2"].hidden is True
    assert rows["Продажи продукта 2"].values == ["4", "5", "6"]
    assert "Hidden columns CF!C are included" in doc.warnings


def test_unrecognized_column_headers_warn_once_per_block() -> None:
    from finance_context.layout.detect import detect_layout

    cells = []
    for col, head in zip("ABCD", ["Показатель", "Отрасль", "Мероприятия", "Значение"], strict=True):
        cells.append(
            {
                "sheet": "Показатели FLM",
                "row": 5,
                "col": ord(col) - ord("A") + 1,
                "addr": f"{col}5",
                "cached_value": head,
                "hidden": False,
            }
        )
    rows = [
        ("Инфляция", "Все", "Мониторинг", "0.05"),
        ("Доходы", "Все", "Мониторинг", "0.04"),
        ("EBITDA", "Все", "Мониторинг", "0.03"),
    ]
    for row, (name, industry, action, amount) in enumerate(rows, start=6):
        for col, value in zip("ABCD", [name, industry, action, amount], strict=True):
            cells.append(
                {
                    "sheet": "Показатели FLM",
                    "row": row,
                    "col": ord(col) - ord("A") + 1,
                    "addr": f"{col}{row}",
                    "cached_value": value,
                    "hidden": False,
                }
            )
    layout = detect_layout(cells)
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "Показатели FLM"}]},
        cells=cells,
        layout=layout,
    )
    assert doc.warnings == [
        "Unrecognized column headers in Показатели FLM!r5: «Отрасль»"
    ]
    roles = {header.text: header.role for header in layout.sheets[0].blocks[0].axis.headers}
    assert roles["Отрасль"] == "unknown"
    assert roles["Мероприятия"] == "note"
    assert roles["Значение"] == "value"


def test_hidden_sheet_is_included_with_a_warning() -> None:
    doc = build_context(
        job_id="abc",
        workbook_meta={"sheets": [{"name": "CF", "hidden": True}]},
        cells=[
            {
                "sheet": "CF",
                "row": 2,
                "col": 2,
                "addr": "B2",
                "cached_value": "1",
            }
        ],
        layout=_layout(),
    )
    assert doc.blocks[0].rows[0].label == "Opening cash"
    assert any(warning == "Hidden sheet CF is included" for warning in doc.warnings)


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
