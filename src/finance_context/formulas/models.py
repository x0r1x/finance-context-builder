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


class ParsedFormula(BaseModel):
    ast: dict[str, Any] | None
    template: str | None
    unparsed: bool
    edges: list[Edge] = Field(default_factory=list)


@dataclass
class CsrGraph:
    nodes: list[str]
    node_index: dict[str, int]
    matrix: Any
    truncated_sources: set[str] = field(default_factory=set)


@dataclass
class CompileResult:
    csr: CsrGraph
