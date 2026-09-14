from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ArticleRole = Literal[
    "assumption",
    "calculation",
    "database_like",
    "check",
    "output",
    "actual_adjustment",
]
MapSource = Literal["glossary", "embed", "chat", "question", "rule"]
Confidence = Literal["high", "medium", "low"]


class Concept(BaseModel):
    id: str
    labels: list[str]


class ConceptPick(BaseModel):
    concept_id: str


class MappedRow(BaseModel):
    row_key: str
    sheet: str
    row: int
    block_id: str
    label: str
    parent_label: str | None = None
    concept_id: str | None = None
    article_role: ArticleRole
    source: MapSource
    score: float | None = None
    confidence: Confidence | None = None
    alternatives: list[tuple[str, float]] = Field(default_factory=list)


class MappingQuestion(BaseModel):
    id: str
    kind: Literal["mapping", "identity_gap", "explain"] = "mapping"
    prompt: str
    cell_refs: list[str]
    options: list[str]


class MappingDocument(BaseModel):
    rows: list[MappedRow] = Field(default_factory=list)
    questions: list[MappingQuestion] = Field(default_factory=list)
