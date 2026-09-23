from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from finance_context.adapters.disk_store import DiskStore
from finance_context.adapters.memory_bus import MemoryJobBus
from finance_context.api.context import AppContext
from finance_context.api.errors import ApiError, api_error_handler, context_error_handler
from finance_context.api.processes import JobProcesses
from finance_context.api.routes import router
from finance_context.app.pipeline import Pipeline
from finance_context.errors import ContextError
from finance_context.observability import configure_logging
from finance_context.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    store = DiskStore(settings.data_dir)
    bus = MemoryJobBus()
    pipeline = Pipeline(settings)
    processes = JobProcesses(
        data_dir=settings.data_dir,
        bus=bus,
        timeout_sec=settings.job_timeout_sec,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        processes.stop_all()

    app = FastAPI(title="finance-context-builder", lifespan=lifespan)
    app.state.ctx = AppContext(
        settings=settings,
        store=store,
        bus=bus,
        pipeline=pipeline,
        processes=processes,
        max_upload_bytes=settings.max_upload_bytes,
    )
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(ContextError, context_error_handler)
    app.include_router(router)
    return app


def app_from_env() -> FastAPI:
    return create_app(Settings())
