from __future__ import annotations

import json
from pathlib import Path

from finance_context.graph.models import (
    FormulaLink,
    GraphDocument,
    TraceDocument,
    TraceEdge,
    TraceNode,
)
from finance_context.models.context import (
    ArtifactMeta,
    BlockRow,
    ContextAxis,
    ContextDocument,
    ContextPeriod,
    FinancialBlock,
    MappingEvidence,
    MappingStats,
    RoleCell,
    RowHints,
    WorkbookRaw,
)
from finance_context.render.graph import render_graph_markdown, render_trace_markdown
from finance_context.render.markdown import render_markdown

GOLDEN_MD = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "simple_context.md"
GOLDEN_JSON = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "simple_context.json"


def _doc() -> ContextDocument:
    return ContextDocument(
        meta=ArtifactMeta(
            job_id="job1",
            status="succeeded",
            stage="done",
            source_filename="simple.xlsx",
        ),
        workbook=WorkbookRaw(sheets=["CF"], sheet_count=1, cell_count=4, formula_count=1),
        blocks=[
            FinancialBlock(
                block_id="CF!r1",
                sheet="CF",
                label_col=1,
                grain="year",
                periods=[
                    {"col": 2, "text": "2023", "role": "historical", "period_key": "2023"},
                    {"col": 3, "text": "2024E", "role": "forecast", "period_key": "2024E"},
                ],
                rows=[
                    BlockRow(
                        row_key="CF|2|CF!r1",
                        sheet="CF",
                        row=2,
                        kind="fact",
                        label="Opening cash",
                        concept_id="bs.cash",
                        disposition="mapped",
                        formula="=RC[-1]",
                        unit="money",
                        mapping=MappingEvidence(method="rule", confidence="high", score=1.0),
                        hints=RowHints(
                            unit="money",
                            currency="GBP",
                            scale="k",
                            time_semantics="bop",
                            sign="stock",
                        ),
                        period_position="beginning",
                        aggregation="first",
                        scale_factor=1000,
                        values=["100", "110"],
                        value_statuses=["cached", "cached"],
                        normalized_values=["100000", "110000"],
                    )
                ],
            )
        ],
        mapping_stats=MappingStats(
            inventory_rows=1,
            mapped=1,
            content_completeness=1.0,
            concept_coverage=1.0,
        ),
        warnings=[],
    )


def test_period_cell_prints_the_graph_address_under_the_value() -> None:
    doc = ContextDocument(
        meta=ArtifactMeta(job_id="job1", status="succeeded", stage="done"),
        workbook=WorkbookRaw(sheets=["PF Model"], sheet_count=1, cell_count=2),
        blocks=[
            FinancialBlock(
                block_id="PF Model!r7",
                sheet="PF Model",
                label_col=1,
                periods=[
                    {"col": 27, "text": "31.12.2038", "role": "forecast", "period_key": "2038"},
                ],
                rows=[
                    BlockRow(
                        row_key="PF Model|172|PF Model!r7",
                        sheet="PF Model",
                        row=172,
                        kind="fact",
                        label="CPI",
                        disposition="mapped",
                        concept_id="ops.cpi",
                        values=["1.3458683383241299"],
                        value_statuses=["cached"],
                        normalized_values=["1.3458683383"],
                    ),
                    BlockRow(
                        row_key="PF Model|537|PF Model!r7",
                        sheet="PF Model",
                        row=537,
                        kind="fact",
                        label="Missing",
                        disposition="mapped",
                        values=[None],
                        value_statuses=["empty"],
                    ),
                ],
            )
        ],
    )
    rendered = render_markdown(doc)
    assert "1.3458683383241299<br>PF Model!AA172" in rendered
    assert "empty<br>PF Model!AA537" in rendered
    assert "PF Model!AA172" not in doc.model_dump_json()


