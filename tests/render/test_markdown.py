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
                                formula="=B2+10",
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
