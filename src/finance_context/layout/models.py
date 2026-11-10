from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer
from pydantic.dataclasses import dataclass

# Params-block column roles and non-period cells. Not a period classification.
ColumnRole = Literal[
    "stub",
    "total",
    "relative",
    "label",
    "value",
    "unit",
    "note",
    "index",
    "unknown",
]
# Internal period-column classification. Not written to context.json.
ColumnKind = Literal["period", "total", "stub", "relative"]
RowKind = Literal["fact", "abstract", "index", "helper", "flag"]
BlockKind = Literal["timeline", "params", "key_value", "matrix"]
# Blocks whose columns are values, not a period axis. Timeline code must not
# read them as years.
VALUE_BLOCK_KINDS = frozenset({"params", "key_value", "matrix"})

SERIES_KINDS = frozenset({"period", "stub", "relative"})
STRUCTURAL_PERIOD_KEYS = frozenset({"total", "stub"})


def is_value_block(kind: str | None) -> bool:
    """True for a block that publishes value columns instead of a period axis."""
    return kind in VALUE_BLOCK_KINDS


def is_series_column(kind: str | None, period_key: str) -> bool:
    """A timeline value column, not a total or a stub."""
    return kind in SERIES_KINDS and period_key not in STRUCTURAL_PERIOD_KEYS


class PeriodHit(BaseModel):
    # Shared from the classification cache, so callers cannot change it.
    model_config = ConfigDict(frozen=True)

    kind: ColumnKind
    period_key: str
    # Marker text after normalize_header (suffix letter or marker word).
    variant: str | None = None
    explicit_role: bool = False


class AxisHeader(BaseModel):
    col: int
    text: str
    # Params columns only (value/unit/note/...). Period columns leave this empty.
    role: str | None = None
    kind: ColumnKind | None = None
    variant: str | None = Field(default=None, exclude=True)
    period_key: str
    explicit_role: bool = False
    hidden: bool = False

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        if not data.get("hidden"):
            data.pop("hidden", None)
        if data.get("role") is None:
            data.pop("role", None)
        if data.get("kind") is None:
            data.pop("kind", None)
        return data


class Axis(BaseModel):
    id: str
    row: int
    headers: list[AxisHeader]


class AxisPeriod(BaseModel):
    col: int
    text: str
    kind: ColumnKind = "period"
    variant: str | None = Field(default=None, exclude=True)
    period_key: str
    explicit_role: bool = False
    group_key: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    hidden: bool = False

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        if not data.get("hidden"):
            data.pop("hidden", None)
        return data


class TimeAxis(BaseModel):
    """One period axis on a sheet. Tables reference it; they do not own a copy."""

    id: str
    grain: str | None = None
    header_row: int
    periods: list[AxisPeriod]
    # Ambiguous-marker warnings. Not a context-schema field. Empty notes are
    # omitted; a reused layout.json still carries a note that was raised.
    notes: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        if not data.get("notes"):
            data.pop("notes", None)
        return data


@dataclass(slots=True)
class RowCell:
    col: int
    role: ColumnRole
    header: str | None = None


@dataclass(slots=True)
class LayoutRow:
    row: int
    label: str
    parent_row: int | None = None
    indent: int = 0
    check_row: bool = False
    hidden: bool = False
    kind: RowKind = "fact"
    section_path: list[str] = Field(default_factory=list)
    label_col: int | None = None
    cells: list[RowCell] = Field(default_factory=list)


class Block(BaseModel):
    block_id: str
    label_col: int
    axis: Axis | None = None
    axis_ids: list[str] = Field(default_factory=list)
    # Published timeline ids, parallel to axis_ids. Local columns stay on axis_ids.
    timeline_ids: list[str] = Field(default_factory=list)
    rows: list[LayoutRow]
    kind: BlockKind = "timeline"


class SheetLayout(BaseModel):
    name: str
    hidden: bool = False
    axes: list[TimeAxis] = Field(default_factory=list)
    blocks: list[Block]
    # Coverage remarks. Empty on a sheet whose blocks are unchanged.
    notes: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _slim(self, handler):
        data = handler(self)
        if not data.get("notes"):
            data.pop("notes", None)
        return data


class Layout(BaseModel):
    sheets: list[SheetLayout]
