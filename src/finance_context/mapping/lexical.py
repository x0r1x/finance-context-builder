from __future__ import annotations

from finance_context.mapping.models import Candidate, Concept, RowContext
from finance_context.mapping.normalize import normalize_label
from finance_context.mapping.structure import BookView, section_tokens


class LexicalSignal:
    name = "lexical"

    def __init__(self, taxonomy: list[Concept]) -> None:
        self.concepts = {c.id: c for c in taxonomy}
        self.phrases: list[tuple[str, str, int]] = []
        for concept in taxonomy:
            for label in [*concept.labels, *concept.aliases]:
                n = normalize_label(label)
                if n:
                    self.phrases.append((n, concept.id, len(n.split())))

    def propose(self, ctx: RowContext, book: BookView) -> list[Candidate]:
        n = normalize_label(ctx.label)
        if not n:
            return []
        hits: dict[str, Candidate] = {}
        tokens = set(n.split())
        extra = section_tokens(ctx)
        if "cf.net" in book.taxonomy and (
            "cumulative net" in n
            or n in {"net cf", "net cashflow", "net cash flow"}
            or ({"net", "flow"} <= tokens and "present" not in n and "financing" not in n)
        ):
            hits["cf.net"] = Candidate(
                concept_id="cf.net",
                score=0.93,
                signal=self.name,
                evidence="net flow phrasing",
            )
        if "debt" in extra and (
            {"opening", "balance"} <= tokens or {"closing", "balance"} <= tokens
        ):
            if "bs.debt" in book.taxonomy:
                hits["bs.debt"] = Candidate(
                    concept_id="bs.debt",
                    score=0.91,
                    signal=self.name,
                    evidence="debt opening/closing balance",
                )
        for phrase, concept_id, size in self.phrases:
            concept = self.concepts.get(concept_id)
            if concept is None or _blocked_by_anti(n, concept):
                continue
            if concept.section_hints and not _hint_hit(ctx, extra, concept.section_hints):
                continue
            score = _phrase_score(n, tokens, phrase, size)
            if score is None:
                continue
            if concept_id == "bs.ap" and "debt" in extra:
                continue
            prev = hits.get(concept_id)
            if prev is None or score > prev.score:
                hits[concept_id] = Candidate(
                    concept_id=concept_id,
                    score=score,
                    signal=self.name,
                    evidence=f"label matches {phrase!r}",
                )
        return list(hits.values())


def _blocked_by_anti(label: str, concept: Concept) -> bool:
    return any(
        normalize_label(anti) and normalize_label(anti) in label for anti in concept.anti_labels
    )


def _hint_hit(ctx: RowContext, extra: set[str], hints: list[str]) -> bool:
    blob = " ".join([ctx.parent_label or "", *ctx.section_path, ctx.sheet, ctx.label])
    nblob = normalize_label(blob)
    for hint in hints:
        nh = normalize_label(hint)
        if not nh:
            continue
        if nh in nblob or set(nh.split()) <= extra:
            return True
    return False


def _phrase_score(label: str, tokens: set[str], phrase: str, size: int) -> float | None:
    if label == phrase:
        return 1.0
    parts = phrase.split()
    if size >= 2 and phrase in label:
        return 0.94
    if size == 1:
        if phrase in {
            "net",
            "cash",
            "total",
            "opening",
            "closing",
            "balance",
            "flow",
            "debt",
            "revenue",
            "headroom",
        }:
            return None
        if phrase in tokens:
            return 0.9
        return None
    if set(parts) <= tokens:
        return 0.92
    return None
