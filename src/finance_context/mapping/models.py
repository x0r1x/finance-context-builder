from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
StatementKind = Literal["pnl", "bs", "cf", "cov", "val", "ops", "fx"]
NatureKind = Literal["flow", "balance"]
BasisKind = Literal["cash", "accrual", "noncash"]
DirectionKind = Literal["inflow", "outflow"]
PositionKind = Literal["opening", "closing"]
SeriesKind = Literal["constant", "series"]
Disposition = Literal["mapped", "excluded", "abstained"]
ExclusionReason = Literal[
    "check",
    "helper",
    "flag",
    "noise",
    "technical_bridge",
    "no_candidate",
    "facet_mismatch",
    "ambiguous",
    "low_score",
    "calculation_conflict",
]


class Facets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: StatementKind | None = None
    nature: NatureKind | None = None
    basis: BasisKind | None = None
    direction: DirectionKind | None = None
    position: PositionKind | None = None
    series: SeriesKind | None = None
    unit: ValueKind | None = None


class FacetGuess(BaseModel):
    value: str | None = None
    confident: bool = False


class InferredFacets(BaseModel):
    statement: FacetGuess = Field(default_factory=FacetGuess)
    nature: FacetGuess = Field(default_factory=FacetGuess)
    basis: FacetGuess = Field(default_factory=FacetGuess)
    direction: FacetGuess = Field(default_factory=FacetGuess)
    position: FacetGuess = Field(default_factory=FacetGuess)
    series: FacetGuess = Field(default_factory=FacetGuess)
    unit: FacetGuess = Field(default_factory=FacetGuess)


class CalcTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept: str
    weight: float = 1.0


class Calculation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent: str
    terms: list[CalcTerm] = Field(default_factory=list)
    origin: Literal["declared", "broader"] = "declared"


class PatternWhen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label_contains: list[str] = Field(default_factory=list)
    label_in: list[str] = Field(default_factory=list)
    label_tokens: list[str] = Field(default_factory=list)
    label_excludes: list[str] = Field(default_factory=list)
    section_contains: list[str] = Field(default_factory=list)
    any: list[PatternWhen] = Field(default_factory=list)
    unless: PatternWhen | None = None


class LexicalPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept: str | None = None
    skip_concept: str | None = None
    score: float = 0.9
    evidence: str = "pattern"
    when: PatternWhen


class Concept(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    facets: Facets = Field(default_factory=Facets)
    exact_labels: list[str] = Field(default_factory=list)
    deprecated: bool = False
    replaced_by: str | None = None
    match: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _sync_unit_alias(self) -> Concept:
        if self.value_kind and self.facets.unit is None:
            self.facets = self.facets.model_copy(update={"unit": self.value_kind})
        elif self.facets.unit and self.value_kind is None:
            self.value_kind = self.facets.unit
        return self


class TaxonomyDocument(BaseModel):
    version: int = 1
    facet_defaults: dict[str, Facets] = Field(default_factory=dict)
    concepts: list[Concept] = Field(default_factory=list)
    calculations: list[Calculation] = Field(default_factory=list)
    patterns: list[LexicalPattern] = Field(default_factory=list)


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
    inferred_facets: InferredFacets = Field(default_factory=InferredFacets)
