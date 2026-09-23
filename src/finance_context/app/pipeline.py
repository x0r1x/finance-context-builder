from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from finance_context.adapters.slots import AlwaysGrant
from finance_context.context.build import build_context
from finance_context.errors import ContextError, PortError
from finance_context.excel.stage import parse_workbook
from finance_context.formulas.stage import compile_workbook, ir_is_current
from finance_context.graph.stage import build_formula_graph
from finance_context.layout.models import Layout
from finance_context.layout.stage import layout_workbook
from finance_context.mapping.models import MappingDocument
from finance_context.mapping.stage import mapping_workbook
from finance_context.mapping.taxonomy import load_taxonomy
from finance_context.mapping.vectors import TaxonomyPrefetch
from finance_context.models.context import ArtifactMeta, ContextDocument, GraphPointer
from finance_context.observability import configure_logging, job_id_var, log_event, stage_var
from finance_context.ports.protocols import ChatPort, EmbedPort, SlotGate
from finance_context.render.markdown import render_markdown
from finance_context.settings import Settings
from finance_context.store.fs import file_lock, read_parquet, write_json

_LOGGER = logging.getLogger("finance_context.pipeline")


class Pipeline:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        embed: EmbedPort | None = None,
        chat: ChatPort | None = None,
        slots: SlotGate | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.embed = embed if embed is not None else self.settings.embed()
        self.chat = chat if chat is not None else self.settings.chat()
        self.slots = slots or AlwaysGrant()

    def run(
        self,
        dest_dir: Path,
        *,
        job_id: str,
        source_filename: str | None = None,
        content_sha256: str | None = None,
        progress: object | None = None,
    ) -> ContextDocument:
        token_job = job_id_var.set(job_id)
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            with file_lock(dest_dir / ".lock"):
                return self._run(
                    dest_dir,
                    job_id=job_id,
                    source_filename=source_filename,
                    content_sha256=content_sha256,
                    progress=progress,
                )
        finally:
            job_id_var.reset(token_job)

    def _run(
        self,
        dest_dir: Path,
        *,
        job_id: str,
        source_filename: str | None,
        content_sha256: str | None,
        progress: object | None,
    ) -> ContextDocument:
        dest_dir.mkdir(parents=True, exist_ok=True)
        context_path = dest_dir / "context.json"
        markdown_path = dest_dir / "context.md"
        if context_path.is_file() and markdown_path.is_file():
            return ContextDocument.model_validate_json(context_path.read_text(encoding="utf-8"))
        source = dest_dir / "source.xlsx"
        if not source.is_file():
            raise ContextError("empty_file", "source.xlsx missing")
        prefetch = _start_taxonomy_prefetch(self)
        try:
            return self._run_loaded(
                dest_dir,
                job_id=job_id,
                source_filename=source_filename,
                content_sha256=content_sha256,
                progress=progress,
                prefetch=prefetch,
            )
        finally:
            if prefetch is not None:
                prefetch.join()

    def _run_loaded(
        self,
        dest_dir: Path,
        *,
        job_id: str,
        source_filename: str | None,
        content_sha256: str | None,
        progress: object | None,
        prefetch: TaxonomyPrefetch | None,
    ) -> ContextDocument:
        source = dest_dir / "source.xlsx"
        owner = _load_owner(dest_dir)
        source_filename = source_filename or owner.get("source_filename")
        content_sha256 = content_sha256 or owner.get("content_sha256")

        def set_stage(stage: str, status: str = "running") -> None:
            _raise_if_cancelled(progress)
            _write_meta(
                dest_dir,
                job_id=job_id,
                status=status,
                stage=stage,
                source_filename=source_filename,
                content_sha256=content_sha256,
            )
            if progress is not None:
                fn = getattr(progress, "progress", None)
                if callable(fn):
                    fn(stage)

        set_stage("parse")
        if not (dest_dir / "raw" / "workbook.json").is_file():
            _timed("parse", lambda: parse_workbook(source, dest_dir))
        set_stage("compile")
        if not ir_is_current(dest_dir):
            compiled = _timed("compile", lambda: compile_workbook(dest_dir))
            ir_cells = compiled.cells
            ir_edges = compiled.edges
            ir_cell_edges = compiled.cell_edges
        else:
            ir_cells, ir_edges, ir_cell_edges = _read_ir(dest_dir)
        set_stage("layout")
        if not (dest_dir / "layout.json").is_file():
            layout = _timed(
                "layout",
                lambda: layout_workbook(dest_dir, cells=ir_cells, edges=ir_edges),
            )
        else:
            layout = Layout.model_validate_json(
                (dest_dir / "layout.json").read_text(encoding="utf-8")
            )
        set_stage("mapping")
        mapping = _timed(
            "mapping",
            lambda: mapping_workbook(
                dest_dir,
                embed=self.embed,
                chat=self.chat,
                slots=self.slots,
                cache_path=self.settings.data_dir / "taxonomy_embeddings.npz",
                slot_timeout_sec=self.settings.llm_slot_wait_sec,
                embedding_model=self.settings.embedding_model or "",
                glossary_path=self.settings.data_dir / "glossary.json",
                concept_index=prefetch,
                llm_concurrency=self.settings.llm_concurrency,
                cells=ir_cells,
                edges=ir_cell_edges,
            ),
        )
        cells = ir_cells
        edges = ir_edges
        workbook_meta = json.loads((dest_dir / "raw" / "workbook.json").read_text(encoding="utf-8"))
        status = _final_status(mapping, embed=self.embed, chat=self.chat)
        set_stage("graph", status="running")
        if not (dest_dir / "graph.json").is_file():
            graph = _timed(
                "graph",
                lambda: build_formula_graph(
                    dest_dir,
                    job_id=job_id,
                    layout=layout,
                    mapping=mapping,
                    cells=ir_cells,
                    edges=ir_edges,
                    cell_edges=ir_cell_edges,
                ),
            )
        else:
            payload = json.loads((dest_dir / "graph.json").read_text(encoding="utf-8"))
            unexpected = sum(
                1 for c in payload.get("cycles") or [] if c.get("class") == "unexpected"
            )
            iterative = sum(
                1 for c in payload.get("cycles") or [] if c.get("class") == "iterative_ok"
            )
            graph = GraphPointer(
                artifact="graph.json",
                cell_edges="ir/cell_edges.parquet",
                index="ir/graph_index.parquet",
                nodes=int(payload.get("nodes") or 0),
                edges=int(payload.get("edges") or 0),
                cycles_unexpected=unexpected,
                cycles_iterative=iterative,
                iterate=bool(payload.get("iterate")),
                unresolved=int((payload.get("unresolved") or {}).get("count") or 0),
                dangling=int((payload.get("dangling") or {}).get("count") or 0),
                empty_range_members=int(
                    (payload.get("dangling_classes") or {}).get("empty_range_member") or 0
                ),
            )
        set_stage("build", status="running")
        doc = _timed(
            "build",
            lambda: build_context(
                job_id=job_id,
                workbook_meta=workbook_meta,
                cells=cells,
                layout=layout,
                mapping=mapping,
                source_filename=source_filename,
                content_sha256=content_sha256,
                status=status,
                stage="done",
                graph=graph,
                edges=edges,
            ),
        )
        _write_sorted_json(dest_dir / "context.json", doc.model_dump(mode="json"))
        set_stage("render", status="running")
        markdown = render_markdown(doc)
        (dest_dir / "context.md").parent.mkdir(parents=True, exist_ok=True)
        tmp = dest_dir / "context.md.tmp"
        tmp.write_text(markdown, encoding="utf-8")
        tmp.replace(dest_dir / "context.md")
        _raise_if_cancelled(progress)
        _write_meta(
            dest_dir,
            job_id=job_id,
            status=status,
            stage="done",
            source_filename=source_filename,
            content_sha256=content_sha256,
            warnings=doc.warnings,
            questions=[q.model_dump(mode="json") for q in mapping.questions],
        )
        return doc


