from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from finance_context.layout.models import RowKind

ArticleRole = Literal[
    "assumption",
    "calculation",
    "database_like",
    "check",
    "output",
    "actual_adjustment",
]
MapSource = Literal[
    "glossary",
    "embed",
    "chat",
    "question",
    "rule",
    "structure",
    "lexical",
]
Confidence = Literal["high", "medium", "low"]
ValueKind = Literal["money", "rate", "ratio", "count"]
Disposition = Literal["mapped", "excluded", "abstained"]
ExclusionReason = Literal[
    "check",
    "helper",
    "noise",
    "technical_bridge",
    "no_candidate",
    "facet_mismatch",
    "ambiguous",
    "low_score",
]


class Concept(BaseModel):
    id: str
    labels: list[str]
    definition: str | None = None
    statements: list[str] = Field(default_factory=list)
    value_kind: ValueKind | None = None
    role: str | None = None
    broader: str | None = None
    aliases: list[str] = Field(default_factory=list)
    anti_labels: list[str] = Field(default_factory=list)
    section_hints: list[str] = Field(default_factory=list)


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
    evidence: str | None = None
    disposition: Disposition = "mapped"
    exclusion_reason: ExclusionReason | None = None


class MappingQuestion(BaseModel):
    id: str
    kind: Literal["mapping", "identity_gap", "explain"] = "mapping"
    prompt: str
    cell_refs: list[str]
    options: list[str]


class RowRelation(BaseModel):
    kind: str
    source_row_key: str
    target_row_key: str | None = None
    member_row_keys: list[str] = Field(default_factory=list)
    evidence: str | None = None


class MappingDocument(BaseModel):
    rows: list[MappedRow] = Field(default_factory=list)
    questions: list[MappingQuestion] = Field(default_factory=list)
    relations: list[RowRelation] = Field(default_factory=list)


class Candidate(BaseModel):
    concept_id: str
    score: float
    signal: str
    evidence: str


class RowContext(BaseModel):
    row_key: str
    sheet: str
    row: int
    block_id: str
    label: str
    parent_label: str | None = None
    section_path: list[str] = Field(default_factory=list)
    kind: RowKind = "fact"
    value_kind: ValueKind = "money"
    is_total: bool = False
    period_grain: str | None = None
    period_headers: list[str] = Field(default_factory=list)
    article_role: ArticleRole = "database_like"
    query_text: str = ""
    label_col: int = 1
