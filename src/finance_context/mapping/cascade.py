from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from finance_context.errors import PortError
from finance_context.excel.a1 import format_addr
from finance_context.layout.models import Block, Layout, LayoutRow
from finance_context.mapping.knn import TOP_K, confident_match, rank_concepts
from finance_context.mapping.models import (
    Concept,
    ConceptPick,
    MappedRow,
    MappingDocument,
    MappingQuestion,
    MapSource,
)
from finance_context.mapping.normalize import normalize_label
from finance_context.mapping.roles import article_role
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
) -> MappingDocument:
    concept_ids = {c.id for c in taxonomy}
    glossary_n = {
        (normalize_label(label), normalize_label(parent)): concept_id
        for (label, parent), concept_id in glossary.items()
    }
    templates = _templates_by_row(cells or [])
    pending = _collect_rows(layout, glossary_n, concept_ids, templates)

    need_knn = [row for row in pending if row.concept_id is None]
    if need_knn and embed is not None:
        if _acquire(slots, "embed", slot_timeout_sec):
            try:
                index = load_concept_vectors(
                    embed,
                    taxonomy,
                    cache_path=cache_path,
                    model=embedding_model,
                )
                if index and _charge(slots, "embed"):
                    queries = embed.embed([row.label for row in need_knn])
                    for row, vec in zip(need_knn, queries, strict=True):
                        row.ranked = rank_concepts(vec, index)
                        picked = confident_match(row.ranked)
                        picked = _guard(row.label, picked, concept_ids)
                        if picked:
                            row.concept_id = picked
                            row.source = "embed"
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
        else:
            log_event(
                _LOGGER,
                logging.WARNING,
                "port_fallback",
                "embed fallback",
                port="embed",
                reason="slot_timeout",
            )

    need_chat = [row for row in pending if row.concept_id is None]
    if need_chat and chat is not None:
        if _acquire(slots, "llm", slot_timeout_sec):
            try:
                for row in need_chat:
                    if not _charge(slots, "llm"):
                        break
                    picked = _ask_chat(chat, row, taxonomy)
                    picked = _guard(row.label, picked, concept_ids)
                    if picked:
                        row.concept_id = picked
                        row.source = "chat"
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
        else:
            log_event(
                _LOGGER,
                logging.WARNING,
                "port_fallback",
                "chat fallback",
                port="chat",
                reason="slot_timeout",
            )

    questions: list[MappingQuestion] = []
    mapped: list[MappedRow] = []
    qn = 1
    for row in pending:
        if row.concept_id is None:
            row.source = "question"
            questions.append(_question(row, taxonomy, qn))
            qn += 1
        mapped.append(row.to_mapped())
    return MappingDocument(rows=mapped, questions=questions)


class _Row:
    def __init__(
        self,
        *,
        sheet: str,
        block: Block,
        layout_row: LayoutRow,
        parent_label: str | None,
        concept_id: str | None,
        source: MapSource,
        article: str,
    ) -> None:
        self.sheet = sheet
        self.block = block
        self.layout_row = layout_row
        self.parent_label = parent_label
        self.concept_id = concept_id
        self.source = source
        self.article = article
        self.ranked: list[tuple[str, float]] = []
        self.label = layout_row.label

    def to_mapped(self) -> MappedRow:
        row = self.layout_row
        score = self.ranked[0][1] if self.ranked else None
        confidence = _confidence_for(self.source, score)
        return MappedRow(
            row_key=f"{self.sheet}|{row.row}|{self.block.block_id}",
            sheet=self.sheet,
            row=row.row,
            block_id=self.block.block_id,
            label=row.label,
            parent_label=self.parent_label,
            concept_id=self.concept_id,
            article_role=self.article,  # type: ignore[arg-type]
            source=self.source,
            score=score if self.source in {"embed", "chat"} else (1.0 if self.concept_id else None),
            confidence=confidence,
            alternatives=self.ranked[:5],
        )


