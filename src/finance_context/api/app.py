from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request

from finance_context.adapters.disk_store import DiskStore
from finance_context.adapters.memory_bus import MemoryJobBus
from finance_context.api.errors import ApiError, api_error_handler, context_error_handler
from finance_context.api.processes import JobProcesses
from finance_context.api.routes import router
from finance_context.api.state import AppContext
from finance_context.app.pipeline import Pipeline, mark_job_failed
from finance_context.errors import ContextError
from finance_context.jobqueue.queue import RQJobQueue
from finance_context.memory import budget_for_settings, unusable_cap_detail
from finance_context.observability import configure_logging, log_event
from finance_context.settings import Settings
from finance_context.store.filesystem import ensure_data_dir_local
from finance_context.store.paths import session_jobs_root

_HTTP_LOGGER = logging.getLogger("finance_context.api.http")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    ensure_data_dir_local(
        settings.data_dir,
        allow_network=settings.data_dir_allow_network_fs,
    )
    # Reject a SESSION_ID that would leave data/sessions before the server listens.
    session_jobs_root(settings.data_dir, settings.session_id)
    store = DiskStore(settings.data_dir, session_id=settings.session_id)
    bus = MemoryJobBus()
    pipeline = Pipeline(settings)
    processes = JobProcesses(
        data_dir=settings.data_dir,
        bus=bus,
        timeout_sec=settings.job_timeout_sec,
        max_concurrent_jobs=settings.max_concurrent_jobs,
        session_id=settings.session_id,
        memory_budget_bytes=budget_for_settings(settings),
        unusable_detail=unusable_cap_detail(settings),
        watch_interval_sec=settings.job_memory_watch_interval_sec,
        malloc_arena_max=settings.job_malloc_arena_max,
    )
    queue = RQJobQueue.from_settings(settings, connect_timeout=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        reconcile_orphaned_jobs(settings.data_dir, settings.session_id)
        yield
        processes.stop_all()

    app = FastAPI(title="finance-context-builder", lifespan=lifespan)
    app.state.ctx = AppContext(
        settings=settings,
        store=store,
        bus=bus,
        pipeline=pipeline,
        processes=processes,
        queue=queue,
        max_upload_bytes=settings.max_upload_bytes,
    )
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(ContextError, context_error_handler)
    app.include_router(router)
    _install_http_log(app)
    return app


def app_from_env() -> FastAPI:
    return create_app(Settings())


def reconcile_orphaned_jobs(data_dir: Path, session_id: str) -> None:
    """A restart has no live children of this session.

    Jobs left queued or running in this SESSION_ID did not finish. Another
    session's jobs belong to another process and are left alone, including
    a directory entry that resolves outside this session.
    """
    jobs = session_jobs_root(data_dir, session_id)
    if not jobs.is_dir():
        return
    root = jobs.resolve()
    for dest in jobs.iterdir():
        if not dest.is_dir():
            continue
        try:
            resolved = dest.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root):
            continue
        _mark_orphan(dest)


def _mark_orphan(dest: Path) -> None:
    if not dest.is_dir():
        return
    meta_path = dest / "meta.json"
    if not meta_path.is_file():
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(meta, dict) or meta.get("status") not in {"queued", "running"}:
        return
    try:
        generation = int(meta.get("generation") or 0)
    except (TypeError, ValueError):
        generation = 0
    mark_job_failed(
        dest,
        str(meta.get("job_id") or dest.name),
        "process_lost",
        generation,
    )


def _request_path(request: Request) -> str:
    path = request.url.path
    if request.url.query:
        return f"{path}?{request.url.query}"
    return path


def _install_http_log(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_http(request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        path = _request_path(request)
        log_event(
            _HTTP_LOGGER,
            logging.INFO,
            "http_start",
            "request start",
            method=request.method,
            path=path,
        )
        started = time.monotonic()
        response = await call_next(request)
        log_event(
            _HTTP_LOGGER,
            logging.INFO,
            "http_done",
            "request done",
            method=request.method,
            path=path,
            http_code=response.status_code,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return response
