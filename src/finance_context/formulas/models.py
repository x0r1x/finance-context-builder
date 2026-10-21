from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

EdgeKind = Literal["ref", "range", "cross_sheet", "external", "dynamic"]


class Edge(BaseModel):
    kind: EdgeKind
    source: str
    target: str | None = None
    unresolved: bool = False
    truncated: bool = False
    abs_col: bool | None = None
    abs_row: bool | None = None
    abs_col_end: bool | None = None
    abs_row_end: bool | None = None
    named: bool = False


class ParsedFormula(BaseModel):
    ast: dict[str, Any] | None
    template: str | None
    unparsed: bool
    edges: list[Edge] = Field(default_factory=list)


@dataclass
class CompileResult:
    cells: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    cell_edges: list[dict[str, Any]] = field(default_factory=list)
