from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from finance_context.adapters.soffice import SofficeConverter
from finance_context.app.job_clock import process_mono_start
from finance_context.app.publisher import publisher_stamp, stamped_stages
from finance_context.app.shared_cache import (
    SharedCacheBusy,
    invalidate_for_run,
    shared_book_lock,
    write_shared_stamp,
)
from finance_context.context.build import build_context
from finance_context.errors import ContextError
from finance_context.excel.legacy import XlsConverter, ensure_xlsx, has_source
from finance_context.excel.stage import parse_workbook
from finance_context.formulas.csr import (
    as_cell_edge,
    bind_range_expand_cap,
    bind_range_node_min_cells,
    reset_range_expand_cap,
    reset_range_node_min_cells,
)
from finance_context.formulas.models import as_edge, release_cell_asts
from finance_context.formulas.stage import EdgeCount, compile_workbook, ir_is_current
from finance_context.graph.stage import build_formula_graph
from finance_context.jobqueue.progress import (
    emit_progress,
    enable_progress,
    progress_enabled,
    reset_progress,
)
from finance_context.layout.models import Layout
from finance_context.layout.relations import formula_relations
from finance_context.layout.stage import layout_workbook, load_relation_cells
from finance_context.memory import (
    MEMORY_ERROR_DETAIL,
    out_of_memory,
    prefer_oom_kill,
    rss_bytes,
    trim_process_heap,
    usage_for_book,
)
from finance_context.models.context import (
    BLOCK_ROW_ADAPTER,
    ArtifactMeta,
    ContextDocument,
    FinancialBlock,
    GraphPointer,
)
from finance_context.observability import (
    bind_log_context,
    configure_logging,
    flush_logs,
    log_event,
    stage_var,
)
from finance_context.render.markdown import (
    commit_renderer_stamp,
    refresh_context_markdown,
    write_context_markdown,
)
from finance_context.settings import Settings
from finance_context.store.duckdb_io import export_succeeded_book, republish_file
from finance_context.store.fs import JsonItems, file_lock, read_parquet, update_json
from finance_context.store.paths import session_job_dir, shared_book_dir

_LOGGER = logging.getLogger("finance_context.pipeline")
_TERMINAL_STATUS = {"succeeded", "degraded", "needs_input", "failed"}
# Interpreter startup sits between proc.start() and process_mono_start, and
# mark_job_failed still has to hit disk. Both fit in this margin.
_BUSY_TIMEOUT_MARGIN_SEC = 2.0
_PARSE_SAVED = ("raw/cells.parquet", "raw/cell_presence.parquet", "raw/workbook.json")
_COMPILE_SAVED = ("ir/cells.parquet", "ir/edges.parquet", "ir/cell_edges.parquet")
_LAYOUT_SAVED = ("layout/layout.json",)
_GRAPH_SAVED = (
    "graph/graph.json",
    "graph/graph.md",
    "graph/index.parquet",
    "graph/edges.parquet",
)
_BUILD_SAVED = ("context/context.json",)
_RENDER_SAVED = ("context/context.md",)
_REUSE_SAVED = (
    "context/context.json",
    "context/context.md",
    "graph/graph.json",
    "graph/graph.md",
)
_READY_ARTIFACTS = (
    "context/context.json",
    "context/context.md",
    "graph/graph.json",
    "graph/graph.md",
)
# Set only in the spawned child. In-process callers measure from execute_job.
_child_watchdog_deadline: float | None = None
# sysexits.h EX_TEMPFAIL. The RQ horse maps this to Retry and does not sleep.
SHARED_CACHE_BUSY_EXIT = 75


