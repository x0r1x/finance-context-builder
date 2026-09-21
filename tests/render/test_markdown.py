from __future__ import annotations

import json
from pathlib import Path

from finance_context.models.context import (
    ArtifactMeta,
    ContextDocument,
    FinancialBlock,
    MappingEvidence,
    MetricSeries,
    PeriodValue,
    RoleCell,
    SourceRef,
    WorkbookRaw,
)
from finance_context.render.markdown import render_markdown

GOLDEN_MD = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "simple_context.md"
GOLDEN_JSON = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "simple_context.json"


def test_markdown_matches_golden() -> None:
    doc = ContextDocument(
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
                metrics=[
                    MetricSeries(
                        row_key="CF|2|CF!r1",
                        label="Opening cash",
                        concept_id="bs.cash",
                        article_role="database_like",
                        mapping=MappingEvidence(method="rule", confidence="high", score=1.0),
                        source=SourceRef(sheet="CF", addr="A2", row=2, col=1),
                        values=[
                            PeriodValue(
                                period_key="2023",
                                header_text="2023",
                                role="historical",
                                cached_value="100",
                                source=SourceRef(sheet="CF", addr="B2", row=2, col=2),
                            ),
                            PeriodValue(
                                period_key="2024E",
                                header_text="2024E",
                                role="forecast",
                                cached_value="110",
                                has_formula=True,
                                source=SourceRef(sheet="CF", addr="C2", row=2, col=3),
                            ),
                        ],
                    )
                ],
            )
        ],
        warnings=[],
    )
    rendered = render_markdown(doc)
    payload = json.dumps(
        doc.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    assert rendered == GOLDEN_MD.read_text(encoding="utf-8")
    assert payload == GOLDEN_JSON.read_text(encoding="utf-8")
    assert "CF!B2" in rendered
    assert "Opening cash" in rendered


def test_markdown_includes_navigator_and_excluded() -> None:
    from finance_context.models.context import InventoryRow, RowHints

    doc = ContextDocument(
        meta=ArtifactMeta(job_id="job1", status="succeeded", stage="done"),
        workbook=WorkbookRaw(sheets=["CF"], sheet_count=1, cell_count=1),
        blocks=[],
        excluded=[
            MetricSeries(
                row_key="CF|3|CF!r1",
                label="Spare",
                concept_id=None,
                article_role="check",
                kind="helper",
                mapping=MappingEvidence(method="unmapped", confidence="low"),
                source=SourceRef(sheet="CF", addr="A3", row=3, col=1),
                disposition="excluded",
                exclusion_reason="helper",
            )
        ],
        inventory=[
            InventoryRow(
                row_key="CF|2|CF!r1",
                sheet="CF",
                row=2,
                kind="fact",
                label="Opening cash",
                label_path=["Cashflow"],
                concept_id="bs.cash",
                unit="currency",
                formula_fingerprint="=RC[1]",
                hints=RowHints(time_semantics="bop"),
            )
        ],
    )
    rendered = render_markdown(doc)
    assert "## Excluded" in rendered
    assert "Spare" in rendered
    assert "## Row navigator / CF" in rendered
    assert "Opening cash" in rendered
    assert "- Content completeness: 1.00 (1/1 layout rows)" in rendered
    assert "- Concept coverage: 1.00 (1/1 annotatable)" in rendered


def test_markdown_uses_column_not_period_key() -> None:
    doc = ContextDocument(
        meta=ArtifactMeta(job_id="job1", status="succeeded", stage="done"),
        workbook=WorkbookRaw(sheets=["Dash"], sheet_count=1, cell_count=2, formula_count=0),
        blocks=[
            FinancialBlock(
                block_id="Dash!r1",
                sheet="Dash",
                label_col=1,
                grain="week",
                periods=[
                    {
                        "col": 2,
                        "text": "11.01.2026",
                        "role": "forecast",
                        "period_key": "2026-01",
                    },
                    {
                        "col": 3,
                        "text": "18.01.2026",
                        "role": "forecast",
                        "period_key": "2026-01",
                    },
                ],
                metrics=[
                    MetricSeries(
                        row_key="Dash|2|Dash!r1",
                        label="Receipts",
                        concept_id="cf.receipts",
                        article_role="database_like",
                        mapping=MappingEvidence(method="rule", confidence="high"),
                        source=SourceRef(sheet="Dash", addr="A2", row=2, col=1),
                        values=[
                            PeriodValue(
                                period_key="2026-01",
                                header_text="11.01.2026",
                                role="forecast",
                                cached_value="10",
                                source=SourceRef(sheet="Dash", addr="B2", row=2, col=2),
                            ),
                            PeriodValue(
                                period_key="2026-01",
                                header_text="18.01.2026",
                                role="forecast",
                                cached_value="20",
                                source=SourceRef(sheet="Dash", addr="C2", row=2, col=3),
                            ),
                        ],
                    )
                ],
            )
        ],
    )
    rendered = render_markdown(doc)
    assert "11.01.2026" in rendered
    assert "18.01.2026" in rendered
    assert "10 `Dash!B2`" in rendered
    assert "20 `Dash!C2`" in rendered
    assert rendered.count(" high") == 0


def test_markdown_keeps_thirteen_week_columns() -> None:
    periods = [
        {
            "col": i + 2,
            "text": f"w{i}",
            "role": "forecast",
            "period_key": f"2026-01-{i + 1:02d}",
        }
        for i in range(13)
    ]
    doc = ContextDocument(
        meta=ArtifactMeta(job_id="job1", status="succeeded", stage="done"),
        workbook=WorkbookRaw(sheets=["Dash"], sheet_count=1, cell_count=13),
        blocks=[
            FinancialBlock(
                block_id="Dash!r1",
                sheet="Dash",
                label_col=1,
                grain="week",
                periods=periods,
                metrics=[
                    MetricSeries(
                        row_key="Dash|2|Dash!r1",
                        label="Receipts",
                        concept_id="cf.receipts",
                        article_role="database_like",
                        mapping=MappingEvidence(method="rule", confidence="high"),
                        source=SourceRef(sheet="Dash", addr="A2", row=2, col=1),
                        values=[
                            PeriodValue(
                                period_key=item["period_key"],
                                header_text=item["text"],
                                role="forecast",
                                cached_value=str(i),
                                source=SourceRef(
                                    sheet="Dash",
                                    addr=f"col{item['col']}",
                                    row=2,
                                    col=item["col"],
                                ),
                            )
                            for i, item in enumerate(periods)
                        ],
                    )
                ],
            )
        ],
    )
    rendered = render_markdown(doc)
    assert rendered.count("| w") >= 13
    assert "Truncated" not in rendered


def test_markdown_unmapped_period_table() -> None:
    doc = ContextDocument(
        meta=ArtifactMeta(job_id="job1", status="needs_input", stage="done"),
        workbook=WorkbookRaw(sheets=["Dash"], sheet_count=1, cell_count=2),
        blocks=[
            FinancialBlock(
                block_id="Dash!r1",
                sheet="Dash",
                label_col=1,
                grain="week",
                periods=[
                    {"col": 2, "text": "2023", "role": "historical", "period_key": "2023"},
                    {"col": 3, "text": "2024E", "role": "forecast", "period_key": "2024E"},
                ],
                metrics=[
                    MetricSeries(
                        row_key="Dash|2|Dash!r1",
                        label="Receipts",
                        concept_id="cf.receipts",
                        article_role="database_like",
                        mapping=MappingEvidence(method="rule", confidence="high"),
                        source=SourceRef(sheet="Dash", addr="A2", row=2, col=1),
                        values=[
                            PeriodValue(
                                period_key="2023",
                                header_text="2023",
                                role="historical",
                                cached_value="10",
                                source=SourceRef(sheet="Dash", addr="B2", row=2, col=2),
                            ),
                            PeriodValue(
                                period_key="2024E",
                                header_text="2024E",
                                role="forecast",
                                cached_value="20",
                                source=SourceRef(sheet="Dash", addr="C2", row=2, col=3),
                            ),
                        ],
                    )
                ],
            )
        ],
        unmapped=[
            MetricSeries(
                row_key="Dash|3|Dash!r1",
                label="Min Cash Target",
                concept_id=None,
                article_role="database_like",
                mapping=MappingEvidence(method="unmapped", confidence="low"),
                source=SourceRef(sheet="Dash", addr="A3", row=3, col=1),
                values=[
                    PeriodValue(
                        period_key="2023",
                        header_text="2023",
                        role="historical",
                        cached_value="50",
                        source=SourceRef(sheet="Dash", addr="B3", row=3, col=2),
                    ),
                    PeriodValue(
                        period_key="2024E",
                        header_text="2024E",
                        role="forecast",
                        cached_value="55",
                        has_formula=True,
                        source=SourceRef(sheet="Dash", addr="C3", row=3, col=3),
                    ),
                ],
            )
        ],
        warnings=["1 row(s) need mapping review"],
    )
    rendered = render_markdown(doc)
    assert "### Unmapped" in rendered
    assert "Min Cash Target" in rendered
    assert "unknown" in rendered
    assert "50 `Dash!B3`" in rendered
    assert "55* `Dash!C3`" in rendered
    assert "Unmapped rows" not in rendered
    assert rendered.count("## Dash") == 1


def test_markdown_renders_parameters_table() -> None:
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
                metrics=[
                    MetricSeries(
                        row_key="Input Assumptions|8|Input Assumptions!r5",
                        label="Tax Rate",
                        concept_id="pnl.tax_rate",
                        article_role="assumption",
                        unit="rate",
                        mapping=MappingEvidence(method="rule", confidence="high", score=0.9),
                        source=SourceRef(sheet="Input Assumptions", addr="B8", row=8, col=2),
                        cells=[
                            RoleCell(addr="C8", col=3, role="unit", cached_value="%"),
                            RoleCell(addr="D8", col=4, role="value", cached_value="0.3"),
                        ],
                    )
                ],
            )
        ],
    )
    rendered = render_markdown(doc)
    assert "## Parameters / Input Assumptions" in rendered
    assert "Tax Rate" in rendered
    assert "%" in rendered
    assert "pnl.tax_rate" in rendered


