from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ColumnRole = Literal[
    "historical",
    "forecast",
    "stub",
    "scenario",
    "total",
    "relative",
    "label",
    "value",
    "unit",
    "note",
]
RowKind = Literal["fact", "abstract", "index", "helper", "flag"]
BlockKind = Literal["timeline", "params"]


class PeriodHit(BaseModel):
    role: ColumnRole
    period_key: str
    explicit_role: bool = False


class AxisHeader(BaseModel):
    col: int
    text: str
    role: ColumnRole
    period_key: str


class Axis(BaseModel):
    id: str
    row: int
    headers: list[AxisHeader]


class AxisPeriod(BaseModel):
    col: int
    text: str
    role: ColumnRole
    period_key: str
    group_key: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class TimeAxis(BaseModel):
    """One period axis on a sheet. Tables reference it; they do not own a copy."""

    id: str
    grain: str | None = None
    header_row: int
    periods: list[AxisPeriod]


class RowCell(BaseModel):
    col: int
    role: ColumnRole
    header: str | None = None


class LayoutRow(BaseModel):
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
    rows: list[LayoutRow]
    kind: BlockKind = "timeline"


class SheetLayout(BaseModel):
    name: str
    axes: list[TimeAxis] = Field(default_factory=list)
    blocks: list[Block]


class Layout(BaseModel):
    sheets: list[SheetLayout]
