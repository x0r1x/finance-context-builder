from __future__ import annotations

from finance_context.mapping.models import (
    Candidate,
    Concept,
    Facets,
    RowContext,
    ValueKind,
)

_COMPATIBLE: dict[ValueKind, set[str]] = {
    "money": {"money"},
    "rate": {"rate"},
    "ratio": {"ratio", "rate"},
    "count": {"count"},
}

_RATIO_LABELS = (
    "dscr",
    "llcr",
    "plcr",
    "coverage",
    "conversion",
    "leverage",
    "runway",
    "ratio",
)

_FACET_FIELDS = (
    "statement",
    "nature",
    "basis",
    "direction",
    "position",
    "series",
    "unit",
)


def merge_facets(base: Facets, overlay: Facets) -> Facets:
    data = base.model_dump()
    for key, value in overlay.model_dump().items():
        if value is not None:
            data[key] = value
    return Facets.model_validate(data)


def inherit_facets(
    concept: Concept,
    by_id: dict[str, Concept],
    defaults: dict[str, Facets],
) -> Facets:
    chain: list[Concept] = []
    seen: set[str] = set()
    current: Concept | None = concept
    while current is not None and current.id not in seen:
        seen.add(current.id)
        chain.append(current)
        current = by_id.get(current.broader) if current.broader else None
    merged = Facets()
    prefix = concept.id.split(".", 1)[0]
    if prefix in defaults:
        merged = merge_facets(merged, defaults[prefix])
    for item in reversed(chain):
        merged = merge_facets(merged, item.facets)
    return merged


def enrich_concept(
    concept: Concept,
    *,
    by_id: dict[str, Concept] | None = None,
    defaults: dict[str, Facets] | None = None,
) -> Concept:
    facets = inherit_facets(concept, by_id or {}, defaults or {})
    unit: ValueKind = facets.unit or concept.value_kind or "money"
    facets = facets.model_copy(update={"unit": unit})
    statements = list(concept.statements)
    if not statements and facets.statement:
        statements = [facets.statement]
    definition = concept.definition or f"{concept.id}: {', '.join(concept.labels)}"
    return concept.model_copy(
        update={
            "facets": facets,
            "value_kind": unit,
            "statements": statements,
            "definition": definition,
        }
    )


def prune_candidates(
    candidates: list[Candidate],
    ctx: RowContext,
    taxonomy: dict[str, Concept],
) -> list[Candidate]:
    allowed = _COMPATIBLE.get(ctx.value_kind, {"money"})
    label = (ctx.label or "").casefold()
    kept: list[Candidate] = []
    for item in candidates:
        concept = taxonomy.get(item.concept_id)
        if concept is None:
            continue
        kind = concept.value_kind or concept.facets.unit or "money"
        if kind not in allowed and not _ratio_label_exception(label, kind, ctx.value_kind):
            continue
        if any(anti.casefold() in label for anti in concept.anti_labels if anti):
            continue
        skip_nature = item.signal == "lexical" and item.score >= 0.9
        if not _facets_compatible(ctx, concept, skip_fields={"nature"} if skip_nature else set()):
            continue
        kept.append(item)
    return kept


def _facets_compatible(
    ctx: RowContext,
    concept: Concept,
    skip_fields: set[str] | None = None,
) -> bool:
    inferred = ctx.inferred_facets
    concept_values = concept.facets.model_dump()
    skip = skip_fields or set()
    for field in _FACET_FIELDS:
        if field == "unit" or field in skip:
            continue
        guess = getattr(inferred, field)
        if not guess.confident or not guess.value:
            continue
        expected = concept_values.get(field)
        if expected is None:
            continue
        if str(expected) != str(guess.value):
            return False
    return True


def _ratio_label_exception(label: str, concept_kind: str, row_kind: ValueKind) -> bool:
    if concept_kind != "ratio":
        return False
    if row_kind == "money" and any(token in label for token in _RATIO_LABELS):
        return True
    return False
