from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from finance_context.errors import PortError
from finance_context.excel.a1 import format_addr
from finance_context.layout.models import Layout, LayoutRow
from finance_context.mapping.exclusion import exclusion_reason
from finance_context.mapping.facets import prune_candidates
from finance_context.mapping.glossary import GlossarySignal
from finance_context.mapping.knn import TOP_K, rank_concepts
from finance_context.mapping.lexical import LexicalSignal
from finance_context.mapping.models import (
    Calculation,
    Candidate,
    Concept,
    ConceptPick,
    LexicalPattern,
    MappingDocument,
    MappingQuestion,
    RowContext,
)
from finance_context.mapping.resolver import (
    SOURCE_BY_SIGNAL,
    Resolver,
    collect_proposals,
    to_mapped,
)
from finance_context.mapping.rules import is_noise_label
from finance_context.mapping.structure import (
    BookView,
    StructureSignal,
    analyze_structure,
    build_row_context,
)
from finance_context.mapping.taxonomy import attached_document, implicit_calculations
from finance_context.mapping.vectors import load_concept_vectors
from finance_context.observability import log_event
from finance_context.ports.protocols import ChatPort, EmbedPort, SlotGate

_LOGGER = logging.getLogger(__name__)


def map_layout(
    layout: Layout,
    *,
    taxonomy: list[Concept],
    glossary: dict[tuple[str, str], str],
    embed: EmbedPort | None = None,
    chat: ChatPort | None = None,
    slots: SlotGate | None = None,
    cells: list[dict] | None = None,
    slot_timeout_sec: float = 0.0,
    cache_path: Path | None = None,
    embedding_model: str = "",
    patterns: list[LexicalPattern] | None = None,
    calculations: list[Calculation] | None = None,
) -> MappingDocument:
    attached = attached_document(taxonomy)
    merged_calcs = list(calculations or (attached.calculations if attached else []))
    merged_calcs.extend(implicit_calculations(taxonomy))
    merged_patterns = list(patterns or (attached.patterns if attached else []))
    book = BookView(
        layout,
        cells or [],
        taxonomy,
        calculations=merged_calcs,
        patterns=merged_patterns,
    )
    analyze_structure(book)
    templates = _templates_by_row(cells or [])
    resolver = Resolver(taxonomy)
    pending = _collect_contexts(book, templates)
    signals = [
        GlossarySignal(glossary),
        LexicalSignal(taxonomy, merged_patterns),
        StructureSignal(),
    ]

    for _ in range(4):
        progressed = False
        for ctx in pending:
            if exclusion_reason(ctx):
                continue
            before = ctx.row_key in book.concepts
            _resolve_row(ctx, book, signals, resolver)
            if not before and ctx.row_key in book.concepts:
                progressed = True
        if not progressed:
            break

    need_knn = [
        ctx for ctx in pending if ctx.row_key not in book.concepts and not exclusion_reason(ctx)
    ]
    index: dict[str, list[float]] = {}
    if need_knn and embed is not None:
        index = _embed_pass(
            need_knn,
            book,
            taxonomy,
            embed,
            slots,
            slot_timeout_sec,
            cache_path,
            embedding_model,
            resolver,
            signals,
        )

    need_chat = [
        ctx for ctx in pending if ctx.row_key not in book.concepts and not exclusion_reason(ctx)
    ]
    if need_chat and chat is not None:
        _chat_pass(need_chat, book, taxonomy, chat, slots, slot_timeout_sec, resolver, index)
    for ctx in pending:
        if ctx.row_key not in book.concepts and not exclusion_reason(ctx):
            _resolve_row(ctx, book, [StructureSignal()], resolver)
    _apply_calculation_checks(pending, book)

    questions: list[MappingQuestion] = []
    mapped = []
    qn = 1
    for ctx in pending:
        concept_id = book.concepts.get(ctx.row_key)
        ranked = ctx.extras.get("ranked") or []
        picked = ctx.extras.get("picked")
        source = ctx.extras.get("source") or "question"
        reason = exclusion_reason(ctx)
        conflict = ctx.extras.get("exclusion_reason")
        if reason:
            mapped.append(
                to_mapped(
                    ctx,
                    concept_id=None,
                    picked=None,
                    ranked=[],
                    source="rule",
                    disposition="excluded",
                    exclusion_reason=reason,
                )
            )
            continue
        if concept_id is None:
            source = "question"
            questions.append(_question(ctx, ranked, qn))
            qn += 1
        mapped.append(
            to_mapped(
                ctx,
                concept_id=concept_id,
                picked=picked,
                ranked=ranked,
                source=source,
                exclusion_reason=conflict if concept_id is None else None,
            )
        )
    return MappingDocument(rows=mapped, questions=questions, relations=book.relations)


