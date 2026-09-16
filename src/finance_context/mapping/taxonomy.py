from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from finance_context.mapping.facets import enrich_concept, inherit_facets
from finance_context.mapping.models import (
    CalcTerm,
    Calculation,
    Concept,
    Facets,
    LexicalPattern,
    TaxonomyDocument,
)

_DEFAULT = Path(__file__).resolve().parents[1] / "ontology" / "taxonomy.yaml"
_DOCS: dict[tuple[str, ...], TaxonomyDocument] = {}


class TaxonomyError(ValueError):
    """Invalid taxonomy document."""


def load_taxonomy(path: Path | None = None) -> list[Concept]:
    return list(load_taxonomy_document(path).concepts)


def load_taxonomy_document(path: Path | None = None) -> TaxonomyDocument:
    target = path or _DEFAULT
    resolved = str(target.resolve())
    mtime = target.stat().st_mtime
    return _load_cached(resolved, mtime)


def attached_document(concepts: list[Concept]) -> TaxonomyDocument | None:
    key = tuple(c.id for c in concepts)
    return _DOCS.get(key)


def register_document(doc: TaxonomyDocument) -> None:
    _DOCS[tuple(c.id for c in doc.concepts)] = doc


@lru_cache(maxsize=16)
def _load_cached(resolved: str, mtime: float) -> TaxonomyDocument:
    raw = yaml.safe_load(Path(resolved).read_text(encoding="utf-8")) or {}
    defaults = {
        prefix: Facets.model_validate(values or {})
        for prefix, values in (raw.get("facet_defaults") or {}).items()
    }
    concepts = [Concept.model_validate(item) for item in raw.get("concepts") or []]
    calculations = [Calculation.model_validate(item) for item in raw.get("calculations") or []]
    patterns = [LexicalPattern.model_validate(item) for item in raw.get("patterns") or []]
    by_id = {c.id: c for c in concepts}
    validate_taxonomy(concepts, defaults=defaults, calculations=calculations)
    enriched = [enrich_concept(c, by_id=by_id, defaults=defaults) for c in concepts]
    doc = TaxonomyDocument(
        version=int(raw.get("version") or 1),
        facet_defaults=defaults,
        concepts=enriched,
        calculations=calculations,
        patterns=patterns,
    )
    _DOCS[tuple(c.id for c in enriched)] = doc
    return doc


def implicit_calculations(concepts: list[Concept]) -> list[Calculation]:
    children: dict[str, list[str]] = {}
    for concept in concepts:
        if concept.broader:
            children.setdefault(concept.broader, []).append(concept.id)
    return [
        Calculation(
            parent=parent,
            terms=[CalcTerm(concept=cid, weight=1.0) for cid in kids],
            origin="broader",
        )
        for parent, kids in children.items()
        if parent in {c.id for c in concepts}
    ]


def validate_taxonomy(
    concepts: list[Concept],
    *,
    defaults: dict[str, Facets] | None = None,
    calculations: list[Calculation] | None = None,
) -> None:
    ids = [c.id for c in concepts]
    duplicates = sorted({cid for cid in ids if ids.count(cid) > 1})
    if duplicates:
        raise TaxonomyError(f"duplicate concept ids: {duplicates}")
    by_id = {c.id: c for c in concepts}
    for concept in concepts:
        if concept.broader and concept.broader not in by_id:
            raise TaxonomyError(f"{concept.id} broader {concept.broader!r} is missing")
        if concept.deprecated and not concept.replaced_by:
            raise TaxonomyError(f"{concept.id} is deprecated without replaced_by")
        if concept.replaced_by and concept.replaced_by not in by_id:
            raise TaxonomyError(f"{concept.id} replaced_by {concept.replaced_by!r} is missing")
        _assert_no_cycle(concept, by_id)
        _assert_facet_agreement(concept, by_id, defaults or {})
    for calc in calculations or []:
        if calc.parent not in by_id:
            raise TaxonomyError(f"calculation parent {calc.parent!r} is missing")
        for term in calc.terms:
            if term.concept not in by_id:
                raise TaxonomyError(f"calculation term {term.concept!r} is missing")


def _assert_no_cycle(concept: Concept, by_id: dict[str, Concept]) -> None:
    seen: set[str] = set()
    current: Concept | None = concept
    while current is not None:
        if current.id in seen:
            raise TaxonomyError(f"broader cycle involving {concept.id}")
        seen.add(current.id)
        current = by_id.get(current.broader) if current.broader else None


def _assert_facet_agreement(
    concept: Concept,
    by_id: dict[str, Concept],
    defaults: dict[str, Facets],
) -> None:
    if not concept.broader or concept.broader not in by_id:
        return
    parent = inherit_facets(by_id[concept.broader], by_id, defaults)
    child = concept.facets
    parent_data = parent.model_dump()
    child_data = child.model_dump()
    for key, value in child_data.items():
        inherited = parent_data.get(key)
        if value is not None and inherited is not None and value != inherited:
            raise TaxonomyError(
                f"{concept.id} facet {key}={value!r} conflicts with parent {inherited!r}"
            )
