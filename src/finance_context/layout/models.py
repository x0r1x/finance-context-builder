from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ColumnRole = Literal["historical", "forecast", "stub", "scenario", "total"]


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


class LayoutRow(BaseModel):
    row: int
    label: str
    parent_row: int | None = None
    indent: int = 0
    check_row: bool = False
    hidden: bool = False


class Block(BaseModel):
    block_id: str
    label_col: int
    axis: Axis
    rows: list[LayoutRow]


class SheetLayout(BaseModel):
    name: str
    blocks: list[Block]


class Layout(BaseModel):
    sheets: list[SheetLayout]
