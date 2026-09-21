from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GRAPH_SCHEMA_VERSION = "1.6.0"
CycleClass = Literal["iterative_ok", "unexpected"]
ID_CAP = 32


class CycleRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    members: list[str]
    class_: CycleClass = Field(alias="class")
    breakers: list[str] = Field(default_factory=list)


class CircularityHint(BaseModel):
    sheet: str
    row: int
    label: str
    cell_ids: list[str] = Field(default_factory=list)


class IdCount(BaseModel):
    count: int = 0
    ids: list[str] = Field(default_factory=list)


class FormulaLink(BaseModel):
    """One formula cell. A range stays one ref.

    `row_key` and `period_id` join the cell to a context row and its axis.
    Both are null when the cell is outside layout.
    """

    cell: str
    formula: str | None = None
    refs: list[str] = Field(default_factory=list)
    row_key: str | None = None
    period_id: str | None = None


class GraphContract(BaseModel):
    source_of_truth: str = "parquet"
    edges: str = "ir/cell_edges.parquet"
    formulas: str = "ir/cells.parquet"


class GraphDocument(BaseModel):
    schema_version: str = GRAPH_SCHEMA_VERSION
    job_id: str
    iterate: bool = False
    nodes: int = 0
    edges: int = 0
    kinds: dict[str, int] = Field(default_factory=dict)
    contract: GraphContract = Field(default_factory=GraphContract)
    unresolved: IdCount = Field(default_factory=IdCount)
    dynamic: IdCount = Field(default_factory=IdCount)
    external: IdCount = Field(default_factory=IdCount)
    truncated: IdCount = Field(default_factory=IdCount)
    dangling: IdCount = Field(default_factory=IdCount)
    dangling_classes: dict[str, int] = Field(default_factory=dict)
    cycles: list[CycleRecord] = Field(default_factory=list)
    circularity_hints: list[CircularityHint] = Field(default_factory=list)
    links: list[FormulaLink] = Field(default_factory=list)
    artifacts: dict[str, str] = Field(
        default_factory=lambda: {
            "cells": "ir/cells.parquet",
            "edges": "ir/edges.parquet",
            "cell_edges": "ir/cell_edges.parquet",
            "index": "ir/graph_index.parquet",
        }
    )


class TraceNode(BaseModel):
    node_id: str
    sheet: str | None = None
    addr: str | None = None
    row_key: str | None = None
    concept_id: str | None = None
    period_id: str | None = None
    node_type: str | None = None
    formula: str | None = None
    formula_template: str | None = None
    cached_value: str | None = None
    depth: int = 0


class TraceEdge(BaseModel):
    source: str
    target: str
    kind: str | None = None
    period_lag: str | None = None
    col_offset: int | None = None


class TraceDocument(BaseModel):
    origin: str
    direction: Literal["precedents", "dependents"]
    depth: int
    stopped: str | None = None
    nodes: list[TraceNode] = Field(default_factory=list)
    edges: list[TraceEdge] = Field(default_factory=list)