class Pipeline:
    def __init__(
        self, settings: Settings | None = None, *, converter: XlsConverter | None = None
    ) -> None:
        self.settings = settings or Settings()
        self.converter = converter or SofficeConverter.from_settings(self.settings)

    def run(
        self,
        dest_dir: Path,
        *,
        job_id: str,
        source_filename: str | None = None,
        content_sha256: str | None = None,
        progress: object | None = None,
        generation: int = 0,
        shared_dir: Path | None = None,
    ) -> ContextDocument:
        book = shared_dir or dest_dir
        shared = book != dest_dir
        with bind_log_context(job_id, source_filename or _owner_name(dest_dir)):
            dest_dir.mkdir(parents=True, exist_ok=True)
            book.mkdir(parents=True, exist_ok=True)
            with shared_book_lock(
                book,
                job_id=job_id,
                retry_after_sec=self.settings.shared_cache_busy_retry_sec,
                max_retries=self.settings.shared_cache_busy_max_retries,
            ):
                if shared:
                    with file_lock(dest_dir / ".lock"):
                        invalidation = invalidate_for_run(
                            dest_dir, book, shared=True, job_id=job_id
                        )
                        document = self._run(
                            dest_dir,
                            job_id=job_id,
                            source_filename=source_filename,
                            content_sha256=content_sha256,
                            progress=progress,
                            generation=generation,
                            shared_dir=book,
                        )
                else:
                    invalidation = invalidate_for_run(dest_dir, book, shared=False, job_id=job_id)
                    document = self._run(
                        dest_dir,
                        job_id=job_id,
                        source_filename=source_filename,
                        content_sha256=content_sha256,
                        progress=progress,
                        generation=generation,
                        shared_dir=None,
                    )
                if shared:
                    write_shared_stamp(
                        book,
                        rebuilt_from=invalidation.rebuilt_from,
                        trusted=invalidation.trusted_stages,
                        adopt=invalidation.adopt,
                    )
                return document

    def _run(
        self,
        dest_dir: Path,
        *,
        job_id: str,
        source_filename: str | None,
        content_sha256: str | None,
        progress: object | None,
        generation: int,
        shared_dir: Path | None = None,
    ) -> ContextDocument:
        book = shared_dir or dest_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        book.mkdir(parents=True, exist_ok=True)
        context_path = dest_dir / "context" / "context.json"
        markdown_path = dest_dir / "context" / "context.md"
        if context_path.is_file() and markdown_path.is_file():
            if refresh_context_markdown(dest_dir / "context"):
                try:
                    republish_file(self.settings, markdown_path, job_id, "context/context.md")
                except Exception:
                    _LOGGER.warning("minio export failed for context.md")
                else:
                    commit_renderer_stamp(dest_dir / "context")
            log_event(
                _LOGGER,
                logging.INFO,
                "job_reused",
                "finished artifacts kept",
                reused=True,
                dir=str(dest_dir),
                saved=_existing(dest_dir, _REUSE_SAVED),
            )
            return ContextDocument.model_validate_json(context_path.read_text(encoding="utf-8"))
        if not has_source(book):
            raise ContextError("empty_file", "source.xlsx missing")
        token = bind_range_expand_cap(self.settings.range_expand_cap)
        node_token = bind_range_node_min_cells(self.settings.range_node_min_cells)
        try:
            return self._run_loaded(
                dest_dir,
                job_id=job_id,
                source_filename=source_filename,
                content_sha256=content_sha256,
                progress=progress,
                generation=generation,
                shared_dir=book if book != dest_dir else None,
            )
        finally:
            reset_range_node_min_cells(node_token)
            reset_range_expand_cap(token)

    def _run_loaded(
        self,
        dest_dir: Path,
        *,
        job_id: str,
        source_filename: str | None,
        content_sha256: str | None,
        progress: object | None,
        generation: int,
        shared_dir: Path | None = None,
    ) -> ContextDocument:
        book = shared_dir or dest_dir
        owner = _load_owner(dest_dir)
        source_filename = source_filename or owner.get("source_filename")
        content_sha256 = content_sha256 or owner.get("content_sha256")

        open_stage: str | None = None

        def set_stage(stage: str, status: str = "running") -> None:
            nonlocal open_stage
            _raise_if_cancelled(progress)
            _write_meta(
                dest_dir,
                job_id=job_id,
                status=status,
                stage=stage,
                source_filename=source_filename,
                content_sha256=content_sha256,
                generation=generation,
            )
            if progress is not None:
                fn = getattr(progress, "progress", None)
                if callable(fn):
                    fn(stage)
            open_stage = _emit_stage(
                self.settings,
                job_id=job_id,
                generation=generation,
                stage=stage,
                open_stage=open_stage,
            )

        set_stage("parse")
        usage_box: list[object | None] = [None]

        def book_usage():
            if usage_box[0] is None:
                usage_box[0] = usage_for_book(book)
            return usage_box[0]

        if not (book / "raw" / "workbook.json").is_file():
            with _stage("parse", root=book, saved=_PARSE_SAVED, settings=self.settings):
                parse_workbook(ensure_xlsx(book, self.converter), book, settings=self.settings)
        else:
            _stage_reused("parse", root=book, saved=_PARSE_SAVED)
        set_stage("compile")
        if not ir_is_current(book):
            profile: dict[str, int] = {}
            with _stage(
                "compile",
                root=book,
                saved=_COMPILE_SAVED,
                counts=profile,
                settings=self.settings,
            ):
                compiled_book = compile_workbook(book, settings=self.settings)
                profile["cells"] = compiled_book.cell_count
                profile["edges"] = len(compiled_book.edges)
                profile["cell_edges"] = len(compiled_book.cell_edges)
                usage_box[0] = compiled_book.usage
                cell_count = compiled_book.cell_count
                ir_edges = compiled_book.edges
                ir_cell_edges = compiled_book.cell_edges
        else:
            _stage_reused("compile", root=book, saved=_COMPILE_SAVED)
            ir_edges, ir_cell_edges = _read_ir(
                book, count_cell_edges=self.settings.graph_stream
            )
            cell_count = _parquet_rows(book / "ir" / "cells.parquet")
        set_stage("layout")
        if not (book / "layout" / "layout.json").is_file():
            layout_profile = {"cells": cell_count, "edges": len(ir_edges)}
            with _stage(
                "layout",
                root=book,
                saved=_LAYOUT_SAVED,
                counts=layout_profile,
                settings=self.settings,
            ):
                layout = layout_workbook(book, edges=ir_edges, settings=self.settings)
        else:
            _stage_reused("layout", root=book, saved=_LAYOUT_SAVED)
            _pause_layout_reuse()
            layout = Layout.model_validate_json(
                (book / "layout" / "layout.json").read_text(encoding="utf-8")
            )
        set_stage("relations")
        relation_profile: dict[str, int] = {}
        with _stage(
            "relations",
            root=book,
            saved=(),
            counts=relation_profile,
            settings=self.settings,
        ):
            relation_cells = load_relation_cells(book, layout)
            relations = formula_relations(layout, relation_cells)
            relation_profile["cells"] = relation_cells.count
            release_cell_asts(relation_cells)
        relation_cells = None
        edges = ir_edges
        workbook_meta = json.loads((book / "raw" / "workbook.json").read_text(encoding="utf-8"))
        status = _final_status()
        set_stage("graph", status="running")
        # graph.json can outlive graph/edges.parquet (a publisher change drops the parquet).
        # Trace walks that file, so a missing parquet has to be rebuilt.
        if (
            not (dest_dir / "graph" / "graph.json").is_file()
            or not (dest_dir / "graph" / "edges.parquet").is_file()
        ):
            graph_profile = {
                "cells": cell_count,
                "edges": len(ir_edges),
                "cell_edges": len(ir_cell_edges),
            }
            graph_edges = None if isinstance(ir_cell_edges, EdgeCount) else ir_cell_edges
            with _stage(
                "graph",
                root=dest_dir,
                saved=_GRAPH_SAVED,
                counts=graph_profile,
                settings=self.settings,
            ):
                graph = build_formula_graph(
                    dest_dir,
                    job_id=job_id,
                    layout=layout,
                    edges=ir_edges,
                    cell_edges=graph_edges,
                    book_dir=book,
                    settings=self.settings,
                )
        else:
            _stage_reused("graph", root=dest_dir, saved=_GRAPH_SAVED)
            payload = json.loads((dest_dir / "graph" / "graph.json").read_text(encoding="utf-8"))
            unexpected = sum(
                1 for c in payload.get("cycles") or [] if c.get("class") == "unexpected"
            )
            iterative = sum(
                1 for c in payload.get("cycles") or [] if c.get("class") == "iterative_ok"
            )
            graph = GraphPointer(
                artifact="graph.json",
                cell_edges="ir/cell_edges.parquet",
                index="graph/index.parquet",
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
        ir_cell_edges = None
        set_stage("build", status="running")
        build_profile = {"cells": cell_count, "edges": len(edges)}
        with _stage(
            "build",
            root=dest_dir,
            saved=_BUILD_SAVED,
            counts=build_profile,
            settings=self.settings,
        ):
            doc = build_context(
                job_id=job_id,
                workbook_meta=workbook_meta,
                layout=layout,
                book_dir=book,
                relations=relations,
                source_filename=source_filename,
                content_sha256=content_sha256,
                status=status,
                stage="done",
                graph=graph,
                edges=edges,
                usage=book_usage(),
            )
            build_profile["formulas"] = doc.workbook.formula_count
            # Render reads the context document. Drop layout before this stage
            # trims so those pages are not still resident.
            edges = ir_edges = relations = layout = None
            _write_sorted_json(
                dest_dir / "context" / "context.json",
                _context_json(doc),
                stream=self.settings.json_stream,
            )
        set_stage("render", status="running")
        with _stage("render", root=dest_dir, saved=_RENDER_SAVED, settings=self.settings):
            write_context_markdown(dest_dir / "context", doc)
        _raise_if_cancelled(progress)
        _write_meta(
            dest_dir,
            job_id=job_id,
            status=status,
            stage="done",
            source_filename=source_filename,
            content_sha256=content_sha256,
            warnings=doc.warnings,
            questions=[],
            generation=generation,
            stream=self.settings.json_stream,
        )
        export_warning = export_succeeded_book(
            self.settings,
            job_id=job_id,
            book=book,
            dest=dest_dir,
        )
        if export_warning:
            update_json(
                dest_dir / "meta" / "meta.json",
                lambda stored: _append_export_warning(
                    stored,
                    generation=generation,
                    warning=export_warning,
                ),
                stream=self.settings.json_stream,
            )
        if progress_enabled():
            if open_stage is not None:
                emit_progress(
                    self.settings,
                    session_id=self.settings.session_id,
                    job_id=job_id,
                    event="stage_done",
                    status=status,
                    stage=open_stage,
                    generation=generation,
                )
            emit_progress(
                self.settings,
                session_id=self.settings.session_id,
                job_id=job_id,
                event="succeeded",
                status=status,
                stage="done",
                generation=generation,
            )
        return doc


def run_job_process(
    data_dir: str, job_id: str, generation: int = 0, session_id: str = "local"
) -> None:
    """Entry point that waits out a busy shared cache in this process.

    CLI ``build`` and in-process tests use this. The RQ child uses
    :func:`run_queued_job` so a busy lock does not hold a worker slot.
    """
    _run_pipeline_child(data_dir, job_id, generation, session_id, wait_on_busy=True)


def run_queued_job(
    data_dir: str, job_id: str, generation: int = 0, session_id: str = "local"
) -> None:
    """RQ spawn child. One :meth:`Pipeline.run`, then exit.

    A busy shared book lock exits :data:`SHARED_CACHE_BUSY_EXIT` without
    writing ``failed`` and without sleeping. The horse turns that into
    ``rq.Retry``. Other errors are recorded the same way as
    :func:`run_job_process`.
    """
    token = enable_progress()
    try:
        _run_pipeline_child(data_dir, job_id, generation, session_id, wait_on_busy=False)
    finally:
        reset_progress(token)


def _run_pipeline_child(
    data_dir: str,
    job_id: str,
    generation: int,
    session_id: str,
    *,
    wait_on_busy: bool,
) -> None:
    """Run one book on this process's main thread."""
    settings = Settings(data_dir=Path(data_dir), session_id=session_id)
    _arm_child_watchdog(settings.job_timeout_sec)
    prefer_oom_kill(score=settings.job_oom_score_adj)
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    _install_stage_pause()
    dest = session_job_dir(settings.data_dir, job_id, settings.session_id)
    shared = shared_book_dir(settings.data_dir, job_id)
    pipeline = Pipeline(settings)
    with bind_log_context(job_id, _owner_name(dest)):
        try:
            if wait_on_busy:
                execute_job(
                    pipeline,
                    dest,
                    job_id=job_id,
                    generation=generation,
                    shared_dir=shared,
                )
            else:
                pipeline.run(
                    dest,
                    job_id=job_id,
                    generation=generation,
                    shared_dir=shared,
                )
        except SharedCacheBusy:
            raise SystemExit(SHARED_CACHE_BUSY_EXIT) from None
        except ContextError as exc:
            # A known refusal (encrypted book, over budget, bad zip): the code, no traceback.
            mark_job_failed(dest, job_id, exc.code, generation, detail=exc.detail or None)
            log_event(
                _LOGGER,
                logging.WARNING,
                "job_failed",
                str(exc),
                error_code=exc.code,
                reason=exc.detail or None,
            )
        except Exception as exc:
            if not out_of_memory(exc):
                mark_job_failed(dest, job_id, type(exc).__name__, generation)
                raise
            mark_job_failed(
                dest, job_id, "workbook_too_large", generation, detail=MEMORY_ERROR_DETAIL
            )
            log_event(
                _LOGGER,
                logging.WARNING,
                "job_failed",
                f"workbook_too_large: {MEMORY_ERROR_DETAIL}",
                error_code="workbook_too_large",
                reason=MEMORY_ERROR_DETAIL,
                cause=type(exc).__name__,
            )
        finally:
            # In-process tests call this too. A leftover deadline would outlive the job.
            _clear_child_watchdog()
            flush_logs()


def execute_job(
    pipeline: Pipeline,
    dest_dir: Path,
    *,
    job_id: str,
    source_filename: str | None = None,
    content_sha256: str | None = None,
    progress: object | None = None,
    generation: int = 0,
    shared_dir: Path | None = None,
) -> ContextDocument:
    """Run one job, waiting out :class:`SharedCacheBusy` inside this process.

    CLI ``build`` and in-process tests use this. The RQ spawn child must not:
    it calls :meth:`Pipeline.run` once and exits :data:`SHARED_CACHE_BUSY_EXIT`
    so the horse can return ``rq.Retry``. After ``max_retries`` requeues, the
    horse fails the job with ``shared_cache_busy``.

    Here the first busy result is followed by at most ``max_retries`` more
    attempts, sleeping ``retry_after_sec`` between them. ``max_retries`` of 0
    fails on the first busy result. A sleep that would reach the parent
    watchdog fails the same way, so the parent does not record ``TimeoutError``
    first. The spawned child counts that watchdog from process start. Each
    wait logs ``shared_cache_busy_requeue``.
    """
    attempts = 0
    deadline = _busy_deadline(pipeline)
    with bind_log_context(job_id, source_filename or _owner_name(dest_dir)):
        while True:
            try:
                return pipeline.run(
                    dest_dir,
                    job_id=job_id,
                    source_filename=source_filename,
                    content_sha256=content_sha256,
                    progress=progress,
                    generation=generation,
                    shared_dir=shared_dir,
                )
            except SharedCacheBusy as exc:
                attempts += 1
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "shared_cache_busy_requeue",
                    "shared cache busy",
                    job_id=exc.job_id,
                    attempt=attempts,
                    retry_after_sec=exc.retry_after_sec,
                    max_retries=exc.max_retries,
                )
                if attempts > exc.max_retries or _busy_wait_exceeds(deadline, exc.retry_after_sec):
                    raise ContextError(
                        "shared_cache_busy",
                        "shared book cache stayed busy",
                    ) from exc
                time.sleep(exc.retry_after_sec)