class _Pending:
    def __init__(self, ctx: RowContext) -> None:
        self.ctx = ctx
        self.row_key = ctx.row_key
        self.extras: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.ctx, name)


def _collect_contexts(
    book: BookView, templates: dict[tuple[str, int], list[str | None]]
) -> list[_Pending]:
    pending: list[_Pending] = []
    for sheet in book.layout.sheets:
        for block in sheet.blocks:
            by_row = {r.row: r for r in block.rows}
            for layout_row in block.rows:
                if layout_row.kind != "fact":
                    continue
                if is_noise_label(layout_row.label):
                    continue
                parent = _parent_label(layout_row, by_row)
                ctx = build_row_context(
                    book,
                    sheet.name,
                    block,
                    layout_row,
                    parent,
                    templates.get((sheet.name, layout_row.row), []),
                )
                pending.append(_Pending(ctx))
    return pending


def _parent_label(layout_row: LayoutRow, by_row: dict[int, LayoutRow]) -> str | None:
    if layout_row.parent_row and layout_row.parent_row in by_row:
        return by_row[layout_row.parent_row].label
    if layout_row.section_path:
        return layout_row.section_path[-1]
    return None


def _resolve_row(
    pending: _Pending,
    book: BookView,
    signals: list,
    resolver: Resolver,
) -> None:
    proposals = collect_proposals(pending.ctx, book, signals)
    ranked = resolver.fuse(pending.ctx, proposals)
    concept_id, picked = resolver.decide(pending.ctx, ranked)
    pending.extras["ranked"] = ranked
    if concept_id and picked:
        book.concepts[pending.row_key] = concept_id
        pending.extras["picked"] = picked
        pending.extras["source"] = SOURCE_BY_SIGNAL.get(picked.signal, picked.signal)


def _embed_pass(
    need_knn: list[_Pending],
    book: BookView,
    taxonomy: list[Concept],
    embed: EmbedPort,
    slots: SlotGate | None,
    slot_timeout_sec: float,
    cache_path: Path | None,
    embedding_model: str,
    resolver: Resolver,
    signals: list,
) -> dict[str, list[float]]:
    index: dict[str, list[float]] = {}
    if not _acquire(slots, "embed", slot_timeout_sec):
        log_event(
            _LOGGER,
            logging.WARNING,
            "port_fallback",
            "embed fallback",
            port="embed",
            reason="slot_timeout",
        )
        return index
    try:
        index = load_concept_vectors(
            embed,
            taxonomy,
            cache_path=cache_path,
            model=embedding_model,
        )
        if not index or not _charge(slots, "embed"):
            return index
        queries = embed.embed([row.ctx.query_text or row.ctx.label for row in need_knn])
        for row, vec in zip(need_knn, queries, strict=True):
            scored = rank_concepts(vec, index)
            embed_cands = [
                Candidate(
                    concept_id=cid,
                    score=score,
                    signal="embed",
                    evidence=f"cosine={score:.3f}",
                )
                for cid, score in scored[:TOP_K]
            ]
            proposals = collect_proposals(row.ctx, book, signals) + embed_cands
            ranked = resolver.fuse(row.ctx, proposals)
            row.extras["ranked"] = ranked
            concept_id, picked = resolver.decide(row.ctx, ranked)
            if concept_id and picked:
                book.concepts[row.row_key] = concept_id
                row.extras["picked"] = picked
                row.extras["source"] = SOURCE_BY_SIGNAL.get(picked.signal, picked.signal)
        for row in need_knn:
            if row.row_key not in book.concepts:
                _resolve_row(row, book, signals, resolver)
    except PortError:
        log_event(
            _LOGGER,
            logging.WARNING,
            "port_fallback",
            "embed fallback",
            port="embed",
            reason="port_error",
        )
    finally:
        _release(slots, "embed")
    return index


