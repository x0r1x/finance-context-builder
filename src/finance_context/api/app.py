from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Request

from finance_context.adapters.disk_store import DiskStore
from finance_context.api.errors import ApiError, api_error_handler, context_error_handler
from finance_context.api.routes import router
from finance_context.api.state import AppContext
from finance_context.app.pipeline import Pipeline
from finance_context.errors import ContextError
from finance_context.jobqueue.queue import RQJobQueue
from finance_context.observability import configure_logging, flush_logs, log_event
from finance_context.settings import Settings
from finance_context.store.filesystem import ensure_data_dir_local
from finance_context.store.paths import session_jobs_root

_HTTP_LOGGER = logging.getLogger("finance_context.api.http")
_API_LOGGER = logging.getLogger("finance_context.api")


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
    pipeline = Pipeline(settings)
    queue = RQJobQueue.from_settings(settings, connect_timeout=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # One limiter object for every request. Request tasks do not inherit
        # the lifespan contextvar, so routes pass this object into run_sync.
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = settings.api_thread_limit
        app.state.ctx.thread_limiter = limiter
        log_event(
            _API_LOGGER,
            logging.INFO,
            "api_config",
            "effective API thread settings",
            thread_limit=settings.api_thread_limit,
            readyz_ttl_sec=settings.readyz_cache_ttl_sec,
        )
        yield

    app = FastAPI(title="finance-context-builder", lifespan=lifespan)
    app.state.ctx = AppContext(
        settings=settings,
        store=store,
        pipeline=pipeline,
        queue=queue,
        max_upload_bytes=settings.max_upload_bytes,
    )
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(ContextError, context_error_handler)
    app.include_router(router)
    _install_http_log(app)
    # Startup warnings are queued. Hand them to stdout before the caller reads it.
    flush_logs()
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