def _arm_child_watchdog(timeout: object) -> None:
    """Anchor the busy wait to process start. The parent clock already includes imports."""
    global _child_watchdog_deadline
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        _child_watchdog_deadline = None
        return
    _child_watchdog_deadline = process_mono_start + float(timeout)


def _clear_child_watchdog() -> None:
    global _child_watchdog_deadline
    _child_watchdog_deadline = None


def _busy_deadline(pipeline: Pipeline) -> float | None:
    if _child_watchdog_deadline is not None:
        return _child_watchdog_deadline
    settings = getattr(pipeline, "settings", None)
    timeout = getattr(settings, "job_timeout_sec", None)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        return None
    return time.monotonic() + float(timeout)


def _busy_wait_exceeds(deadline: float | None, retry_after_sec: float) -> bool:
    if deadline is None:
        return False
    remaining = deadline - time.monotonic() - _BUSY_TIMEOUT_MARGIN_SEC
    return remaining < retry_after_sec


def mark_job_failed(
    dest: Path,
    job_id: str,
    error: str,
    generation: int = 0,
    *,
    detail: str | None = None,
) -> None:
    dest.mkdir(parents=True, exist_ok=True)

    def mutate(current: dict) -> dict:
        seen = _stored_generation(current)
        if seen is not None and seen != generation:
            return current
        ready = all((dest / name).is_file() for name in _READY_ARTIFACTS)
        if ready and current.get("status") in _TERMINAL_STATUS:
            return current
        failed = dict(current)
        # A failure that still carries this process's publisher looks finished
        # to the next POST, so the book is never launched again.
        failed.pop("publisher", None)
        failed.pop("stages", None)
        failed["job_id"] = job_id
        failed["status"] = "failed"
        failed["stage"] = "failed"
        failed["error"] = error
        failed["generation"] = generation
        if detail:
            failed["detail"] = detail
        return failed

    update_json(dest / "meta" / "meta.json", mutate)
    _emit_failure(dest, job_id, error, generation)