def _chat_pass(
    need_chat: list[_Pending],
    book: BookView,
    taxonomy: list[Concept],
    chat: ChatPort,
    slots: SlotGate | None,
    slot_timeout_sec: float,
    resolver: Resolver,
    index: dict[str, list[float]],
) -> None:
    if not _acquire(slots, "llm", slot_timeout_sec):
        log_event(
            _LOGGER,
            logging.WARNING,
            "port_fallback",
            "chat fallback",
            port="chat",
            reason="slot_timeout",
        )
        return
    try:
        for row in need_chat:
            if not _charge(slots, "llm"):
                break
            options = prune_candidates(
                list(row.extras.get("ranked") or []),
                row.ctx,
                book.taxonomy,
            )
            picked_id = _ask_chat(chat, row.ctx, taxonomy, options)
            if picked_id and picked_id in book.taxonomy:
                cand = Candidate(
                    concept_id=picked_id,
                    score=0.88,
                    signal="chat",
                    evidence="llm rerank",
                )
                ranked = resolver.fuse(row.ctx, [*(row.extras.get("ranked") or []), cand])
                concept_id, picked = resolver.decide(row.ctx, ranked)
                row.extras["ranked"] = ranked
                if concept_id and picked:
                    book.concepts[row.row_key] = concept_id
                    row.extras["picked"] = picked
                    row.extras["source"] = "chat"
    except PortError:
        log_event(
            _LOGGER,
            logging.WARNING,
            "port_fallback",
            "chat fallback",
            port="chat",
            reason="port_error",
        )
    finally:
        _release(slots, "llm")


def _templates_by_row(cells: list[dict]) -> dict[tuple[str, int], list[str | None]]:
    out: dict[tuple[str, int], list[str | None]] = {}
    for cell in cells:
        key = (str(cell["sheet"]), int(cell["row"]))
        out.setdefault(key, []).append(cell.get("formula_template"))
    return out


def _ask_chat(
    chat: ChatPort,
    ctx: RowContext,
    taxonomy: list[Concept],
    options: list[Candidate],
) -> str | None:
    allowed = {c.id for c in taxonomy}
    by_id = {c.id: c for c in taxonomy}
    choices = [
        c.concept_id
        for c in options
        if c.concept_id in allowed and c.score >= 0.2
    ][:TOP_K]
    defs = []
    for cid in choices:
        concept = by_id.get(cid)
        if concept is None:
            continue
        defs.append(f"{cid}: {concept.definition or ', '.join(concept.labels)}")
    listed = ", ".join([*choices, "unknown"]) if choices else "unknown"
    messages = [
        {
            "role": "system",
            "content": (
                "Map the financial statement line to one concept_id from the list. "
                "If none of the options fit, return unknown. "
                "Use labels, parent rows, section path, and nearby period headers only. "
                "Never use numeric cell values and never invent a concept_id."
            ),
        },
        {
            "role": "user",
            "content": (
                f"sheet: {ctx.sheet}\n"
                f"label: {ctx.label}\n"
                f"parent: {ctx.parent_label or ''}\n"
                f"section: {' / '.join(ctx.section_path)}\n"
                f"value_kind: {ctx.value_kind}\n"
                f"facets: {ctx.inferred_facets.model_dump()}\n"
                f"period_headers: {', '.join(ctx.period_headers)}\n"
                f"options: {listed}\n"
                f"definitions: {'; '.join(defs)}"
            ),
        },
    ]
    picked = chat.complete_json(ConceptPick, messages)
    concept_id = getattr(picked, "concept_id", None)
    if concept_id in {"unknown", None} or concept_id not in allowed:
        return None
    if choices and concept_id not in choices:
        return None
    return str(concept_id)


