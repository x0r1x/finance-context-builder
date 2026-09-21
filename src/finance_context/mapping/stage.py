from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from finance_context.layout.models import Layout
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.glossary import (
    learn_from_rows,
    load_glossary,
    reconcile_glossary,
    save_glossary,
)
from finance_context.mapping.models import Concept, MappingDocument
from finance_context.mapping.taxonomy import load_taxonomy
from finance_context.observability import log_event
from finance_context.ports.protocols import ChatPort, EmbedPort, SlotGate
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
    slot_timeout_sec: float = 120.0,
    embedding_model: str = "",
    glossary_path: Path | None = None,
) -> MappingDocument:
    path = dest_dir / "mapping.json"
    if path.exists():
        log_event(_LOGGER, logging.INFO, "stage_skip", "artifact exists", stage="mapping")
        return MappingDocument.model_validate_json(path.read_text(encoding="utf-8"))
    t0 = time.monotonic()
    layout = Layout.model_validate(
        json.loads((dest_dir / "layout.json").read_text(encoding="utf-8"))
    )
    cells: list[dict] = []
    ir_cells = dest_dir / "ir" / "cells.parquet"
    if ir_cells.exists():
        cells = read_parquet(ir_cells)
    edges: list[dict] = []
    ir_cell_edges = dest_dir / "ir" / "cell_edges.parquet"
    ir_edges = dest_dir / "ir" / "edges.parquet"
    if ir_cell_edges.exists():
        edges = read_parquet(ir_cell_edges)
    elif ir_edges.exists():
        edges = read_parquet(ir_edges)
    tax = taxonomy or load_taxonomy()
    merged = dict(load_glossary(glossary_path))
    merged.update(glossary or {})
    merged = reconcile_glossary(merged, tax)
    doc = map_layout(
        layout,
        taxonomy=tax,
        glossary=merged,
        embed=embed,
        chat=chat,
        slots=slots,
        cells=cells,
        slot_timeout_sec=slot_timeout_sec,
        cache_path=cache_path,
        embedding_model=embedding_model,
        edges=edges,
    )
    if glossary_path is not None:
        save_glossary(glossary_path, learn_from_rows(merged, doc.rows))
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
