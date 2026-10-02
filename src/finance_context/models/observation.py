"""One row × period. The only projection that carries a cell cache."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from finance_context.models.context import TimelinePhase
from finance_context.vocab import FormulaClass, PeriodPosition, SeriesAggregation, ValueStatus

OBSERVATION_SCHEMA_VERSION = "observation-1"


class ObservationDimensions(BaseModel):
    segment: str


class ObservationUnit(BaseModel):
    kind: str | None = None
    currency: str | None = None
    scale: str | None = None
    sign: str | None = None


class ObservationPrecedent(BaseModel):
    row_key: str | None = None
    concept_id: str | None = None
    period_id: str | None = None
    value: str | None = None
    cell: str | None = None
    depth: int
    label: str | None = None


class ObservationFormula(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    text: str | None = None
    formula_class: FormulaClass | None = Field(default=None, alias="class")
    precedents: list[ObservationPrecedent] = Field(default_factory=list)
    precedents_total: int = 0


class ObservationSource(BaseModel):
    sheet: str
    cell: str


class ObservationTimeline(BaseModel):
    axis_id: str
    phase: TimelinePhase | None = None
    phase_year: int | None = None
    start_date: str | None = None
    end_date: str | None = None
    group_key: str | None = None
    flags: dict[str, bool] = Field(default_factory=dict)

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        if data.get("phase") is None:
            data.pop("phase", None)
            data.pop("phase_year", None)
        for key in ("start_date", "end_date", "group_key"):
            if data.get(key) is None:
                data.pop(key, None)
        if not data.get("flags"):
            data.pop("flags", None)
        return data


class Observation(BaseModel):
    """Inner object from the llm slice. Absent keys are omitted, not sent as null."""

    model_config = ConfigDict(populate_by_name=True)

    row_key: str
    label: str
    concept_id: str | None = None
    disposition: str | None = None
    dimensions: ObservationDimensions | None = None
    period_id: str
    value: str | None = None
    value_status: ValueStatus
    normalized_value: str | None = None
    scale_factor: int | None = None
    period_position: PeriodPosition | None = None
    aggregation: SeriesAggregation | None = None
    scenario: str | None = None
    unit: ObservationUnit
    formula: ObservationFormula
    source: ObservationSource
    timeline: ObservationTimeline | None = None

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        if data.get("dimensions") is None:
            data.pop("dimensions", None)
        if data.get("scenario") is None:
            data.pop("scenario", None)
        if data.get("timeline") is None:
            data.pop("timeline", None)
        return data


class ObservationDocument(BaseModel):
    """Selected observations. Schema observation-1. Not a file on disk."""

    schema_version: Literal["observation-1"] = OBSERVATION_SCHEMA_VERSION
    truncated: bool = False
    observations: list[Observation] = Field(default_factory=list)
