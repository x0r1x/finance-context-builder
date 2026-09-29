from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from pydantic import ValidationError

from finance_context.errors import PortError
from finance_context.layout.models import Layout
from finance_context.mapping.cascade import ConceptIndex, _acquire, _charge, _release, map_layout
from finance_context.mapping.facets import inherit_facets
from finance_context.mapping.glossary import (
    learn_from_rows,
    learned_hits,
    load_glossary,
    load_label_memory,
    reconcile_glossary,
    reconcile_label_memory,
    save_glossary,
    save_label_memory,
)
from finance_context.mapping.induce import concept_id_for_label, should_mint
from finance_context.mapping.models import (
    Concept,
    ExtensionDecision,
    Facets,
    MappedRow,
    MappingDocument,
)
from finance_context.mapping.taxonomy import TaxonomyDocument, load_taxonomy, remember_concept
from finance_context.observability import log_event
from finance_context.ports.protocols import ChatPort, EmbedPort, SlotGate
from finance_context.settings import _DEFAULT_LLM_CONCURRENCY, _DEFAULT_LLM_SLOT_WAIT_SEC
from finance_context.store.fs import read_parquet, write_json

_LOGGER = logging.getLogger("finance_context.mapping")


def mapping_workbook(
    dest_dir: Path,
    *,
    embed: EmbedPort | None = None,
    chat: ChatPort | None = None,
    slots: SlotGate | None = None,
    glossary: dict[tuple[str, str], str] | None = None,
    taxonomy: list[Concept] | None = None,
    cache_path: Path | None = None,
    slot_timeout_sec: float = _DEFAULT_LLM_SLOT_WAIT_SEC,
    embedding_model: str = "",
    glossary_path: Path | None = None,
    label_memory_path: Path | None = None,
    runtime_taxonomy_path: Path | None = None,
    concept_index: ConceptIndex | None = None,
    llm_concurrency: int = _DEFAULT_LLM_CONCURRENCY,
    cells: list[dict] | None = None,
    edges: list[dict] | None = None,
    book_dir: Path | None = None,
) -> MappingDocument:
    book = book_dir or dest_dir
    path = dest_dir / "mapping.json"
    if path.exists():
        log_event(_LOGGER, logging.INFO, "stage_skip", "artifact exists", stage="mapping")
        return MappingDocument.model_validate_json(path.read_text(encoding="utf-8"))
    t0 = time.monotonic()
    layout = Layout.model_validate(
        json.loads((book / "layout.json").read_text(encoding="utf-8"))
    )
    if cells is None:
        ir_cells = book / "ir" / "cells.parquet"
        cells = read_parquet(ir_cells) if ir_cells.is_file() else []
    if edges is None:
        ir_cell_edges = book / "ir" / "cell_edges.parquet"
        ir_edges = book / "ir" / "edges.parquet"
        if ir_cell_edges.is_file():
            edges = read_parquet(ir_cell_edges)
        elif ir_edges.is_file():
            edges = read_parquet(ir_edges)
        else:
            edges = []
    tax = taxonomy or load_taxonomy()
    session = dict(load_glossary(glossary_path))
    snapshot = dict(session)
    merged = dict(snapshot)
    merged.update(glossary or {})
    merged = reconcile_glossary(merged, tax)
    memory = {
        key: hit.concept_id
        for key, hit in reconcile_label_memory(
            load_label_memory(label_memory_path), tax
        ).items()
    }
    doc = map_layout(
        layout,
        taxonomy=tax,
        glossary=merged,
        label_memory=memory,
        embed=embed,
        chat=chat,
        slots=slots,
        cells=cells,
        slot_timeout_sec=slot_timeout_sec,
        cache_path=cache_path,
        embedding_model=embedding_model,
        concept_index=concept_index,
        llm_concurrency=llm_concurrency,
        edges=edges,
    )
    if embed is not None and runtime_taxonomy_path is not None:
        _mint_unmatched(
            doc,
            runtime_taxonomy_path,
            tax,
            chat=chat,
            slots=slots,
            slot_timeout_sec=slot_timeout_sec,
        )
    if label_memory_path is not None:
        hits, drop = learned_hits(doc.rows)
        save_label_memory(label_memory_path, hits, drop)
    elif glossary_path is not None:
        learned = learn_from_rows(merged, doc.rows)
        delta = {key: concept for key, concept in learned.items() if key not in snapshot}
        if delta:
            save_glossary(glossary_path, delta)
    write_json(path, doc.model_dump(mode="json"))
    log_event(
        _LOGGER,
        logging.INFO,
        "stage_done",
        "mapping done",
        stage="mapping",
        duration_ms=int((time.monotonic() - t0) * 1000),
    )
    return doc


