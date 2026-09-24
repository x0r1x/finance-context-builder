from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_serializer

from finance_context.vocab import PeriodPosition, SeriesAggregation, ValueStatus

SCHEMA_VERSION = "1.13.0"

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


class CandidateHit(BaseModel):
    concept_id: str
    score: float
    evidence: str | None = None


class SemanticIdentity(BaseModel):
    """Economic meaning of the line. Not the statement slot and not a rival candidate."""

    family: str
    concept_id: str
    confidence: float


class ReportingRole(BaseModel):
    """Where this row is used: selected statement concept, or a layout/calculation role."""

    role: str
    confidence: float
    selected: bool = False


class CashSemantics(BaseModel):
    recognition: Literal["accrual", "cash", "noncash", "rate", "stock"]
    cash_movement: Literal["inflow", "outflow", "none"]


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
    unit_per: str | None = None
    currency: str | None = None
    scale: str | None = None
    sign: str | None = None
    segment: str | None = None
    escalation: str | None = None


class RoleCell(BaseModel):
    """A cell left of the ruler. `header` is the caption above it (Start, End, Live Case)."""

    addr: str
    col: int
    role: str
    cached_value: str | None = None
    header: str | None = None


class RowSeries(BaseModel):
    """Values of one row on one axis. Aligned to that axis's periods."""

    axis_id: str
    formula: str | None = None
    formula_exceptions: list[str] = Field(default_factory=list)
    numeric_summary: NumericSummary | None = None
    values: list[str | None] = Field(default_factory=list)
    value_statuses: list[ValueStatus] = Field(default_factory=list)
    normalized_values: list[str | None] = Field(default_factory=list)


class BlockRow(BaseModel):
    """One layout line. Lives only inside its block."""

    row_key: str
    sheet: str
    row: int
    label: str
    parent_label: str | None = None
    label_path: list[str] = Field(default_factory=list)
    kind: str
    disposition: str | None = None
    exclusion_reason: str | None = None
    concept_id: str | None = None
    article_role: str | None = None
    unit: str | None = None
    formula: str | None = None
    formula_exceptions: list[str] = Field(default_factory=list)
    indent: int = 0
    hidden: bool = False
    check_row: bool = False
    neighbors: list[str] = Field(default_factory=list)
    numeric_summary: NumericSummary | None = None
    candidates: list[CandidateHit] = Field(default_factory=list)
    hints: RowHints = Field(default_factory=RowHints)
    cells: list[RoleCell] = Field(default_factory=list)
    mapping: MappingEvidence | None = None
    context_role: str | None = None
    secondary_concepts: list[str] = Field(default_factory=list)
    semantic_identity: SemanticIdentity | None = None
    reporting_roles: list[ReportingRole] = Field(default_factory=list)
    cash_semantics: CashSemantics | None = None
    period_position: PeriodPosition | None = None
    aggregation: SeriesAggregation | None = None
    scale_factor: int | None = None
    values: list[str | None] = Field(default_factory=list)
    value_statuses: list[ValueStatus] = Field(default_factory=list)
    normalized_values: list[str | None] = Field(default_factory=list)
    series: list[RowSeries] = Field(default_factory=list)


class ModelPeriod(BaseModel):
    period_id: str
    index: int
    phase: TimelinePhase | None = None
    phase_year: int | None = None
    calendar_year: str | None = None
    flags: dict[str, bool] = Field(default_factory=dict)


class ContextPeriod(BaseModel):
    col: int
    text: str = ""
    role: str = "historical"
    period_key: str
    group_key: str | None = None
    index: int = 0
    phase: TimelinePhase | None = None
    phase_year: int | None = None
    calendar_year: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    flags: dict[str, bool] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        for key in ("start_date", "end_date"):
            if data.get(key) is None:
                data.pop(key, None)
        if data.get("phase") is None:
            data.pop("phase", None)
            data.pop("phase_year", None)
        if not data.get("flags"):
            data.pop("flags", None)
        if data.get("group_key") is None:
            data.pop("group_key", None)
        calendar = data.get("calendar_year")
        key = str(data.get("period_key") or "")
        if calendar is not None and (calendar == key or calendar == key[:4]):
            data.pop("calendar_year", None)
        return data


class ContextAxis(BaseModel):
    id: str
    sheet: str
    grain: str | None = None
    header_row: int
    # Shared key sequence. Equals ``id`` when this axis is not grouped.
    timeline_id: str | None = None
    periods: list[ContextPeriod] = Field(default_factory=list)


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
    axis_ids: list[str] = Field(default_factory=list)
    periods: list[dict[str, Any]] = Field(default_factory=list)
    rows: list[BlockRow] = Field(default_factory=list)
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


class MappingQuality(BaseModel):
    """Checks on accepted concepts. Independent of `concept_coverage`."""

    label_coverage: float = 0.0
    semantic_coverage: float = 0.0
    unit_coverage: float = 0.0
    temporal_coverage: float = 0.0
    formula_coverage: float = 0.0
    confidence_threshold_passed: bool = False


class MappingStats(BaseModel):
    """Layout coverage plus semantic quality of accepted concepts.

    `unmapped_series` counts abstained fact lines inside blocks.
    `concept_coverage` is the share of annotatable rows with an accepted concept.
    `mapping_quality` scores label, semantic, unit, temporal, and formula checks.
    """

    inventory_rows: int = 0
    mapped: int = 0
    abstained: int = 0
    excluded: int = 0
    abstract: int = 0
    unmapped_series: int = 0
    content_completeness: float = 1.0
    concept_coverage: float = 0.0
    mapping_quality: MappingQuality = Field(default_factory=MappingQuality)


class ContextDocument(BaseModel):
    schema_version: str = SCHEMA_VERSION
    meta: ArtifactMeta
    workbook: WorkbookRaw
    axes: list[ContextAxis] = Field(default_factory=list)
    blocks: list[FinancialBlock] = Field(default_factory=list)
    mapping_stats: MappingStats = Field(default_factory=MappingStats)
    graph: GraphPointer = Field(default_factory=GraphPointer)
    warnings: list[str] = Field(default_factory=list)
