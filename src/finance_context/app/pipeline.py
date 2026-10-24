from __future__ import annotations

import json
import logging
import os
import threading
import time
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
from finance_context.formulas.models import as_edge, as_ir_cell, release_cell_asts
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
from finance_context.layout.stage import layout_workbook
from finance_context.memory import (
    MEMORY_ERROR_DETAIL,
    assess_raw,
    budget_for_settings,
    check_budget,
    prefer_oom_kill,
    rss_bytes,
    trim_process_heap,
    unusable_cap_detail,
    usage_for_book,
)
from finance_context.models.context import ArtifactMeta, ContextDocument, GraphPointer
from finance_context.observability import configure_logging, job_id_var, log_event, stage_var
from finance_context.render.markdown import refresh_context_markdown, write_context_markdown
from finance_context.settings import Settings
from finance_context.store.duckdb_io import export_succeeded_book
from finance_context.store.fs import file_lock, read_parquet, update_json
from finance_context.store.paths import session_job_dir, shared_book_dir

_LOGGER = logging.getLogger("finance_context.pipeline")
_TERMINAL_STATUS = {"succeeded", "degraded", "needs_input", "failed"}
# Interpreter startup sits between proc.start() and process_mono_start, and
# mark_job_failed still has to hit disk. Both fit in this margin.
_BUSY_TIMEOUT_MARGIN_SEC = 2.0
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
        token_job = job_id_var.set(job_id)
        book = shared_dir or dest_dir
        shared = book != dest_dir
        try:
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
        generation: int,
        shared_dir: Path | None = None,
    ) -> ContextDocument:
        book = shared_dir or dest_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        book.mkdir(parents=True, exist_ok=True)
        context_path = dest_dir / "context" / "context.json"
        markdown_path = dest_dir / "context" / "context.md"
        if context_path.is_file() and markdown_path.is_file():
            refresh_context_markdown(dest_dir / "context")
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
        blocked = unusable_cap_detail(self.settings)
        if blocked:
            raise ContextError("workbook_too_large", blocked)
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
        estimate = None
        usage_box: list[object | None] = [None]

        def book_usage():
            if usage_box[0] is None:
                usage_box[0] = usage_for_book(book)
            return usage_box[0]

        if not (book / "raw" / "workbook.json").is_file():
            parse_profile: dict[str, int] = {}

            def parse_and_count():
                parse_workbook(ensure_xlsx(book, self.converter), book, settings=self.settings)
                counted = assess_raw(book, usage_box[0] or book_usage(), settings=self.settings)
                parse_profile["cells"] = counted.cells
                parse_profile["formulas"] = counted.formulas
                parse_profile["estimate_bytes"] = counted.estimate_bytes
                job_budget = budget_for_settings(self.settings)
                if job_budget is not None:
                    parse_profile["budget_bytes"] = job_budget
                return counted

            estimate = _timed("parse", parse_and_count, parse_profile, settings=self.settings)
        set_stage("compile")
        if not ir_is_current(book):
            budget = budget_for_settings(self.settings)
            if estimate is None:
                estimate = assess_raw(book, book_usage(), settings=self.settings)
            check_budget(
                formulas=estimate.formulas,
                expanded_cells=estimate.expanded_cells,
                present_cells=estimate.present_cells,
                budget=budget,
                source="preflight",
                settings=self.settings,
            )
            profile: dict[str, int] = {
                "cells": estimate.cells,
                "formulas": estimate.formulas,
                "estimate_bytes": estimate.estimate_bytes,
            }
            if budget is not None:
                profile["budget_bytes"] = budget

            def compile_and_count():
                compiled_book = compile_workbook(
                    book,
                    memory_budget=budget,
                    usage=book_usage(),
                    settings=self.settings,
                )
                profile["cells"] = len(compiled_book.cells)
                profile["edges"] = len(compiled_book.edges)
                profile["cell_edges"] = len(compiled_book.cell_edges)
                return compiled_book

            compiled = _timed("compile", compile_and_count, profile, settings=self.settings)
            ir_cells = compiled.cells
            ir_edges = compiled.edges
            ir_cell_edges = compiled.cell_edges
            compiled = None
        else:
            ir_cells, ir_edges, ir_cell_edges = _read_ir(
                book, count_cell_edges=self.settings.graph_stream
            )
        set_stage("layout")
        if not (book / "layout" / "layout.json").is_file():
            layout_profile = {"cells": len(ir_cells), "edges": len(ir_edges)}
            layout = _timed(
                "layout",
                lambda: layout_workbook(
                    book, cells=ir_cells, edges=ir_edges, settings=self.settings
                ),
                layout_profile,
                settings=self.settings,
            )
        else:
            _pause_layout_reuse()
            layout = Layout.model_validate_json(
                (book / "layout" / "layout.json").read_text(encoding="utf-8")
            )
        cells = ir_cells
        edges = ir_edges
        relations = formula_relations(layout, ir_cells)
        release_cell_asts(ir_cells)
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
                "cells": len(ir_cells),
                "edges": len(ir_edges),
                "cell_edges": len(ir_cell_edges),
            }
            graph_edges = None if isinstance(ir_cell_edges, EdgeCount) else ir_cell_edges
            graph = _timed(
                "graph",
                lambda: build_formula_graph(
                    dest_dir,
                    job_id=job_id,
                    layout=layout,
                    cells=ir_cells,
                    edges=ir_edges,
                    cell_edges=graph_edges,
                    book_dir=book,
                    settings=self.settings,
                ),
                graph_profile,
                settings=self.settings,
            )
        else:
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
        build_profile = {"cells": len(cells), "edges": len(edges)}

        def build_and_count():
            built = build_context(
                job_id=job_id,
                workbook_meta=workbook_meta,
                cells=cells,
                layout=layout,
                relations=relations,
                source_filename=source_filename,
                content_sha256=content_sha256,
                status=status,
                stage="done",
                graph=graph,
                edges=edges,
                usage=book_usage(),
            )
            build_profile["formulas"] = built.workbook.formula_count
            return built

        doc = _timed("build", build_and_count, build_profile, settings=self.settings)
        cells = edges = ir_cells = ir_edges = relations = None
        _write_sorted_json(
            dest_dir / "context" / "context.json",
            doc.model_dump(mode="json"),
            stream=self.settings.json_stream,
        )
        set_stage("render", status="running")
        _timed(
            "render",
            lambda: write_context_markdown(dest_dir / "context", doc),
            settings=self.settings,
        )
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
    except MemoryError:
        mark_job_failed(dest, job_id, "workbook_too_large", generation, detail=MEMORY_ERROR_DETAIL)
        log_event(
            _LOGGER,
            logging.WARNING,
            "job_failed",
            f"workbook_too_large: {MEMORY_ERROR_DETAIL}",
            error_code="workbook_too_large",
            reason=MEMORY_ERROR_DETAIL,
        )
    except Exception as exc:
        mark_job_failed(dest, job_id, type(exc).__name__, generation)
        raise
    finally:
        # In-process tests call this too. A leftover deadline would outlive the job.
        _clear_child_watchdog()


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
    """Publish compile, layout, graph, and publish start/done. Return the open stage."""
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
) -> tuple[list, list, list | EdgeCount]:
    cells = read_parquet(dest_dir / "ir" / "cells.parquet")
    edges_path = dest_dir / "ir" / "edges.parquet"
    edges = read_parquet(edges_path) if edges_path.is_file() else []
    cell_edges_path = dest_dir / "ir" / "cell_edges.parquet"
    if count_cell_edges and cell_edges_path.is_file():
        import pyarrow.parquet as pq

        cell_edges: list | EdgeCount = EdgeCount(pq.ParquetFile(cell_edges_path).metadata.num_rows)
    else:
        loaded = read_parquet(cell_edges_path) if cell_edges_path.is_file() else []
        cell_edges = [as_cell_edge(edge) for edge in loaded]
    return (
        [as_ir_cell(cell) for cell in cells],
        [as_edge(edge) for edge in edges],
        cell_edges,
    )


