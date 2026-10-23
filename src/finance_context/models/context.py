from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_serializer

from finance_context.vocab import ValueStatus

SCHEMA_VERSION = "1.14.0"

JobStatus = Literal["queued", "running", "succeeded", "degraded", "needs_input", "failed"]


class SourceRef(BaseModel):
    sheet: str
    addr: str
    row: int
    col: int

    @property
    def cell_ref(self) -> str:
        return f"{self.sheet}!{self.addr}"


class NumericSummary(BaseModel):
    first: str | None = None
    last: str | None = None
    minimum: str | None = None
    maximum: str | None = None
    constant: bool = False
    n: int = 0


class RowHints(BaseModel):
    unit: str | None = None
    unit_per: str | None = None
    currency: str | None = None
    scale: str | None = None


class RoleCell(BaseModel):
    """A cell left of the ruler. `header` is the caption above it (Start, End, Live Case)."""

    addr: str
    col: int
    role: str
    cached_value: str | None = None
    header: str | None = None
    hidden: bool = False

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        if not data.get("hidden"):
            data.pop("hidden", None)
        return data


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
    unit: str | None = None
    formula: str | None = None
    formula_exceptions: list[str] = Field(default_factory=list)
    indent: int = 0
    hidden: bool = False
    check_row: bool = False
    neighbors: list[str] = Field(default_factory=list)
    # Large ranges (ids in ContextDocument.ranges) this row's formula reads.
    # Their member rows are not repeated in ``neighbors``.
    neighbor_ranges: list[str] = Field(default_factory=list)
    numeric_summary: NumericSummary | None = None
    hints: RowHints = Field(default_factory=RowHints)
    cells: list[RoleCell] = Field(default_factory=list)
    scale_factor: int | None = None
    values: list[str | None] = Field(default_factory=list)
    value_statuses: list[ValueStatus] = Field(default_factory=list)
    normalized_values: list[str | None] = Field(default_factory=list)
    series: list[RowSeries] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        if not data.get("neighbor_ranges"):
            data.pop("neighbor_ranges", None)
        return data


class ContextRange(BaseModel):
    """A large range read by formula rows, listed once instead of per member row.

    ``col_start``/``col_end`` are None for whole rows, ``row_start``/``row_end``
    for whole columns. ``cells`` counts present cells inside. ``readers`` are
    the rows (``Sheet!12``) whose formula reads it.
    """

    id: str
    sheet: str
    col_start: str | None = None
    col_end: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    cells: int = 0
    readers: list[str] = Field(default_factory=list)


class ModelPeriod(BaseModel):
    period_id: str
    index: int
    calendar_year: str | None = None
    flags: dict[str, bool] = Field(default_factory=dict)


class ContextPeriod(BaseModel):
    col: int
    text: str = ""
    period_key: str
    group_key: str | None = None
    index: int = 0
    calendar_year: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    flags: dict[str, bool] = Field(default_factory=dict)
    hidden: bool = False

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        for key in ("start_date", "end_date"):
            if data.get(key) is None:
                data.pop(key, None)
        if not data.get("hidden"):
            data.pop("hidden", None)
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


class ContextDocument(BaseModel):
    schema_version: str = SCHEMA_VERSION
    meta: ArtifactMeta
    workbook: WorkbookRaw
    axes: list[ContextAxis] = Field(default_factory=list)
    blocks: list[FinancialBlock] = Field(default_factory=list)
    ranges: list[ContextRange] = Field(default_factory=list)
    graph: GraphPointer = Field(default_factory=GraphPointer)
    warnings: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        if not data.get("ranges"):
            data.pop("ranges", None)
        return data