def run_job_process(data_dir: str, job_id: str) -> None:
    """Entry point for a spawned interpreter. Runs one book on this process's main thread."""
    settings = Settings(data_dir=Path(data_dir))
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    _install_stage_pause()
    dest = settings.data_dir / "jobs" / job_id
    try:
        Pipeline(settings).run(dest, job_id=job_id)
    except Exception as exc:
        mark_job_failed(dest, job_id, type(exc).__name__)
        raise


def mark_job_failed(dest: Path, job_id: str, error: str) -> None:
    ready = ("context.json", "context.md", "graph.json", "graph.md")
    if all((dest / name).is_file() for name in ready):
        return
    dest.mkdir(parents=True, exist_ok=True)
    write_json(
        dest / "meta.json",
        {
            "job_id": job_id,
            "status": "failed",
            "stage": "failed",
            "error": error,
            "warnings": [],
            "questions": [],
        },
    )


def _install_stage_pause() -> None:
    stage = os.environ.get("FINANCE_CONTEXT_PAUSE_STAGE", "").strip()
    if not stage:
        return
    attr = {
        "parse": "parse_workbook",
        "compile": "compile_workbook",
        "layout": "layout_workbook",
        "mapping": "mapping_workbook",
    }.get(stage)
    if attr is None:
        return
    original = globals()[attr]

    def wrapped(*args, **kwargs):
        _wait_for_pause_release()
        return original(*args, **kwargs)

    globals()[attr] = wrapped


