from __future__ import annotations

import json
from pathlib import Path

from finance_context.mapping.models import Candidate, RowContext
from finance_context.mapping.normalize import normalize_label, section_class
from finance_context.mapping.structure import BookView
from finance_context.store.fs import write_json


def load_glossary(path: Path | None) -> dict[tuple[str, str], str]:
    if path is None or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
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


def save_glossary(path: Path, glossary: dict[tuple[str, str], str]) -> None:
    entries = [
        {"label": label, "parent": parent, "concept_id": concept_id}
        for (label, parent), concept_id in sorted(glossary.items())
    ]
    write_json(path, {"entries": entries})


def merge_glossary(
    base: dict[tuple[str, str], str],
    extra: dict[tuple[str, str], str],
) -> dict[tuple[str, str], str]:
    merged = dict(base)
    for key, value in extra.items():
        merged.setdefault(key, value)
    return merged


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


class GlossarySignal:
    name = "glossary"

    def __init__(self, glossary: dict[tuple[str, str], str]) -> None:
        self.glossary = {
            (normalize_label(label), normalize_label(parent)): concept_id
            for (label, parent), concept_id in glossary.items()
        }

    def propose(self, ctx: RowContext, book: BookView) -> list[Candidate]:
        key = (normalize_label(ctx.label), normalize_label(ctx.parent_label))
        concept_id = self.glossary.get(key)
        if concept_id is None:
            concept_id = self.glossary.get((normalize_label(ctx.label), ""))
        if concept_id is None:
            klass = section_class(ctx.parent_label, ctx.section_path, ctx.sheet)
            concept_id = self.glossary.get((normalize_label(ctx.label), klass))
        if not concept_id or concept_id not in book.taxonomy:
            return []
        return [
            Candidate(
                concept_id=concept_id,
                score=1.0,
                signal=self.name,
                evidence="learned glossary",
            )
        ]