def _apply_calculation_checks(pending: list[_Pending], book: BookView) -> None:
    for row in pending:
        concept_id = book.concepts.get(row.row_key)
        pattern = book.patterns.get(row.row_key)
        if not concept_id or pattern is None:
            continue
        observed: list[str] = []
        if pattern.kind == "aggregate":
            for row_n in pattern.aggregate_rows:
                found = _concept_at_row(book, row.sheet, row_n)
                if found:
                    observed.append(found)
            if len(observed) < 2:
                continue
        elif pattern.kind == "diff" and pattern.diff_rows:
            for row_n in pattern.diff_rows:
                found = _concept_at_row(book, row.sheet, row_n)
                if found:
                    observed.append(found)
            if len(observed) != 2:
                continue
        else:
            continue
        if _calculation_compatible(concept_id, pattern.kind, observed, book):
            picked = row.extras.get("picked")
            if picked is not None:
                picked.score = min(1.0, picked.score + 0.02)
            continue
        book.concepts.pop(row.row_key, None)
        row.extras["picked"] = None
        row.extras["source"] = "question"
        row.extras["exclusion_reason"] = "calculation_conflict"


def _concept_at_row(book: BookView, sheet: str, row: int) -> str | None:
    found = book.row_index.get((sheet, row))
    if found is None:
        return None
    block, _layout_row = found
    return book.concepts.get(book.row_key(sheet, row, block.block_id))


def _calculation_compatible(
    concept_id: str,
    kind: str,
    observed: list[str],
    book: BookView,
) -> bool:
    relevant = [
        calc
        for calc in book.calculations
        if calc.parent == concept_id and calc.origin == "declared"
    ]
    if not relevant:
        return True
    observed_set = set(observed)
    if kind == "aggregate":
        sums = [calc for calc in relevant if all(term.weight > 0 for term in calc.terms)]
        diffs = [calc for calc in relevant if any(term.weight < 0 for term in calc.terms)]
        if sums:
            return any(observed_set <= {term.concept for term in calc.terms} for calc in sums)
        if diffs:
            return False
        return True
    if kind == "diff":
        for calc in relevant:
            terms = {term.concept for term in calc.terms}
            if any(term.weight < 0 for term in calc.terms) and observed_set == terms:
                return True
        return False
    return True


def _question(ctx: RowContext, ranked: list[Candidate], qn: int) -> MappingQuestion:
    options = [c.concept_id for c in ranked[:TOP_K]]
    if "unknown" not in options:
        options = [*options, "unknown"]
    addr = format_addr(ctx.label_col, ctx.row)
    hint = options[0] if options else "unknown"
    return MappingQuestion(
        id=f"q_{qn:03d}",
        prompt=f"Строка «{ctx.label}» — это {hint}?",
        cell_refs=[f"{ctx.sheet}!{addr}"],
        options=options,
    )


def _acquire(slots: SlotGate | None, kind: Any, timeout_sec: float) -> bool:
    if slots is None:
        return True
    return slots.acquire(kind, timeout_sec)


def _release(slots: SlotGate | None, kind: Any) -> None:
    if slots is None:
        return
    slots.release(kind)


def _charge(slots: SlotGate | None, kind: Any) -> bool:
    if slots is None:
        return True
    charge = getattr(slots, "charge", None)
    if charge is None:
        return True
    return bool(charge(kind))