def _emit_failure(dest: Path, job_id: str, error: str, generation: int) -> None:
    session_id = _session_of(dest)
    if session_id is None:
        return
    event = "queue_lost" if error == "queue_lost" else "failed"
    emit_progress(
        Settings(),
        session_id=session_id,
        job_id=job_id,
        event=event,
        status="failed",
        stage="failed",
        generation=generation,
        error=error,
    )


def _emit_stage(
    settings: Settings,
    *,
    job_id: str,
    generation: int,
    stage: str,
    open_stage: str | None,
) -> str | None:
    """Publish compile, layout, graph, and publish start/done. Return the open stage.

    ``relations`` stays on the open layout event, the same way build and render
    stay on publish, so the stream does not gain a stage name.
    """
    if not progress_enabled():
        return open_stage
    if stage == "parse":
        emit_progress(
            settings,
            session_id=settings.session_id,
            job_id=job_id,
            event="running",
            status="running",
            stage="parse",
            generation=generation,
        )
        return open_stage
    mapped = {"compile": "compile", "layout": "layout", "graph": "graph"}.get(stage)
    if stage in {"build", "render"}:
        mapped = "publish"
    if stage == "relations":
        mapped = "layout"
    if mapped is None or mapped == open_stage:
        return open_stage
    if open_stage is not None:
        emit_progress(
            settings,
            session_id=settings.session_id,
            job_id=job_id,
            event="stage_done",
            status="running",
            stage=open_stage,
            generation=generation,
        )
    emit_progress(
        settings,
        session_id=settings.session_id,
        job_id=job_id,
        event="stage_start",
        status="running",
        stage=mapped,
        generation=generation,
    )
    return mapped


