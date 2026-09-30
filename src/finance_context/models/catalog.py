"""Catalog projection. Rows and axes without cell caches."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_serializer

from finance_context.models.context import TimelinePhase
from finance_context.vocab import PeriodPosition, SeriesAggregation

CATALOG_SCHEMA_VERSION = "catalog-1"


class SliceMappingStats(BaseModel):
    """Book-level coverage. The six mapping_quality scores stay out."""

    inventory_rows: int = 0
    mapped: int = 0
    abstained: int = 0
    excluded: int = 0
    concept_coverage: float = 0.0


class CatalogHints(BaseModel):
    unit: str | None = None
    currency: str | None = None
    scale: str | None = None
    sign: str | None = None


class CatalogPeriod(BaseModel):
    period_key: str
    phase: TimelinePhase | None = None
    start_date: str | None = None
    end_date: str | None = None

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        if data.get("phase") is None:
            data.pop("phase", None)
        for key in ("start_date", "end_date"):
            if data.get(key) is None:
                data.pop(key, None)
        return data


class CatalogAxis(BaseModel):
    id: str
    sheet: str
    grain: str | None = None
    periods: list[CatalogPeriod] = Field(default_factory=list)


class CatalogRow(BaseModel):
    row_key: str
    sheet: str
    label: str
    label_path: list[str] = Field(default_factory=list)
    concept_id: str | None = None
    disposition: str | None = None
    kind: str
    axis_ids: list[str] = Field(default_factory=list)
    period_position: PeriodPosition | None = None
    aggregation: SeriesAggregation | None = None
    has_formula: bool = False
    hints: CatalogHints = Field(default_factory=CatalogHints)


class CatalogDocument(BaseModel):
    """Rows of one finished job, without values. Schema catalog-1."""

    schema_version: Literal["catalog-1"] = CATALOG_SCHEMA_VERSION
    total: int
    offset: int = 0
    limit: int | None = None
    mapping_stats: SliceMappingStats = Field(default_factory=SliceMappingStats)
    axes: list[CatalogAxis] = Field(default_factory=list)
    rows: list[CatalogRow] = Field(default_factory=list)
