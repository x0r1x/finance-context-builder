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


class RowCell(BaseModel):
    col: int
    role: ColumnRole


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
    axis: Axis
    rows: list[LayoutRow]
    kind: BlockKind = "timeline"


class SheetLayout(BaseModel):
    name: str
    blocks: list[Block]


class Layout(BaseModel):
    sheets: list[SheetLayout]