def _wait_for_pause_release() -> None:
    raw = os.environ.get("FINANCE_CONTEXT_PAUSE_FILE", "").strip()
    if not raw:
        return
    marker = Path(raw)
    deadline = time.monotonic() + 60
    while marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)


def _read_ir(dest_dir: Path) -> tuple[list[dict], list[dict], list[dict]]:
    cells = read_parquet(dest_dir / "ir" / "cells.parquet")
    edges_path = dest_dir / "ir" / "edges.parquet"
    edges = read_parquet(edges_path) if edges_path.is_file() else []
    cell_edges_path = dest_dir / "ir" / "cell_edges.parquet"
    cell_edges = read_parquet(cell_edges_path) if cell_edges_path.is_file() else []
    return cells, edges, cell_edges


def _start_taxonomy_prefetch(pipeline: Pipeline) -> TaxonomyPrefetch | None:
    if pipeline.embed is None:
        return None
    return TaxonomyPrefetch(
        pipeline.embed,
        load_taxonomy(),
        cache_path=pipeline.settings.data_dir / "taxonomy_embeddings.npz",
        model=pipeline.settings.embedding_model or "",
    )


def _raise_if_cancelled(progress: object | None) -> None:
    if progress is None:
        return
    fn = getattr(progress, "cancelled", None)
    if callable(fn) and fn():
        raise ContextError("job_timeout", "job timed out")


def _final_status(mapping: MappingDocument, *, embed: object, chat: object) -> str:
    if mapping.questions:
        if embed is None and chat is None:
            return "degraded"
        return "needs_input"
    return "succeeded"


def _timed(stage: str, fn):
    token = stage_var.set(stage)
    t0 = time.monotonic()
    try:
        return fn()
    except PortError:
        log_event(_LOGGER, logging.WARNING, "port_fallback", "port failed", stage=stage)
        raise
    finally:
        log_event(
            _LOGGER,
            logging.INFO,
            "stage_done",
            f"{stage} done",
            stage=stage,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        stage_var.reset(token)


def _write_meta(dest_dir: Path, **fields: object) -> None:
    meta = ArtifactMeta(
        job_id=str(fields["job_id"]),
        status=fields.get("status", "running"),  # type: ignore[arg-type]
        stage=str(fields.get("stage", "queued")),
        source_filename=fields.get("source_filename"),  # type: ignore[arg-type]
        content_sha256=fields.get("content_sha256"),  # type: ignore[arg-type]
        warnings=list(fields.get("warnings") or []),  # type: ignore[arg-type]
        questions=list(fields.get("questions") or []),  # type: ignore[arg-type]
        error=fields.get("error"),  # type: ignore[arg-type]
    )
    write_json(dest_dir / "meta.json", meta.model_dump(mode="json"))


def _write_sorted_json(path: Path, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _load_owner(dest_dir: Path) -> dict:
    path = dest_dir / "owner.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