def _mint_unmatched(
    doc: MappingDocument,
    path: Path,
    taxonomy: list[Concept],
    *,
    chat: ChatPort | None = None,
    slots: SlotGate | None = None,
    slot_timeout_sec: float = _DEFAULT_LLM_SLOT_WAIT_SEC,
) -> None:
    if not path.is_file():
        return
    document = TaxonomyDocument.model_validate_json(path.read_text(encoding="utf-8"))
    by_id = {item.id: item for item in document.concepts}
    seed_ids = {item.id for item in load_taxonomy()}
    for row in doc.rows:
        if row.concept_id is not None or row.disposition != "abstained":
            continue
        best = row.alternatives[0][1] if row.alternatives else None
        if not should_mint(best):
            continue
        anchors = _seed_anchors(row, by_id, document, seed_ids)
        if not anchors:
            continue
        if chat is None:
            nearest = row.alternatives[0][0]
            chosen = next((item for item in anchors if item[0].id == nearest), None)
            sentence = None
        else:
            decision = _ask_extension(chat, row, anchors, slots, slot_timeout_sec)
            if decision is None or decision.action != "extend":
                continue
            chosen = next((item for item in anchors if item[0].id == decision.broader), None)
            sentence = _one_sentence(decision.definition)
        if chosen is None:
            continue
        parent, facets = chosen
        concept_id = concept_id_for_label(row.label, row.parent_label or "", row.sheet)
        if concept_id is None:
            continue
        statements = list(parent.statements)
        if not statements and facets.statement:
            statements = [facets.statement]
        stored = remember_concept(
            path,
            Concept(
                id=concept_id,
                labels=[row.label],
                definition=sentence,
                statements=statements,
                broader=parent.id,
                facets=facets,
            ),
        )
        if stored is None:
            continue
        if not any(item.id == stored.id for item in taxonomy):
            taxonomy.append(stored)
        by_id[stored.id] = stored
        row.concept_id = stored.id
        row.source = "embed"
        row.confidence = "high"
        row.score = 1.0
        row.disposition = "mapped"


def _seed_anchors(
    row: MappedRow,
    by_id: dict[str, Concept],
    document: TaxonomyDocument,
    seed_ids: set[str],
) -> list[tuple[Concept, Facets]]:
    """Seed concepts whose unit agrees with the row. The nearest stays first."""
    anchors: list[tuple[Concept, Facets]] = []
    seen: set[str] = set()
    for concept_id, _score in row.alternatives:
        if concept_id in seen or concept_id not in seed_ids:
            continue
        parent = by_id.get(concept_id)
        if parent is None:
            continue
        facets = inherit_facets(parent, by_id, document.facet_defaults)
        unit = facets.unit or parent.value_kind
        if row.memory_unit and unit and row.memory_unit != unit:
            continue
        seen.add(concept_id)
        anchors.append((parent, facets))
    return anchors


def _ask_extension(
    chat: ChatPort,
    row: MappedRow,
    anchors: list[tuple[Concept, Facets]],
    slots: SlotGate | None,
    slot_timeout_sec: float,
) -> ExtensionDecision | None:
    if not _acquire(slots, "llm", slot_timeout_sec):
        return None
    try:
        if not _charge(slots, "llm"):
            return None
        shown = ", ".join(parent.id for parent, _facets in anchors)
        definitions = "; ".join(
            f"{parent.id}: {parent.definition or ', '.join(parent.labels)}"
            for parent, _facets in anchors
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "Decide whether this unmatched line is narrower than one shown base concept "
                    "of the same unit. Return action extend and broader set to one shown id, "
                    "or action skip. Do not invent an id."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"label: {row.label}\n"
                    f"parent: {row.parent_label or ''}\n"
                    f"section: {' / '.join(row.section_path)}\n"
                    f"unit: {row.memory_unit}\n"
                    f"options: {shown}\n"
                    f"definitions: {definitions}"
                ),
            },
        ]
        return chat.complete_json(ExtensionDecision, messages)
    except (PortError, ValidationError):
        return None
    finally:
        _release(slots, "llm")


def _one_sentence(text: str | None) -> str | None:
    if not text:
        return None
    line = " ".join(str(text).split())
    if not line:
        return None
    for mark in (". ", "? ", "! "):
        if mark in line:
            line = line.split(mark, 1)[0] + mark.strip()
            break
    return line[:240]
