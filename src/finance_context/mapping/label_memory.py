from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from finance_context.mapping.normalize import memory_section, normalize_label, section_class
from finance_context.store.fs import update_json


@dataclass(frozen=True)
class LabelHit:
    concept_id: str
    score: float
    source: str


MemoryKey = tuple[str, str, str]


LegacyKey = tuple[str, str]


_MEMORY_UNITS = frozenset({"money", "rate", "years"})


_LEARN_SOURCES = {"glossary", "rule", "structure", "lexical", "embed"}


def load_label_memory(path: Path | None) -> dict[MemoryKey, LabelHit]:
    triples, _legacy = _read_label_memory(path)
    return triples


def save_label_memory(
    path: Path,
    updates: dict[MemoryKey, LabelHit],
    drop: set[MemoryKey | LegacyKey] | None = None,
) -> None:
    """Keep the higher score. An equal score does not replace another writer.

    `drop` removes a stored triple or a legacy label/parent pair. A new triple
    is applied after the drop, so an agreed key replaces a disputed one.
    """

    def mutate(current: dict) -> dict:
        triples, legacy = _split_hits(current)
        for key in drop or ():
            if len(key) == 3:
                triples.pop(key, None)  # type: ignore[arg-type]
            elif len(key) == 2:
                legacy.pop(key, None)  # type: ignore[arg-type]
        for key, hit in updates.items():
            label, section, unit = key
            stored = (normalize_label(label), normalize_label(section), _unit_token(unit))
            if not stored[0] or not hit.concept_id:
                continue
            previous = triples.get(stored)
            if previous is None or hit.score > previous.score:
                triples[stored] = hit
        return _hits_payload(triples, legacy)

    update_json(path, mutate)


def merge_label_hits(
    shared: dict[MemoryKey, LabelHit],
    session: dict[MemoryKey, str],
) -> dict[MemoryKey, LabelHit]:
    """Session pairs without a score count as 1.0. The higher score wins."""
    merged = dict(shared)
    for key, concept_id in session.items():
        legacy = LabelHit(concept_id, 1.0, "glossary")
        current = merged.get(key)
        if current is None or legacy.score > current.score:
            merged[key] = legacy
    return merged


def reconcile_label_memory(
    hits: dict[MemoryKey, LabelHit],
    taxonomy: list,
) -> dict[MemoryKey, LabelHit]:
    """Rewrite a stale concept on a triple without collapsing two units."""
    from finance_context.mapping.glossary import reconcile_glossary

    out: dict[MemoryKey, LabelHit] = {}
    for key, hit in hits.items():
        label, section, _unit = key
        rewritten = reconcile_glossary({(label, section): hit.concept_id}, taxonomy)
        concept_id = rewritten.get((label, section))
        if concept_id:
            out[key] = LabelHit(concept_id, hit.score, hit.source)
    return out


def learned_hits(
    rows: list,
    *,
    embed_score_min: float,
) -> tuple[dict[MemoryKey, LabelHit], set[MemoryKey | LegacyKey]]:
    """Agreeing triples, plus every coarse key this book must not leave behind.

    A second concept or an abstained fact row on the same triple omits it.
    Excluded and abstract rows are not compared. Chat is not stored.
    """
    groups: dict[MemoryKey, list] = {}
    drop: set[MemoryKey | LegacyKey] = set()
    for row in rows:
        if not _compared_row(row):
            continue
        label = normalize_label(getattr(row, "label", None))
        if not label:
            continue
        drop.update(_legacy_keys(row))
        if _banner_label(row):
            continue
        key = _memory_key(row)
        groups.setdefault(key, []).append(row)
    found: dict[MemoryKey, LabelHit] = {}
    for key, grouped in groups.items():
        concepts = {
            str(row.concept_id)
            for row in grouped
            if getattr(row, "concept_id", None) and not _abstained(row)
        }
        if _group_abstained(grouped) or len(concepts) != 1:
            drop.add(key)
            continue
        learnable = [
            hit for row in grouped if (hit := _learnable_hit(row, embed_score_min)) is not None
        ]
        learnable = [hit for hit in learnable if hit.concept_id == next(iter(concepts))]
        if not learnable:
            continue
        best = learnable[0]
        for hit in learnable[1:]:
            if hit.score > best.score:
                best = hit
        found[key] = best
    return found, drop