def _session_of(dest: Path) -> str | None:
    # data/sessions/{session}/jobs/{job_id}
    if dest.parent.name != "jobs":
        return None
    session_id = dest.parent.parent.name
    return session_id or None


def _pause_layout_reuse() -> None:
    """Test seam: hold the layout-already-exists read until the pause file is gone."""
    if os.environ.get("FINANCE_CONTEXT_PAUSE_STAGE", "").strip() != "layout_reuse":
        return
    _wait_for_pause_release()


def _install_stage_pause() -> None:
    stage = os.environ.get("FINANCE_CONTEXT_PAUSE_STAGE", "").strip()
    if not stage:
        return
    attr = {
        "parse": "parse_workbook",
        "compile": "compile_workbook",
        "layout": "layout_workbook",
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


def _read_ir(
    dest_dir: Path, *, count_cell_edges: bool = False
) -> tuple[list, list | EdgeCount]:
    edges_path = dest_dir / "ir" / "edges.parquet"
    edges = read_parquet(edges_path) if edges_path.is_file() else []
    cell_edges_path = dest_dir / "ir" / "cell_edges.parquet"
    if count_cell_edges and cell_edges_path.is_file():
        cell_edges: list | EdgeCount = EdgeCount(_parquet_rows(cell_edges_path))
    else:
        loaded = read_parquet(cell_edges_path) if cell_edges_path.is_file() else []
        cell_edges = [as_cell_edge(edge) for edge in loaded]
    return [as_edge(edge) for edge in edges], cell_edges


def _parquet_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    import pyarrow.parquet as pq

    return pq.ParquetFile(path).metadata.num_rows


def _raise_if_cancelled(progress: object | None) -> None:
    if progress is None:
        return
    fn = getattr(progress, "cancelled", None)
    if callable(fn) and fn():
        raise ContextError("job_timeout", "job timed out")


def _final_status() -> str:
    return "succeeded"


def _existing(root: Path, relative: tuple[str, ...]) -> list[str]:
    return [name for name in relative if (root / name).is_file()]


def _stage_reused(stage: str, *, root: Path, saved: tuple[str, ...]) -> None:
    log_event(
        _LOGGER,
        logging.INFO,
        "stage_reused",
        f"{stage} kept",
        stage=stage,
        reused=True,
        dir=str(root),
        saved=_existing(root, saved),
    )


@contextmanager
def _stage(
    stage: str,
    *,
    root: Path,
    saved: tuple[str, ...],
    counts: dict[str, int] | None = None,
    settings: Settings | None = None,
) -> Generator[None, None, None]:
    token = stage_var.set(stage)
    log_event(_LOGGER, logging.INFO, "stage_start", f"{stage} started", stage=stage)
    rss_in = rss_bytes()
    peak = rss_in
    stop = threading.Event()

    def _sample() -> None:
        nonlocal peak
        while not stop.wait(0.05):
            current = rss_bytes()
            if current > peak:
                peak = current

    sampler = threading.Thread(target=_sample, name=f"rss-{stage}", daemon=True)
    sampler.start()
    t0 = time.monotonic()
    failed = False
    try:
        yield
    except BaseException:
        failed = True
        raise
    finally:
        stop.set()
        sampler.join(timeout=0.2)
        rss_out = rss_bytes()
        if rss_out > peak:
            peak = rss_out
        fields: dict[str, object] = {
            "stage": stage,
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "rss_in_mb": _mb(rss_in),
            "rss_out_mb": _mb(rss_out),
            "rss_peak_mb": _mb(peak),
            "dir": str(root),
            "saved": _existing(root, saved),
        }
        for key in ("cells", "formulas", "edges", "cell_edges"):
            if counts and key in counts:
                fields[key] = counts[key]
        if failed:
            log_event(_LOGGER, logging.WARNING, "stage_failed", f"{stage} failed", **fields)
        else:
            log_event(_LOGGER, logging.INFO, "stage_done", f"{stage} done", **fields)
        if settings is not None and settings.job_malloc_trim:
            trim_process_heap()
        stage_var.reset(token)


def _mb(value: int) -> int:
    return int(value / (1024 * 1024))


def _stored_generation(payload: dict) -> int | None:
    if "generation" not in payload:
        return None
    try:
        return int(payload["generation"])
    except (TypeError, ValueError):
        return None


def _append_export_warning(stored: dict, *, generation: int, warning: str) -> dict:
    """Add the export warning. A newer generation, or the same warning, is left as-is."""
    seen = _stored_generation(stored)
    if seen is not None and seen > generation:
        return stored
    warnings = stored.get("warnings")
    items = list(warnings) if isinstance(warnings, list) else []
    if warning in items:
        return stored
    return {**stored, "warnings": [*items, warning]}


def _write_meta(dest_dir: Path, **fields: object) -> None:
    generation = int(fields.get("generation") or 0)
    meta = ArtifactMeta(
        job_id=str(fields["job_id"]),
        status=fields.get("status", "running"),  # type: ignore[arg-type]
        stage=str(fields.get("stage", "queued")),
        source_filename=fields.get("source_filename"),  # type: ignore[arg-type]
        content_sha256=fields.get("content_sha256"),  # type: ignore[arg-type]
        warnings=list(fields.get("warnings") or []),  # type: ignore[arg-type]
        questions=list(fields.get("questions") or []),  # type: ignore[arg-type]
        error=fields.get("error"),  # type: ignore[arg-type]
    ).model_dump(mode="json")
    meta["generation"] = generation
    meta["publisher"] = publisher_stamp()
    meta["stages"] = stamped_stages()

    def mutate(current: dict) -> dict:
        seen = _stored_generation(current)
        if seen is not None and seen > generation:
            return current
        return meta

    stream = fields.get("stream")
    update_json(
        dest_dir / "meta" / "meta.json",
        mutate,
        stream=stream if isinstance(stream, bool) else None,
    )


def _context_json(doc: ContextDocument) -> dict:
    """``model_dump`` of the document. Blocks and rows are dumped while it is written."""
    data = doc.model_dump(mode="json", exclude={"blocks"})
    data["blocks"] = JsonItems(lambda: (_block_json(block) for block in doc.blocks))
    return data


def _block_json(block: FinancialBlock) -> dict:
    data = block.model_dump(mode="json", exclude={"rows"})
    data["rows"] = JsonItems(
        lambda: (BLOCK_ROW_ADAPTER.dump_python(row, mode="json") for row in block.rows)
    )
    return data


def _write_sorted_json(path: Path, data: dict, *, stream: bool | None = None) -> None:
    from finance_context.store.fs import write_json

    write_json(path, data, sort_keys=True, stream=stream)


def _load_owner(dest_dir: Path) -> dict:
    path = dest_dir / "owner.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _owner_name(dest_dir: Path) -> str | None:
    name = _load_owner(dest_dir).get("source_filename")
    if isinstance(name, str) and name:
        return name
    return None
