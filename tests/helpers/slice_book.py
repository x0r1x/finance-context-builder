"""Small context and graph used by catalog, observation, and summary tests."""

from __future__ import annotations

import json
from pathlib import Path

from finance_context.graph.models import (
    CircularityHint,
    CycleRecord,
    FormulaLink,
    GraphDocument,
    IdCount,
)
from finance_context.models.context import (
    ArtifactMeta,
    BlockRow,
    ContextAxis,
    ContextDocument,
    ContextPeriod,
    FinancialBlock,
    MappingStats,
    RoleCell,
    RowHints,
    RowSeries,
    WorkbookRaw,
)

REVENUE = "Operation|10|Operation!r8"
OTHER = "Operation|11|Operation!r8"
TOTAL = "Operation|12|Operation!r8"
BLANK = "Operation|13|Operation!r8"
ZERO = "Operation|14|Operation!r8"
MYSTERY = "Operation|15|Operation!r8"
DISCOUNT = "Inputs|20|Inputs!cases"
AXIS = "Operation!r8"


def sample_links() -> list[FormulaLink]:
    return [
        FormulaLink(
            cell="Operation!H10",
            formula="=RC[-1]*(1+Growth)",
            formula_class="cross_period",
            row_key=REVENUE,
            period_id="Y5",
        ),
        FormulaLink(
            cell="Operation!H12",
            formula="=SUM(H10:H11)",
            formula_class="aggregation",
            row_key=TOTAL,
            period_id="Y5",
        ),
    ]


def sample_context(*, job_id: str = "job") -> ContextDocument:
    axis = ContextAxis(
        id=AXIS,
        sheet="Operation",
        grain="year",
        header_row=8,
        periods=[
            ContextPeriod(
                col=8,
                text="Y5",
                role="forecast",
                period_key="Y5",
                index=5,
                phase="operation",
                phase_year=1,
                start_date="2026-01-01",
                end_date="2026-12-31",
            )
        ],
    )
    timeline = FinancialBlock(
        block_id="Operation!block",
        sheet="Operation",
        label_col=1,
        kind="timeline",
        axis_ids=[AXIS],
        rows=[
            _timeline(
                row_key=REVENUE,
                row=10,
                label="Revenue",
                concept_id="pnl.revenue",
                label_path=["Operation", "Revenue"],
                hints=RowHints(
                    unit="money",
                    currency="GBP",
                    scale="k",
                    sign="inflow",
                    segment="pc",
                ),
                formula="=RC[-1]*(1+Growth)",
                values=["1234.56"],
                statuses=["cached"],
                normalized=["1234560"],
                scale_factor=1000,
            ),
            _timeline(
                row_key=OTHER,
                row=11,
                label="Other income",
                concept_id="pnl.other",
                label_path=["Operation", "Other income"],
                hints=RowHints(unit="money", currency="GBP", scale="k", sign="inflow"),
                values=["765.44"],
                statuses=["cached"],
                normalized=["765440"],
                scale_factor=1000,
            ),
            _timeline(
                row_key=TOTAL,
                row=12,
                label="Total",
                concept_id="pnl.total",
                label_path=["Operation", "Total"],
                hints=RowHints(unit="money", currency="GBP", scale="k", sign="inflow"),
                formula="=SUM(H10:H11)",
                values=["2000"],
                statuses=["cached"],
                normalized=["2000000"],
                scale_factor=1000,
            ),
            _timeline(
                row_key=BLANK,
                row=13,
                label="Blank",
                concept_id="pnl.blank",
                label_path=["Operation", "Blank"],
                hints=RowHints(unit="money"),
                values=[None],
                statuses=["empty"],
                normalized=[None],
            ),
            _timeline(
                row_key=ZERO,
                row=14,
                label="Zero",
                concept_id="pnl.zero",
                label_path=["Operation", "Zero"],
                hints=RowHints(unit="money"),
                values=["0"],
                statuses=["zero_explicit"],
                normalized=["0"],
            ),
            _timeline(
                row_key=MYSTERY,
                row=15,
                label="Mystery line",
                concept_id=None,
                disposition="abstained",
                label_path=["Operation", "Mystery line"],
                hints=RowHints(unit="money"),
                values=["1"],
                statuses=["cached"],
                normalized=["1"],
            ),
        ],
    )
    params = FinancialBlock(
        block_id="Inputs!cases",
        sheet="Inputs",
        label_col=1,
        kind="params",
        axis_ids=["Inputs!cases"],
        periods=[{"col": 2, "text": "Live", "role": "value", "period_key": "live"}],
        rows=[
            BlockRow(
                row_key=DISCOUNT,
                sheet="Inputs",
                row=20,
                label="Discount rate",
                label_path=["Inputs", "Discount rate"],
                kind="fact",
                disposition="mapped",
                concept_id="fin.discount_rate",
                period_position="instant",
                aggregation="none",
                hints=RowHints(unit="rate"),
                cells=[
                    RoleCell(
                        addr="B20",
                        col=2,
                        role="value",
                        cached_value="0.05",
                        header="Live",
                    )
                ],
                series=[
                    RowSeries(
                        axis_id="Inputs!cases",
                        values=["0.05"],
                        value_statuses=["cached"],
                        normalized_values=["0.05"],
                    )
                ],
                values=["0.05"],
                value_statuses=["cached"],
                normalized_values=["0.05"],
            )
        ],
    )
    return ContextDocument(
        meta=ArtifactMeta(
            job_id=job_id,
            status="succeeded",
            stage="done",
            source_filename="rvi-project-finance.xlsx",
            content_sha256="f" * 64,
        ),
        workbook=WorkbookRaw(
            sheets=["Inputs", "Operation"],
            formula_count=20,
            missing_cached_values=17,
            unparsed_formulas=1,
            defined_names=[{"name": "Growth"}],
        ),
        axes=[axis],
        blocks=[timeline, params],
        mapping_stats=MappingStats(
            inventory_rows=7,
            mapped=5,
            abstained=1,
            excluded=1,
            concept_coverage=0.8,
        ),
        warnings=["cache missing inside the phase"],
    )


