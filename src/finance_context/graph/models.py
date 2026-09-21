from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

GRAPH_SCHEMA_VERSION = "1.0.0"
CycleClass = Literal["iterative_ok", "unexpected"]
ID_CAP = 32


class CycleRecord(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    members: list[str]
    class_: CycleClass = Field(alias="class")


class IdCount(BaseModel):
    count: int = 0
    ids: list[str] = Field(default_factory=list)


class GraphDocument(BaseModel):
    schema_version: str = GRAPH_SCHEMA_VERSION
    job_id: str
    nodes: int = 0
    edges: int = 0
    kinds: dict[str, int] = Field(default_factory=dict)
    unresolved: IdCount = Field(default_factory=IdCount)
    dynamic: IdCount = Field(default_factory=IdCount)
    external: IdCount = Field(default_factory=IdCount)
    truncated: IdCount = Field(default_factory=IdCount)
    dangling: IdCount = Field(default_factory=IdCount)
    cycles: list[CycleRecord] = Field(default_factory=list)
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
    formula_ast: dict[str, Any] | None = None
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
