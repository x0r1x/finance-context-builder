"""Passport of one finished job. Counters only: no links and no cell cache."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finance_context.graph.models import CycleClass, IdCount
from finance_context.models.catalog import SliceMappingStats

SUMMARY_SCHEMA_VERSION = "summary-1"


class SummaryCycle(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    cycle_class: CycleClass = Field(alias="class")
    members: int


class SummaryHint(BaseModel):
    sheet: str
    row: int
    label: str


class SummaryDocument(BaseModel):
    """Graph and workbook counters without links[] or row values. Schema summary-1."""

    schema_version: Literal["summary-1"] = SUMMARY_SCHEMA_VERSION
    context_schema_version: str
    graph_schema_version: str
    source_filename: str | None = None
    content_sha256: str | None = None
    status: str | None = None
    stage: str | None = None
    mapping_stats: SliceMappingStats = Field(default_factory=SliceMappingStats)
    sheets: list[str] = Field(default_factory=list)
    formula_count: int = 0
    missing_cached_values: int = 0
    unparsed_formulas: int = 0
    iterate: bool = False
    defined_name_count: int = 0
    nodes: int = 0
    edges: int = 0
    kinds: dict[str, int] = Field(default_factory=dict)
    unresolved: IdCount = Field(default_factory=IdCount)
    external: IdCount = Field(default_factory=IdCount)
    dangling: IdCount = Field(default_factory=IdCount)
    dynamic: IdCount = Field(default_factory=IdCount)
    truncated: IdCount = Field(default_factory=IdCount)
    dangling_classes: dict[str, int] = Field(default_factory=dict)
    cycles: list[SummaryCycle] = Field(default_factory=list)
    circularity_hints: list[SummaryHint] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
