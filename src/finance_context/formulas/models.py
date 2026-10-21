from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

EdgeKind = Literal["ref", "range", "cross_sheet", "external", "dynamic"]


@dataclass(slots=True)
class Edge:
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


@dataclass(slots=True)
class ParsedFormula:
    ast: dict[str, Any] | None
    template: str | None
    unparsed: bool
    edges: list[Edge] = field(default_factory=list)


@dataclass
class CompileResult:
    cells: list[dict[str, Any]] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    cell_edges: list[Any] = field(default_factory=list)


def as_edge(value: Edge | Mapping[str, Any]) -> Edge:
    """One formula edge. A dict from parquet is copied once; an Edge is reused."""
    if isinstance(value, Edge):
        return value
    target = value.get("target")
    return Edge(
        kind=value.get("kind") or "ref",  # type: ignore[arg-type]
        source=str(value.get("source") or ""),
        target=None if target is None else str(target),
        unresolved=bool(value.get("unresolved")),
        truncated=bool(value.get("truncated")),
        abs_col=_opt_bool(value.get("abs_col")),
        abs_row=_opt_bool(value.get("abs_row")),
        abs_col_end=_opt_bool(value.get("abs_col_end")),
        abs_row_end=_opt_bool(value.get("abs_row_end")),
        named=bool(value.get("named")),
    )


def _opt_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)