def _raise_if_cancelled(progress: object | None) -> None:
    if progress is None:
        return
    fn = getattr(progress, "cancelled", None)
    if callable(fn) and fn():
        raise ContextError("job_timeout", "job timed out")


def _final_status() -> str:
    return "succeeded"


def _timed(
    stage: str,
    fn,
    counts: dict[str, int] | None = None,
    *,
    settings: Settings | None = None,
):
    token = stage_var.set(stage)
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
    try:
        return fn()
    finally:
        stop.set()
        sampler.join(timeout=0.2)
        rss_out = rss_bytes()
        if rss_out > peak:
            peak = rss_out
        duration_ms = int((time.monotonic() - t0) * 1000)
        fields: dict[str, object] = {
            "stage": stage,
            "duration_ms": duration_ms,
            "rss_in_mb": _mb(rss_in),
            "rss_out_mb": _mb(rss_out),
            "rss_peak_mb": _mb(peak),
        }
        for key in (
            "cells",
            "formulas",
            "edges",
            "cell_edges",
            "estimate_bytes",
            "budget_bytes",
        ):
            if counts and key in counts:
                fields[key] = counts[key]
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


def _write_sorted_json(path: Path, data: dict, *, stream: bool | None = None) -> None:
    from finance_context.store.fs import write_json

    write_json(path, data, sort_keys=True, stream=stream)


def _load_owner(dest_dir: Path) -> dict:
    path = dest_dir / "owner.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
