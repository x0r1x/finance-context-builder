from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from finance_context.adapters.disk_store import DiskStore
from finance_context.adapters.memory_bus import MemoryJobBus
from finance_context.api.context import AppContext
from finance_context.api.errors import ApiError, api_error_handler, context_error_handler
from finance_context.api.processes import JobProcesses
from finance_context.api.routes import router
from finance_context.app.pipeline import Pipeline
from finance_context.errors import ContextError
from finance_context.observability import configure_logging, log_event
from finance_context.settings import Settings

_HTTP_LOGGER = logging.getLogger("finance_context.api.http")


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
        max_concurrent_jobs=settings.max_concurrent_jobs,
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
    _install_http_log(app)
    return app


def app_from_env() -> FastAPI:
    return create_app(Settings())


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