def sample_graph(job_id: str, links: list[FormulaLink] | None = None) -> GraphDocument:
    return GraphDocument(
        job_id=job_id,
        nodes=10,
        edges=4,
        kinds={"formula": 2},
        unresolved=IdCount(count=2, ids=["ExtBook"]),
        external=IdCount(count=1, ids=["[1]Macro"]),
        dangling=IdCount(count=0, ids=[]),
        cycles=[
            CycleRecord(
                id="cycle-1",
                members=["Operation!H12", "Operation!H10", "Operation!H11", "Operation!H9"],
                class_="unexpected",
            )
        ],
        circularity_hints=[
            CircularityHint(
                sheet="Operation",
                row=12,
                label="Total",
                cell_ids=["Operation!H12"],
            )
        ],
        links=list(links if links is not None else sample_links()),
    )


def write_slice_job(data_dir: Path, job_id: str) -> Path:
    dest = data_dir / "sessions" / "local" / "jobs" / job_id
    dest.mkdir(parents=True)
    context = sample_context(job_id=job_id)
    graph = sample_graph(job_id)
    dest.joinpath("context.json").write_text(context.model_dump_json(), encoding="utf-8")
    dest.joinpath("graph.json").write_text(
        graph.model_dump_json(by_alias=True),
        encoding="utf-8",
    )
    dest.joinpath("meta.json").write_text(
        json.dumps(context.meta.model_dump(mode="json")),
        encoding="utf-8",
    )
    return dest


def _timeline(
    *,
    row_key: str,
    row: int,
    label: str,
    concept_id: str | None,
    label_path: list[str],
    hints: RowHints,
    values: list[str | None],
    statuses: list[str],
    normalized: list[str | None],
    formula: str | None = None,
    disposition: str = "mapped",
    scale_factor: int | None = None,
) -> BlockRow:
    return BlockRow(
        row_key=row_key,
        sheet="Operation",
        row=row,
        label=label,
        label_path=label_path,
        kind="fact",
        disposition=disposition,
        concept_id=concept_id,
        period_position="during_period",
        aggregation="sum",
        scale_factor=scale_factor,
        hints=hints,
        formula=formula,
        values=values,
        value_statuses=statuses,  # type: ignore[arg-type]
        normalized_values=normalized,
        series=[
            RowSeries(
                axis_id=AXIS,
                formula=formula,
                values=values,
                value_statuses=statuses,  # type: ignore[arg-type]
                normalized_values=normalized,
            )
        ],
    )