def test_markdown_matches_golden() -> None:
    doc = _doc()
    rendered = render_markdown(doc)
    payload = json.dumps(
        doc.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    assert rendered == GOLDEN_MD.read_text(encoding="utf-8")
    assert payload == GOLDEN_JSON.read_text(encoding="utf-8")
    assert "Opening cash" in rendered
    assert "100" in rendered
    assert "110" in rendered
    assert "=RC[-1]" in rendered
    assert "bs.cash" in rendered


def test_markdown_keeps_every_period_and_excluded_row() -> None:
    periods = [
        {"col": index, "text": f"Y{index}", "role": "relative", "period_key": f"Y{index}"}
        for index in range(1, 21)
    ]
    doc = ContextDocument(
        meta=ArtifactMeta(job_id="job1", status="succeeded", stage="done"),
        workbook=WorkbookRaw(sheets=["CF"], sheet_count=1, cell_count=40),
        blocks=[
            FinancialBlock(
                block_id="CF!r1",
                sheet="CF",
                label_col=1,
                periods=periods,
                rows=[
                    BlockRow(
                        row_key="CF|2|CF!r1",
                        sheet="CF",
                        row=2,
                        kind="fact",
                        label="Opening cash",
                        concept_id="bs.cash",
                        disposition="mapped",
                        values=[str(index) for index in range(1, 21)],
                    ),
                    BlockRow(
                        row_key="CF|3|CF!r1",
                        sheet="CF",
                        row=3,
                        kind="helper",
                        label="Spare",
                        disposition="excluded",
                        exclusion_reason="helper",
                        values=["0"] * 20,
                    ),
                ],
            )
        ],
        mapping_stats=MappingStats(inventory_rows=2, mapped=1, excluded=1),
    )
    rendered = render_markdown(doc)
    assert "Y20" in rendered
    assert "Spare" in rendered
    assert "excluded" in rendered
    assert "Truncated" not in rendered


def test_markdown_parameters_include_selector_and_value() -> None:
    doc = ContextDocument(
        meta=ArtifactMeta(job_id="job1", status="succeeded", stage="done"),
        workbook=WorkbookRaw(sheets=["Input Assumptions"], sheet_count=1, cell_count=4),
        blocks=[
            FinancialBlock(
                block_id="Input Assumptions!r5",
                sheet="Input Assumptions",
                label_col=2,
                kind="params",
                periods=[
                    {"col": 4, "text": "Values", "role": "value", "period_key": "value"},
                ],
                rows=[
                    BlockRow(
                        row_key="Input Assumptions|3|Input Assumptions!r5",
                        sheet="Input Assumptions",
                        row=3,
                        kind="flag",
                        label="Scenario Chosen",
                        disposition="excluded",
                        context_role="scenario_selector",
                        cells=[RoleCell(addr="D3", col=4, role="value", cached_value="1")],
                        values=["1"],
                    ),
                    BlockRow(
                        row_key="Input Assumptions|8|Input Assumptions!r5",
                        sheet="Input Assumptions",
                        row=8,
                        kind="fact",
                        label="Tax Rate",
                        concept_id="tax.rate",
                        disposition="mapped",
                        hints=RowHints(unit="rate"),
                        cells=[RoleCell(addr="D8", col=4, role="value", cached_value="0.3")],
                        values=["0.3"],
                    ),
                ],
            )
        ],
    )
    rendered = render_markdown(doc)
    assert "## Parameters / Input Assumptions" in rendered
    assert "Scenario Chosen" in rendered
    assert "Tax Rate" in rendered
    assert "0.3" in rendered


def test_axes_print_periods_as_columns() -> None:
    doc = ContextDocument(
        meta=ArtifactMeta(job_id="job1", status="succeeded", stage="done"),
        workbook=WorkbookRaw(sheets=["PF Model", "TBA"], sheet_count=2),
        axes=[
            ContextAxis(
                id="PF Model!r9",
                sheet="PF Model",
                grain="year",
                header_row=9,
                periods=[
                    ContextPeriod(col=col, text=key, period_key=key, calendar_year=key)
                    for col, key in ((14, "2021"), (15, "2022"), (25, "2032"))
                ],
            ),
            ContextAxis(
                id="TBA!r1",
                sheet="TBA",
                grain="year",
                header_row=1,
                periods=[
                    ContextPeriod(
                        col=2,
                        period_key="Y1",
                        phase="construction",
                        phase_year=1,
                        flags={"construction": True},
                    ),
                    ContextPeriod(
                        col=3,
                        period_key="Y2",
                        phase="operation",
                        phase_year=1,
                        flags={"operation": True},
                    ),
                ],
            ),
        ],
    )
    rendered = render_markdown(doc)
    assert "| PF Model!r9 | 2021 | 2022 | 2032 |\n| --- | --- | --- | --- |\n" in rendered
    assert "| Period |" not in rendered
    assert "| TBA!r1 | Y1 | Y2 |" in rendered
    assert "| Phase | construction | operation |" in rendered
    assert "| Phase year | 1 | 1 |" in rendered
    assert "| Flags | construction | operation |" in rendered


def test_graph_and_trace_markdown_repeat_json_facts() -> None:
    graph = GraphDocument(
        job_id="job1",
        nodes=4,
        edges=9,
        iterate=False,
        links=[
            FormulaLink(
                cell="P&L!C13",
                formula="=SUM(C9:C12)",
                formula_class="aggregation",
                refs=["P&L!C9:C12"],
                row_key="P&L|13|P&L!r1",
                period_id="2024",
            ),
        ],
    )
    rendered = render_graph_markdown(graph)
    assert "4" in rendered
    assert "9" in rendered
    assert "P&L!C13" in rendered
    assert "=SUM(C9:C12)" in rendered
    assert "P&L!C9:C12" in rendered
    assert "P&L\\|13\\|P&L!r1" in rendered
    assert "2024" in rendered
    assert "aggregation" in rendered
    trace = TraceDocument(
        origin="P&L!C13",
        direction="precedents",
        depth=6,
        stopped="depth",
        nodes=[
            TraceNode(node_id="P&L!C13", formula="=SUM(C9:C12)", cached_value="23", depth=0),
        ],
        edges=[TraceEdge(source="P&L!C13", target="P&L!C9:C12", kind="range")],
    )
    trace_md = render_trace_markdown(trace)
    assert "P&L!C13" in trace_md
    assert "P&L!C9:C12" in trace_md
    assert "formula_ast" not in trace_md
