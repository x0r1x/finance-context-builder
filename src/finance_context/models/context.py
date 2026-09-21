from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.6.0"

TimelinePhase = Literal["construction", "operation"]

Confidence = Literal["high", "medium", "low"]
MapMethod = Literal["rule", "embed", "llm", "unmapped", "structure"]
JobStatus = Literal["queued", "running", "succeeded", "degraded", "needs_input", "failed"]


class SourceRef(BaseModel):
    sheet: str
    addr: str
    row: int
    col: int

    @property
    def cell_ref(self) -> str:
        return f"{self.sheet}!{self.addr}"


class MappingEvidence(BaseModel):
    method: MapMethod
    score: float | None = None
    confidence: Confidence | None = None
    alternatives: list[tuple[str, float]] = Field(default_factory=list)
    source: str | None = None
    evidence: str | None = None
    disposition: str = "mapped"
    exclusion_reason: str | None = None


class PeriodValue(BaseModel):
    period_key: str
    header_text: str
    role: str
    cached_value: str | None = None
    has_formula: bool = False
    number_format: str | None = None
    source: SourceRef
    missing_cached_value: bool = False
    formula: str | None = None


class CandidateHit(BaseModel):
    concept_id: str
    score: float
    evidence: str | None = None


class NumericSummary(BaseModel):
    first: str | None = None
    last: str | None = None
    minimum: str | None = None
    maximum: str | None = None
    constant: bool = False
    n: int = 0


class RowHints(BaseModel):
    nature: str | None = None
    time_semantics: str | None = None
    statement: str | None = None
    unit: str | None = None
    currency: str | None = None
    scale: str | None = None
    sign: str | None = None
    segment: str | None = None
    escalation: str | None = None


class RoleCell(BaseModel):
    addr: str
    col: int
    role: str
    cached_value: str | None = None


class MetricSeries(BaseModel):
    row_key: str
    label: str
    parent_label: str | None = None
    concept_id: str | None = None
    article_role: str
    unit: str | None = None
    mapping: MappingEvidence
    values: list[PeriodValue] = Field(default_factory=list)
    source: SourceRef
    disposition: str = "mapped"
    exclusion_reason: str | None = None
    kind: str | None = None
    indent: int = 0
    hidden: bool = False
    check_row: bool = False
    label_path: list[str] = Field(default_factory=list)
    neighbors: list[str] = Field(default_factory=list)
    formula_fingerprint: str | None = None
    formula_exceptions: list[str] = Field(default_factory=list)
    numeric_summary: NumericSummary | None = None
    candidates: list[CandidateHit] = Field(default_factory=list)
    hints: RowHints = Field(default_factory=RowHints)
    cells: list[RoleCell] = Field(default_factory=list)
    context_role: str | None = None
    secondary_concepts: list[str] = Field(default_factory=list)


class InventoryRow(BaseModel):
    row_key: str
    sheet: str
    row: int
    kind: str
    label: str
    parent_label: str | None = None
    label_path: list[str] = Field(default_factory=list)
    indent: int = 0
    hidden: bool = False
    check_row: bool = False
    neighbors: list[str] = Field(default_factory=list)
    concept_id: str | None = None
    disposition: str | None = None
    exclusion_reason: str | None = None
    unit: str | None = None
    formula_fingerprint: str | None = None
    formula_exceptions: list[str] = Field(default_factory=list)
    numeric_summary: NumericSummary | None = None
    candidates: list[CandidateHit] = Field(default_factory=list)
    hints: RowHints = Field(default_factory=RowHints)
    cells: list[RoleCell] = Field(default_factory=list)
    context_role: str | None = None
    secondary_concepts: list[str] = Field(default_factory=list)


class ModelPeriod(BaseModel):
    period_id: str
    index: int
    phase: TimelinePhase | None = None
    phase_year: int | None = None
    calendar_year: str | None = None
    flags: dict[str, bool] = Field(default_factory=dict)


class WorkbookTimeline(BaseModel):
    grain: str | None = None
    source_block_id: str | None = None
    periods: list[ModelPeriod] = Field(default_factory=list)


class FinancialBlock(BaseModel):
    block_id: str
    sheet: str
    label_col: int
    grain: str | None = None
    kind: str = "timeline"
    periods: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[MetricSeries] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)


class WorkbookRaw(BaseModel):
    sheets: list[str] = Field(default_factory=list)
    sheet_count: int = 0
    cell_count: int = 0
    has_vba: bool = False
    has_xlm: bool = False
    externals: list[str] = Field(default_factory=list)
    locale_hint: str | None = None
    date1904: bool = False
    defined_names: list[dict[str, Any]] = Field(default_factory=list)
    iterate: bool = False
    formula_count: int = 0
    missing_cached_values: int = 0
    unparsed_formulas: int = 0


class ArtifactMeta(BaseModel):
    schema_version: str = SCHEMA_VERSION
    job_id: str
    status: JobStatus = "running"
    stage: str = "queued"
    source_filename: str | None = None
    content_sha256: str | None = None
    warnings: list[str] = Field(default_factory=list)
    questions: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class GraphPointer(BaseModel):
    artifact: str = "graph.json"
    cell_edges: str = "ir/cell_edges.parquet"
    index: str = "ir/graph_index.parquet"
    nodes: int = 0
    edges: int = 0
    cycles_unexpected: int = 0
    cycles_iterative: int = 0
    iterate: bool = False
    unresolved: int = 0
    dangling: int = 0
    empty_range_members: int = 0


class MappingStats(BaseModel):
    """Coverage of layout rows. `unmapped` on the document is abstained series, not inventory without concept_id."""

    inventory_rows: int = 0
    mapped: int = 0
    abstained: int = 0
    excluded: int = 0
    abstract: int = 0
    unmapped_series: int = 0
    content_completeness: float = 1.0
    concept_coverage: float = 0.0


class ContextDocument(BaseModel):
    schema_version: str = SCHEMA_VERSION
    meta: ArtifactMeta
    workbook: WorkbookRaw
    timeline: WorkbookTimeline | None = None
    blocks: list[FinancialBlock] = Field(default_factory=list)
    unmapped: list[MetricSeries] = Field(default_factory=list)
    excluded: list[MetricSeries] = Field(default_factory=list)
    inventory: list[InventoryRow] = Field(default_factory=list)
    mapping_stats: MappingStats = Field(default_factory=MappingStats)
    graph: GraphPointer = Field(default_factory=GraphPointer)
    warnings: list[str] = Field(default_factory=list)
