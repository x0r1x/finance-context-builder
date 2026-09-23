from __future__ import annotations

import json
from pathlib import Path

from finance_context.mapping.lexical import _GENERIC_TOTALS, skipped_concept_ids
from finance_context.mapping.models import Candidate, LexicalPattern, RowContext
from finance_context.mapping.normalize import normalize_label, section_class
from finance_context.mapping.structure import BookView
from finance_context.store.fs import update_json

# Same label, two live concepts: accrual/stock identity versus the statement projection.
_STATEMENT_PAIRS = {
    ("pnl.revenue", "cf.receipts"),
    ("pnl.opex", "cf.opex_paid"),
    ("pnl.tax", "cf.tax_paid"),
    ("pnl.interest", "cf.interest_paid"),
    ("bs.equity", "cf.equity_issue"),
}


def load_glossary(path: Path | None) -> dict[tuple[str, str], str]:
    if path is None or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(raw, dict):
        return _glossary_from_payload(raw)
    if isinstance(raw, list):
        return _glossary_from_payload({"entries": raw})
    return {}


def save_glossary(path: Path, glossary: dict[tuple[str, str], str]) -> None:
    """Merge keys with setdefault. A stale full dict must not drop another writer's rows."""

    def mutate(current: dict) -> dict:
        loaded = _glossary_from_payload(current)
        for (label, parent), concept_id in glossary.items():
            key = (normalize_label(label), normalize_label(parent))
            if key[0] and concept_id:
                loaded.setdefault(key, str(concept_id))
        return _glossary_payload(loaded)

    update_json(path, mutate)


def _glossary_from_payload(raw: dict) -> dict[tuple[str, str], str]:
    items = raw.get("entries", raw) if isinstance(raw, dict) else raw
    out: dict[tuple[str, str], str] = {}
    if not isinstance(items, list):
        return {}
    for item in items:
        if not isinstance(item, dict):
            continue
        label = normalize_label(item.get("label"))
        parent = normalize_label(item.get("parent") or item.get("section") or "")
        concept_id = item.get("concept_id")
        if label and concept_id:
            out[(label, parent)] = str(concept_id)
    return out


def _glossary_payload(glossary: dict[tuple[str, str], str]) -> dict:
    entries = [
        {"label": label, "parent": parent, "concept_id": concept_id}
        for (label, parent), concept_id in sorted(glossary.items())
    ]
    return {"entries": entries}


def reconcile_glossary(
    glossary: dict[tuple[str, str], str],
    taxonomy: list,
) -> dict[tuple[str, str], str]:
    """Rewrite stale learned ids when a label now belongs to a different concept."""
    phrase_to_ids: dict[str, set[str]] = {}
    known_ids: set[str] = set()
    for concept in taxonomy:
        cid = getattr(concept, "id", None)
        if not cid:
            continue
        known_ids.add(cid)
        phrases = [
            *getattr(concept, "labels", []),
            *getattr(concept, "aliases", []),
            *getattr(concept, "exact_labels", []),
        ]
        for phrase in phrases:
            n = normalize_label(phrase)
            if n:
                phrase_to_ids.setdefault(n, set()).add(cid)
    out: dict[tuple[str, str], str] = {}
    for key, concept_id in glossary.items():
        label, _parent = key
        owners = phrase_to_ids.get(label, set())
        if len(owners) == 1:
            owner = next(iter(owners))
            if concept_id == owner or concept_id not in known_ids:
                out[key] = owner
            elif _statement_pair(owner, concept_id):
                out[key] = concept_id
            else:
                out[key] = owner
            continue
        if concept_id in owners:
            out[key] = concept_id
            continue
        if owners:
            continue
        if concept_id in known_ids:
            out[key] = concept_id
    return out


def learn_from_rows(
    existing: dict[tuple[str, str], str],
    rows: list,
) -> dict[tuple[str, str], str]:
    learned = dict(existing)
    for row in rows:
        if not getattr(row, "concept_id", None):
            continue
        if getattr(row, "kind", None) == "abstract":
            continue
        if getattr(row, "source", None) not in {"glossary", "rule", "structure", "lexical"}:
            continue
        raw_label = str(getattr(row, "label", "") or "").strip()
        if raw_label.isupper() and (
            "ASSUMPTION" in raw_label
            or raw_label.startswith("COSTS DURING")
            or raw_label.startswith("PROJECT FINANCING")
        ):
            continue
        if getattr(row, "confidence", None) != "high":
            continue
        key = (normalize_label(row.label), normalize_label(row.parent_label))
        learned.setdefault(key, row.concept_id)
        klass = section_class(row.parent_label)
        if klass:
            learned.setdefault((normalize_label(row.label), klass), row.concept_id)
    return learned


def _specific_section(ctx: RowContext) -> bool:
    parent = normalize_label(ctx.parent_label)
    return any(
        normalize_label(section) not in {"", parent} for section in ctx.section_path
    )


def _statement_pair(left: str, right: str) -> bool:
    return (left, right) in _STATEMENT_PAIRS or (right, left) in _STATEMENT_PAIRS


class GlossarySignal:
    name = "glossary"

    def __init__(
        self,
        glossary: dict[tuple[str, str], str],
        patterns: list[LexicalPattern] | None = None,
    ) -> None:
        self.patterns = list(patterns or [])
        self.glossary = {
            (normalize_label(label), normalize_label(parent)): concept_id
            for (label, parent), concept_id in glossary.items()
        }

    def propose(self, ctx: RowContext, book: BookView) -> list[Candidate]:
        label = normalize_label(ctx.label)
        # `Total` under Current assets and under Non-current assets share the
        # sheet parent. A learned pair must not paint both with one concept.
        if label in _GENERIC_TOTALS and _specific_section(ctx):
            return []
        key = (label, normalize_label(ctx.parent_label))
        concept_id = self.glossary.get(key)
        if concept_id is None:
            concept_id = self.glossary.get((normalize_label(ctx.label), ""))
        if concept_id is None:
            klass = section_class(ctx.parent_label, ctx.section_path, ctx.sheet)
            concept_id = self.glossary.get((normalize_label(ctx.label), klass))
        if not concept_id or concept_id not in book.taxonomy:
            return []
        if concept_id in skipped_concept_ids(ctx, self.patterns):
            return []
        return [
            Candidate(
                concept_id=concept_id,
                score=1.0,
                signal=self.name,
                evidence="learned glossary",
            )
        ]
