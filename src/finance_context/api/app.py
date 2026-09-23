from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from finance_context.adapters.disk_store import DiskStore
from finance_context.adapters.memory_bus import JobProgress, MemoryJobBus
from finance_context.api.context import AppContext
from finance_context.api.errors import ApiError, api_error_handler, context_error_handler
from finance_context.api.routes import router, start_worker
from finance_context.app.pipeline import Pipeline
from finance_context.errors import ContextError
from finance_context.observability import configure_logging, log_event
from finance_context.settings import Settings
from finance_context.store.fs import write_json

_LOGGER = logging.getLogger("finance_context.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    store = DiskStore(settings.data_dir)
    bus = MemoryJobBus()
    pipeline = Pipeline(settings)

    def run_job(job_id: str, generation: int) -> None:
        dest = store.dest_dir(job_id)
        progress = JobProgress(bus, job_id, generation)
        progress.progress("parse")
        try:
            doc = pipeline.run(dest, job_id=job_id, progress=progress)
            if not bus.owns(job_id, generation):
                return
            if bus.is_cancelled(job_id, generation):
                _write_failed(dest, job_id, "TimeoutError")
                return
            bus.set_terminal(job_id, doc.meta.status, stage="done", generation=generation)
        except Exception as exc:
            if not bus.owns(job_id, generation):
                return
            log_event(_LOGGER, logging.ERROR, "job_failed", "pipeline failed", exc_info=True)
            timed_out = bus.is_cancelled(job_id, generation) or (
                isinstance(exc, ContextError) and exc.code == "job_timeout"
            )
            error = "TimeoutError" if timed_out else type(exc).__name__
            _write_failed(dest, job_id, error)
            bus.set_terminal(job_id, "failed", stage="failed", error=error, generation=generation)

    def on_timeout(job_id: str, generation: int) -> None:
        if not bus.expire(job_id, generation):
            return
        dest = store.dest_dir(job_id)
        _write_failed(dest, job_id, "TimeoutError")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        workers = [
            start_worker(
                bus,
                run_job,
                timeout_sec=settings.job_timeout_sec,
                on_timeout=on_timeout,
            )
            for _ in range(settings.job_concurrency)
        ]
        yield
        for worker in workers:
            worker.cancel()

    app = FastAPI(title="finance-context-builder", lifespan=lifespan)
    app.state.ctx = AppContext(
        settings=settings,
        store=store,
        bus=bus,
        pipeline=pipeline,
        max_upload_bytes=settings.max_upload_bytes,
    )
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(ContextError, context_error_handler)
    app.include_router(router)
    return app


def _write_failed(dest: Path, job_id: str, error: str) -> None:
    ready = ("context.json", "context.md", "graph.json", "graph.md")
    if all((dest / name).is_file() for name in ready):
        return
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


def app_from_env() -> FastAPI:
    return create_app(Settings())