def test_markdown_parameters_leads_with_scenario_selector() -> None:
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
                metrics=[
                    MetricSeries(
                        row_key="Input Assumptions|8|Input Assumptions!r5",
                        label="Tax Rate",
                        article_role="assumption",
                        mapping=MappingEvidence(method="rule", confidence="high", score=0.9),
                        source=SourceRef(sheet="Input Assumptions", addr="B8", row=8, col=2),
                        cells=[
                            RoleCell(addr="D8", col=4, role="value", cached_value="0.3"),
                        ],
                    )
                ],
            )
        ],
        excluded=[
            MetricSeries(
                row_key="Input Assumptions|3|Input Assumptions!r5",
                label="Scenario Chosen",
                article_role="assumption",
                mapping=MappingEvidence(method="unmapped", confidence="low"),
                source=SourceRef(sheet="Input Assumptions", addr="B3", row=3, col=2),
                kind="flag",
                disposition="excluded",
                exclusion_reason="flag",
                context_role="scenario_selector",
                cells=[
                    RoleCell(addr="D3", col=4, role="value", cached_value="1"),
                ],
            )
        ],
    )
    rendered = render_markdown(doc)
    params = rendered.split("## Parameters / Input Assumptions", 1)[1]
    assert params.find("Scenario Chosen") < params.find("Tax Rate")
    assert "| Scenario Chosen |" in rendered
    assert "| 1 |" in rendered

