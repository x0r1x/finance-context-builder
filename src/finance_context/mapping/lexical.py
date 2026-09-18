from __future__ import annotations

from finance_context.mapping.models import Candidate, Concept, LexicalPattern, RowContext
from finance_context.mapping.normalize import normalize_label
from finance_context.mapping.patterns import pattern_matches
from finance_context.mapping.structure import BookView, section_tokens


class LexicalSignal:
    name = "lexical"

    def __init__(
        self,
        taxonomy: list[Concept],
        patterns: list[LexicalPattern] | None = None,
    ) -> None:
        self.concepts = {c.id: c for c in taxonomy}
        self.patterns = list(patterns or [])
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
        tokens = _expand_tokens(n.split())
        extra = section_tokens(ctx)
        skipped = {
            pattern.skip_concept
            for pattern in self.patterns
            if pattern.skip_concept
            and pattern_matches(pattern.when, label=n, tokens=tokens, section=extra)
        }
        for pattern in self.patterns:
            if not pattern.concept or pattern.concept not in book.taxonomy:
                continue
            if pattern.concept in skipped:
                continue
            if not pattern_matches(pattern.when, label=n, tokens=tokens, section=extra):
                continue
            hits[pattern.concept] = Candidate(
                concept_id=pattern.concept,
                score=pattern.score,
                signal=self.name,
                evidence=pattern.evidence,
            )
        for phrase, concept_id, size in self.phrases:
            concept = self.concepts.get(concept_id)
            if concept is None or concept_id in skipped or _blocked_by_anti(n, concept, ctx.label):
                continue
            if concept.section_hints and not _hint_hit(ctx, extra, concept.section_hints):
                continue
            score = _phrase_score(n, tokens, phrase, size)
            if score is None:
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


def _blocked_by_anti(label: str, concept: Concept, raw: str | None = None) -> bool:
    raw_fold = (raw or "").casefold()
    for anti in concept.anti_labels:
        if "/" in str(anti):
            compact = str(anti).replace(" ", "").casefold()
            if compact and compact in raw_fold.replace(" ", ""):
                return True
            continue
        n_anti = normalize_label(anti)
        if n_anti and n_anti in label:
            return True
    return False


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


def _expand_tokens(parts: list[str]) -> set[str]:
    tokens = set(parts)
    extra: set[str] = set()
    for token in tokens:
        stem = _singular(token)
        if stem != token:
            extra.add(stem)
    return tokens | extra


def _singular(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


_WEAK_SINGLETONS = {
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
}
_CASH_FLOW_TOKENS = {"flow", "in", "out", "inflow", "outflow", "total"}
_DEBT_FEE_TOKENS = {"fee", "upfront", "up-front"}


def _phrase_score(label: str, tokens: set[str], phrase: str, size: int) -> float | None:
    if label == phrase:
        return 1.0
    parts = phrase.split()
    if size >= 2 and phrase in label:
        return 0.94
    if size == 1:
        if phrase in _WEAK_SINGLETONS:
            if tokens <= {phrase, _singular(phrase), f"{phrase}s"}:
                return None
            if phrase == "cash" and tokens & _CASH_FLOW_TOKENS:
                if tokens & {"hand", "hands", "balance"}:
                    return 0.9
                return None
            if phrase == "debt" and tokens & _DEBT_FEE_TOKENS:
                return None
            if phrase in tokens:
                return 0.88
            return None
        if phrase in tokens:
            return 0.9
        return None
    if set(parts) <= tokens:
        return 0.92
    return None
