from __future__ import annotations

from tests.helpers.policy import llm_workers, slot_wait, thresholds
from tests.helpers.ports import GrantSlots
from tests.mapping.conftest import _layout

from finance_context.layout.models import (
    Axis,
    AxisHeader,
    Block,
    Layout,
    LayoutRow,
    RowCell,
    SheetLayout,
)
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.structure import BookView, analyze_structure, build_row_context
from finance_context.mapping.taxonomy import load_taxonomy


def test_unit_column_sets_value_kind() -> None:
    taxonomy = load_taxonomy()
    rows = [
        LayoutRow(
            row=8,
            label="Concession Duration",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
        LayoutRow(
            row=9,
            label="Tax Rate",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
        LayoutRow(
            row=10,
            label="Toll Rate",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
    ]
    block = Block(
        block_id="Input Assumptions!r5",
        label_col=2,
        kind="params",
        axis=Axis(
            id="Input Assumptions!r5",
            row=5,
            headers=[
                AxisHeader(col=4, text="Values", role="value", period_key="value"),
            ],
        ),
        rows=rows,
    )
    layout = Layout(
        sheets=[SheetLayout(name="Input Assumptions", blocks=[block])]
    )
    cells = [
        {
            "sheet": "Input Assumptions",
            "row": 8,
            "col": 3,
            "addr": "C8",
            "cached_value": "years",
        },
        {
            "sheet": "Input Assumptions",
            "row": 9,
            "col": 3,
            "addr": "C9",
            "cached_value": "%",
        },
        {
            "sheet": "Input Assumptions",
            "row": 10,
            "col": 3,
            "addr": "C10",
            "cached_value": "£",
        },
    ]
    book = BookView(layout, cells, taxonomy)
    analyze_structure(book)
    kinds = {
        row.label: build_row_context(book, "Input Assumptions", block, row, None, []).value_kind
        for row in rows
    }
    assert kinds["Concession Duration"] == "count"
    assert kinds["Tax Rate"] == "rate"
    assert kinds["Toll Rate"] == "money"
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={},
        cells=cells,
        embed=None,
        chat=None,
        slots=GrantSlots(),
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    by_label = {row.label: row for row in doc.rows}
    assert by_label["Tax Rate"].concept_id == "pnl.tax_rate"
    assert by_label["Toll Rate"].concept_id == "pnl.price"
    assert by_label["Concession Duration"].concept_id == "ops.concession_duration"
    assert by_label["Concession Duration"].article_role == "assumption"


def test_money_exact_label_does_not_take_a_count_concept() -> None:
    taxonomy = load_taxonomy()
    money = LayoutRow(
        row=4,
        label="Development & Construction",
        kind="fact",
        cells=[RowCell(col=4, role="unit")],
    )
    years = LayoutRow(
        row=5,
        label="Development & Construction",
        kind="fact",
        cells=[RowCell(col=4, role="unit")],
    )
    layout = Layout(
        sheets=[
            SheetLayout(
                name="PF Model",
                blocks=[
                    Block(
                        block_id="PF Model!r1",
                        label_col=1,
                        axis=Axis(
                            id="PF Model!r1",
                            row=1,
                            headers=[
                                AxisHeader(
                                    col=2,
                                    text="2024",
                                    role="historical",
                                    period_key="2024",
                                ),
                            ],
                        ),
                        rows=[money, years],
                    )
                ],
            )
        ]
    )
    cells = [
        {
            "sheet": "PF Model",
            "row": 4,
            "col": 4,
            "addr": "D4",
            "cached_value": "EUR'000",
        },
        {
            "sheet": "PF Model",
            "row": 5,
            "col": 4,
            "addr": "D5",
            "cached_value": "years",
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
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    by_row = {row.row: row.concept_id for row in doc.rows}
    assert by_row[4] != "ops.construction_period"
    assert by_row[5] == "ops.construction_period"


def test_concession_and_operations_duration_are_not_the_same_concept() -> None:
    taxonomy = load_taxonomy()
    rows = [
        LayoutRow(
            row=8,
            label="Concession Duration",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
        LayoutRow(
            row=9,
            label="Construction Duration",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
        LayoutRow(
            row=10,
            label="Operations Duration",
            kind="fact",
            cells=[RowCell(col=3, role="unit"), RowCell(col=4, role="value")],
        ),
    ]
    block = Block(
        block_id="Input Assumptions!r5",
        label_col=2,
        kind="params",
        axis=Axis(
            id="Input Assumptions!r5",
            row=5,
            headers=[AxisHeader(col=4, text="Values", role="value", period_key="value")],
        ),
        rows=rows,
    )
    layout = Layout(sheets=[SheetLayout(name="Input Assumptions", blocks=[block])])
    cells = [
        {
            "sheet": "Input Assumptions",
            "row": row,
            "col": 3,
            "addr": f"C{row}",
            "cached_value": "years",
        }
        for row in (8, 9, 10)
    ]
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={
            ("concession duration", ""): "ops.lifetime",
            ("operations duration", ""): "ops.lifetime",
        },
        cells=cells,
        embed=None,
        chat=None,
        slots=GrantSlots(),
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Concession Duration"] == "ops.concession_duration"
    assert by_label["Operations Duration"] == "ops.operating_period"
    assert by_label["Construction Duration"] == "ops.construction_period"
    assert by_label["Concession Duration"] != by_label["Operations Duration"]


def test_inflation_fees_and_cfs_opex_split() -> None:
    taxonomy = load_taxonomy()
    layout = Layout(
        sheets=[
            SheetLayout(
                name="Input Assumptions",
                blocks=[
                    Block(
                        block_id="IA!r1",
                        label_col=1,
                        axis=Axis(
                            id="IA!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="1", role="relative", period_key="Y1"),
                            ],
                        ),
                        rows=[
                            LayoutRow(row=2, label="Inflation per year", kind="fact"),
                            LayoutRow(
                                row=3,
                                label="Inflation per year (costs) from beginning of concession",
                                kind="fact",
                            ),
                            LayoutRow(row=4, label="Engagement fee", kind="fact"),
                        ],
                    )
                ],
            ),
            SheetLayout(
                name="CFS",
                blocks=[
                    Block(
                        block_id="CFS!r1",
                        label_col=1,
                        axis=Axis(
                            id="CFS!r1",
                            row=1,
                            headers=[
                                AxisHeader(col=2, text="1", role="relative", period_key="Y1"),
                            ],
                        ),
                        rows=[
                            LayoutRow(
                                row=5,
                                label="OPEX",
                                kind="fact",
                                section_path=["Cashflow Statement"],
                            ),
                            LayoutRow(
                                row=6,
                                label="Interest",
                                kind="fact",
                                section_path=["Cashflow Statement"],
                            ),
                        ],
                    )
                ],
            ),
        ]
    )
    cells = [
        {
            "sheet": "Input Assumptions",
            "row": 4,
            "col": 2,
            "addr": "B4",
            "cached_value": "0.01",
            "number_format": "0%",
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
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    by_key = {(row.sheet, row.label): row.concept_id for row in doc.rows}
    assert by_key[("Input Assumptions", "Inflation per year")] == "ops.inflation_revenue"
    assert (
        by_key[
            (
                "Input Assumptions",
                "Inflation per year (costs) from beginning of concession",
            )
        ]
        == "ops.inflation_cost"
    )
    assert by_key[("Input Assumptions", "Engagement fee")] == "debt.engagement_fee_rate"
    assert by_key[("CFS", "OPEX")] == "cf.opex_paid"
    assert by_key[("CFS", "Interest")] == "cf.interest_paid"


def test_dscr_minimum_is_limit_not_observed_dscr() -> None:
    taxonomy = load_taxonomy()
    layout = _layout(
        LayoutRow(row=2, label="DSCR minimum"),
        LayoutRow(row=3, label="Average Debt Service Coverage Ratio (DSCR)"),
        LayoutRow(row=4, label="Minimum Debt Service Coverage Ratio (DSCR)"),
        sheet="Funding assumptions",
    )
    doc = map_layout(
        layout,
        taxonomy=taxonomy,
        glossary={},
        embed=None,
        chat=None,
        slots=GrantSlots(),
        thresholds=thresholds(),
        slot_timeout_sec=slot_wait(),
        llm_concurrency=llm_workers(),
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["DSCR minimum"] == "cov.dscr_limit"
    assert by_label["Average Debt Service Coverage Ratio (DSCR)"] == "cov.dscr"
    assert by_label["Minimum Debt Service Coverage Ratio (DSCR)"] == "cov.dscr"
