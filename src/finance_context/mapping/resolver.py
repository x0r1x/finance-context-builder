from __future__ import annotations

from collections import defaultdict

from finance_context.mapping.facets import prune_candidates
from finance_context.mapping.knn import COSINE_GAP, COSINE_MIN, TOP_K
from finance_context.mapping.models import (
    Candidate,
    Concept,
    Disposition,
    ExclusionReason,
    MappedRow,
    RowContext,
)
from finance_context.mapping.structure import BookView, Signal

ACCEPT_MIN = 0.82
SOURCE_BY_SIGNAL = {
    "glossary": "glossary",
    "lexical": "rule",
    "structure": "structure",
    "embed": "embed",
    "chat": "chat",
}


class Resolver:
    def __init__(self, taxonomy: list[Concept], *, accept_min: float = ACCEPT_MIN) -> None:
        self.taxonomy = {c.id: c for c in taxonomy}
        self.accept_min = accept_min

    def fuse(
        self,
        ctx: RowContext,
        proposals: list[Candidate],
    ) -> list[Candidate]:
        buckets: dict[str, list[Candidate]] = defaultdict(list)
        for item in proposals:
            if item.concept_id in self.taxonomy:
                buckets[item.concept_id].append(item)
        fused: list[Candidate] = []
        for concept_id, items in buckets.items():
            items.sort(key=lambda c: c.score, reverse=True)
            top = items[0]
            bonus = 0.04 * (len(items) - 1)
            fused.append(
                Candidate(
                    concept_id=concept_id,
                    score=min(1.0, top.score + bonus),
                    signal=top.signal,
                    evidence="; ".join(dict.fromkeys(i.evidence for i in items)),
                )
            )
        fused.sort(key=lambda c: c.score, reverse=True)
        return prune_candidates(fused, ctx, self.taxonomy)

    def decide(
        self, ctx: RowContext, ranked: list[Candidate]
    ) -> tuple[str | None, Candidate | None]:
        ranked = _apply_guards(ctx, ranked, self.taxonomy)
        if not ranked:
            return None, None
        top = ranked[0]
        if top.score < self.accept_min:
            return None, None
        if len(ranked) > 1 and ranked[0].score - ranked[1].score < 0.02:
            preferred = _prefer_specific(ranked, ctx, self.taxonomy)
            if preferred is None:
                return None, None
            top = preferred
        if top.signal == "embed":
            if top.score < COSINE_MIN:
                return None, None
            if len(ranked) > 1 and top.score - ranked[1].score < COSINE_GAP:
                return None, None
        return top.concept_id, top


def collect_proposals(
    ctx: RowContext,
    book: BookView,
    signals: list[Signal],
) -> list[Candidate]:
    out: list[Candidate] = []
    for signal in signals:
        out.extend(signal.propose(ctx, book))
    return out


def to_mapped(
    ctx: RowContext,
    *,
    concept_id: str | None,
    picked: Candidate | None,
    ranked: list[Candidate],
    source: str,
    disposition: Disposition | None = None,
    exclusion_reason: ExclusionReason | None = None,
) -> MappedRow:
    score = picked.score if picked is not None else (ranked[0].score if ranked else None)
    if disposition is None:
        disposition = "mapped" if concept_id else "abstained"
    if exclusion_reason is None and disposition == "abstained":
        exclusion_reason = _abstain_reason(ranked)
    return MappedRow(
        row_key=ctx.row_key,
        sheet=ctx.sheet,
        row=ctx.row,
        block_id=ctx.block_id,
        label=ctx.label,
        parent_label=ctx.parent_label,
        concept_id=concept_id,
        article_role=ctx.article_role,
        source=source,  # type: ignore[arg-type]
        score=score if concept_id else None,
        confidence=_confidence(source, score),
        alternatives=[(c.concept_id, c.score) for c in ranked[:TOP_K]],
        evidence=picked.evidence if picked is not None else None,
        disposition=disposition,
        exclusion_reason=exclusion_reason,
    )


def _abstain_reason(ranked: list[Candidate]) -> ExclusionReason:
    if not ranked:
        return "no_candidate"
    if len(ranked) > 1 and ranked[0].score - ranked[1].score < 0.06:
        return "ambiguous"
    if ranked[0].score < ACCEPT_MIN:
        return "low_score"
    return "facet_mismatch"


def _prefer_specific(
    ranked: list[Candidate],
    ctx: RowContext,
    taxonomy: dict[str, Concept],
) -> Candidate | None:
    close = [c for c in ranked if ranked[0].score - c.score < 0.02]
    if len(close) < 2:
        return ranked[0]
    leaves = [c for c in close if (taxonomy.get(c.concept_id) and taxonomy[c.concept_id].broader)]
    if len(leaves) == 1:
        return leaves[0]
    return None


def _confidence(source: str, score: float | None) -> str | None:
    if source in {"glossary", "rule", "structure", "lexical"}:
        return "high"
    if source == "embed":
        return "high" if score is not None and score >= 0.85 else "medium"
    if source == "chat":
        return "medium"
    return "low"


def _apply_guards(
    ctx: RowContext, ranked: list[Candidate], taxonomy: dict[str, Concept]
) -> list[Candidate]:
    from finance_context.mapping.normalize import normalize_label

    n = normalize_label(ctx.label)
    if not n:
        return ranked
    forced_id: str | None = None
    for concept in taxonomy.values():
        exact = {normalize_label(label) for label in concept.exact_labels if label}
        if n in exact:
            if forced_id and forced_id != concept.id:
                return ranked
            forced_id = concept.id
    if not forced_id:
        return ranked
    forced = [c for c in ranked if c.concept_id == forced_id]
    if forced:
        return forced
    if forced_id in taxonomy:
        return [
            Candidate(
                concept_id=forced_id,
                score=1.0,
                signal="lexical",
                evidence="exact_labels",
            )
        ]
    return ranked
