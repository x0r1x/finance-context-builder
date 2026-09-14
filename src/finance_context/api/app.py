from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from finance_context.adapters.disk_store import DiskStore
from finance_context.adapters.memory_bus import MemoryJobBus
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

    def run_job(job_id: str) -> None:
        dest = store.dest_dir(job_id)
        bus.set_progress(job_id, "parse")
        try:
            doc = pipeline.run(dest, job_id=job_id)
            bus.set_terminal(job_id, doc.meta.status, stage="done")
        except Exception as exc:
            log_event(_LOGGER, logging.ERROR, "job_failed", "pipeline failed", exc_info=True)
            write_json(
                dest / "meta.json",
                {
                    "job_id": job_id,
                    "status": "failed",
                    "stage": "failed",
                    "error": type(exc).__name__,
                    "warnings": [],
                    "questions": [],
                },
            )
            bus.set_terminal(job_id, "failed", stage="failed", error=type(exc).__name__)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = start_worker(bus, run_job)
        yield
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


def app_from_env() -> FastAPI:
    return create_app(Settings())