def _collect_rows(
    layout: Layout,
    glossary: dict[tuple[str, str], str],
    concept_ids: set[str],
    templates: dict[tuple[str, int], list[str | None]],
) -> list[_Row]:
    pending: list[_Row] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            by_row = {r.row: r for r in block.rows}
            for layout_row in block.rows:
                parent = None
                if layout_row.parent_row and layout_row.parent_row in by_row:
                    parent = by_row[layout_row.parent_row].label
                key = (normalize_label(layout_row.label), normalize_label(parent))
                concept_id = glossary.get(key)
                source: MapSource = "glossary"
                if concept_id:
                    guarded = _guard(layout_row.label, concept_id, concept_ids)
                    if guarded != concept_id:
                        concept_id = guarded
                        source = "rule"
                    elif guarded is None:
                        concept_id = None
                else:
                    concept_id = _guard(layout_row.label, None, concept_ids)
                    source = "rule" if concept_id else "question"
                role = article_role(
                    layout_row, templates.get((sheet.name, layout_row.row), [])
                )
                pending.append(
                    _Row(
                        sheet=sheet.name,
                        block=block,
                        layout_row=layout_row,
                        parent_label=parent,
                        concept_id=concept_id,
                        source=source if concept_id else "question",
                        article=role,
                    )
                )
    return pending


def _guard(label: str, concept_id: str | None, concept_ids: set[str]) -> str | None:
    if normalize_label(label) == "gmv" and concept_id == "pnl.revenue":
        return "pnl.gmv" if "pnl.gmv" in concept_ids else None
    if normalize_label(label) == "gmv" and concept_id is None and "pnl.gmv" in concept_ids:
        return "pnl.gmv"
    return concept_id


def _templates_by_row(cells: list[dict]) -> dict[tuple[str, int], list[str | None]]:
    out: dict[tuple[str, int], list[str | None]] = {}
    for cell in cells:
        key = (str(cell["sheet"]), int(cell["row"]))
        out.setdefault(key, []).append(cell.get("formula_template"))
    return out


def _ask_chat(chat: ChatPort, row: _Row, taxonomy: list[Concept]) -> str | None:
    options = [cid for cid, _ in row.ranked[:TOP_K]]
    if not options:
        options = [c.id for c in taxonomy[:TOP_K]]
    parent = row.parent_label or ""
    headers = ", ".join(h.text for h in row.block.axis.headers[:12])
    messages = [
        {
            "role": "system",
            "content": (
                "Map the financial statement line to one concept_id from the list. "
                "Use labels, parent rows, and nearby period headers only. "
                "Never use numeric cell values and never invent a concept_id."
            ),
        },
        {
            "role": "user",
            "content": (
                f"sheet: {row.sheet}\n"
                f"label: {row.label}\n"
                f"parent: {parent}\n"
                f"period_headers: {headers}\n"
                f"options: {', '.join(options)}"
            ),
        },
    ]
    picked = chat.complete_json(ConceptPick, messages)
    concept_id = getattr(picked, "concept_id", None)
    allowed = {c.id for c in taxonomy}
    if concept_id in allowed:
        return str(concept_id)
    return None


def _question(row: _Row, taxonomy: list[Concept], qn: int) -> MappingQuestion:
    options = [cid for cid, _ in row.ranked[:TOP_K]]
    if not options:
        options = [c.id for c in taxonomy[:TOP_K]]
    if "unknown" not in options:
        options = [*options, "unknown"]
    addr = format_addr(row.block.label_col, row.layout_row.row)
    hint = options[0] if options else "unknown"
    return MappingQuestion(
        id=f"q_{qn:03d}",
        prompt=f"Строка «{row.label}» — это {hint}?",
        cell_refs=[f"{row.sheet}!{addr}"],
        options=options,
    )


def _confidence_for(source: MapSource, score: float | None) -> str | None:
    if source in {"glossary", "rule"}:
        return "high"
    if source == "embed":
        return "high" if score is not None and score >= 0.85 else "medium"
    if source == "chat":
        return "medium"
    return "low"


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