def _read_label_memory(
    path: Path | None,
) -> tuple[dict[MemoryKey, LabelHit], dict[LegacyKey, LabelHit]]:
    if path is None or not path.is_file():
        return {}, {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, {}
    if not isinstance(raw, dict):
        return {}, {}
    return _split_hits(raw)


def _split_hits(raw: dict) -> tuple[dict[MemoryKey, LabelHit], dict[LegacyKey, LabelHit]]:
    """Entries with section and unit are triples. A parent-only row stays legacy.

    A legacy row is not a triple with an empty unit: that would teach the old
    parent to the next unitless line under the same heading.
    """
    triples: dict[MemoryKey, LabelHit] = {}
    legacy: dict[LegacyKey, LabelHit] = {}
    items = raw.get("entries", [])
    if not isinstance(items, list):
        return triples, legacy
    for item in items:
        if not isinstance(item, dict):
            continue
        label = normalize_label(item.get("label"))
        concept_id = item.get("concept_id")
        if not label or not concept_id:
            continue
        hit = LabelHit(str(concept_id), _score(item.get("score")), str(item.get("source") or ""))
        if "section" in item and "unit" in item:
            key = (
                label,
                normalize_label(item.get("section") or ""),
                _unit_token(item.get("unit")),
            )
            triples[key] = hit
            continue
        legacy[(label, normalize_label(item.get("parent") or ""))] = hit
    return triples, legacy


def _hits_payload(
    triples: dict[MemoryKey, LabelHit],
    legacy: dict[LegacyKey, LabelHit],
) -> dict:
    entries: list[dict] = []
    for (label, section, unit), hit in triples.items():
        entries.append(
            {
                "label": label,
                "section": section,
                "unit": unit,
                "concept_id": hit.concept_id,
                "score": hit.score,
                "source": hit.source,
            }
        )
    for (label, parent), hit in legacy.items():
        entries.append(
            {
                "label": label,
                "parent": parent,
                "concept_id": hit.concept_id,
                "score": hit.score,
                "source": hit.source,
            }
        )
    entries.sort(
        key=lambda item: (
            item["label"],
            item.get("section", item.get("parent", "")),
            item.get("unit", ""),
        )
    )
    return {"entries": entries}


def _memory_key(row: object) -> MemoryKey:
    return (
        normalize_label(getattr(row, "label", None)),
        memory_section(
            list(getattr(row, "section_path", None) or []),
            getattr(row, "parent_label", None),
        ),
        _unit_token(getattr(row, "memory_unit", "")),
    )


def _legacy_keys(row: object) -> set[LegacyKey]:
    label = normalize_label(getattr(row, "label", None))
    parent = normalize_label(getattr(row, "parent_label", None))
    keys = {(label, parent)}
    klass = section_class(getattr(row, "parent_label", None))
    if klass:
        keys.add((label, klass))
    return keys


def _compared_row(row: object) -> bool:
    if getattr(row, "kind", None) in {"abstract", "header"}:
        return False
    return getattr(row, "disposition", None) != "excluded"


def _banner_label(row: object) -> bool:
    raw_label = str(getattr(row, "label", "") or "").strip()
    return raw_label.isupper() and (
        "ASSUMPTION" in raw_label
        or raw_label.startswith("COSTS DURING")
        or raw_label.startswith("PROJECT FINANCING")
    )


def _abstained(row: object) -> bool:
    return getattr(row, "disposition", None) == "abstained" or not getattr(row, "concept_id", None)


def _group_abstained(rows: list) -> bool:
    return any(_abstained(row) for row in rows)


def _learnable_hit(row: object, embed_score_min: float) -> LabelHit | None:
    if _abstained(row):
        return None
    source = getattr(row, "source", None)
    if source not in _LEARN_SOURCES or getattr(row, "confidence", None) != "high":
        return None
    if source == "embed":
        raw = getattr(row, "score", None)
        if raw is None:
            return None
        score = float(raw)
        if score < embed_score_min:
            return None
    else:
        score = 1.0
    return LabelHit(str(row.concept_id), score, str(source))


def _unit_token(unit: object) -> str:
    text = str(unit or "")
    return text if text in _MEMORY_UNITS else ""


def _score(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
