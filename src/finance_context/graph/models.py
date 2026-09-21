from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

GRAPH_SCHEMA_VERSION = "1.4.0"
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


class GraphContract(BaseModel):
    source_of_truth: str = "parquet"
    edges: str = "ir/cell_edges.parquet"
    formulas: str = "ir/cells.parquet"
    edges_json: str = "graph-edges.json"
    dangling: str = "graph-dangling.json"
    formulas_json: str = "formulas.json"


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
    artifacts: dict[str, str] = Field(
        default_factory=lambda: {
            "cells": "ir/cells.parquet",
            "edges": "ir/edges.parquet",
            "cell_edges": "ir/cell_edges.parquet",
            "index": "ir/graph_index.parquet",
            "edges_json": "graph-edges.json",
            "dangling": "graph-dangling.json",
            "formulas": "formulas.json",
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
