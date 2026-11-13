from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, StreamingResponse

from finance_context.api.http import _LOGGER, _MARKDOWN, _ctx, _errors, _require_artifact
from finance_context.api.state import AppContext
from finance_context.api.threads import run_blocking
from finance_context.graph.models import GraphDocument
from finance_context.models.context import ContextDocument
from finance_context.observability import log_event
from finance_context.render.markdown import commit_renderer_stamp, refresh_context_markdown
from finance_context.settings import Settings
from finance_context.store.duckdb_io import (
    PublishedFile,
    read_published_file,
    republish_file,
    stream_local_file,
)

router = APIRouter()


def _next_chunk(iterator: Iterator[bytes]) -> bytes:
    return next(iterator, b"")


async def _chunks(ctx: AppContext, body: PublishedFile) -> AsyncIterator[bytes]:
    """Read the next chunk outside the event loop. ``run_blocking`` owns the thread."""
    iterator = iter(body)
    try:
        while True:
            chunk = await run_blocking(ctx, _next_chunk, iterator)
            if not chunk:
                return
            yield chunk
    finally:
        await run_blocking(ctx, body.close)


def _document_response(ctx: AppContext, body: PublishedFile, media_type: str) -> StreamingResponse:
    return StreamingResponse(
        _chunks(ctx, body),
        media_type=media_type,
        headers={"Content-Length": str(body.content_length)},
    )


def _republish_context_md(settings: Settings, path: Path, job_id: str) -> bool:
    """Copy the refreshed markdown. False leaves the caller on the local bytes."""
    try:
        republish_file(settings, path, job_id, "context/context.md")
    except Exception:
        _LOGGER.warning("minio export failed for context.md")
        return False
    return True


@router.get(
    "/v1/context-jobs/{job_id}/context.json",
    response_model=ContextDocument,
    summary="Context JSON",
    responses=_errors(404, 409),
)
async def get_context_json(request: Request, job_id: str) -> StreamingResponse:
    ctx = _ctx(request)
    path = await run_blocking(ctx, _require_artifact, request, job_id, "context/context.json")
    body = await run_blocking(
        ctx, read_published_file, ctx.settings, path, job_id, "context/context.json"
    )
    return _document_response(ctx, body, "application/json")


@router.get(
    "/v1/context-jobs/{job_id}/context.md",
    response_class=PlainTextResponse,
    summary="Context Markdown",
    responses={200: _MARKDOWN, **_errors(404, 409)},
)
async def get_context_md(request: Request, job_id: str) -> StreamingResponse:
    ctx = _ctx(request)
    path = await run_blocking(ctx, _require_artifact, request, job_id, "context/context.md")
    if await run_blocking(ctx, refresh_context_markdown, path.parent):
        log_event(
            _LOGGER,
            logging.INFO,
            "context_md_refresh",
            "context.md rewritten from context.json",
            job_id=job_id,
        )
        copied = await run_blocking(ctx, _republish_context_md, ctx.settings, path, job_id)
        if not copied:
            body = await run_blocking(ctx, stream_local_file, path)
            return _document_response(ctx, body, "text/markdown")
        await run_blocking(ctx, commit_renderer_stamp, path.parent)
    body = await run_blocking(
        ctx, read_published_file, ctx.settings, path, job_id, "context/context.md"
    )
    return _document_response(ctx, body, "text/markdown")


@router.get(
    "/v1/context-jobs/{job_id}/graph.json",
    response_model=GraphDocument,
    summary="Formula graph JSON",
    responses=_errors(404, 409),
)
async def get_graph_json(request: Request, job_id: str) -> StreamingResponse:
    ctx = _ctx(request)
    path = await run_blocking(ctx, _require_artifact, request, job_id, "graph/graph.json")
    body = await run_blocking(
        ctx, read_published_file, ctx.settings, path, job_id, "graph/graph.json"
    )
    return _document_response(ctx, body, "application/json")


@router.get(
    "/v1/context-jobs/{job_id}/graph.md",
    response_class=PlainTextResponse,
    summary="Formula graph Markdown",
    responses={200: _MARKDOWN, **_errors(404, 409)},
)
async def get_graph_md(request: Request, job_id: str) -> StreamingResponse:
    ctx = _ctx(request)
    path = await run_blocking(ctx, _require_artifact, request, job_id, "graph/graph.md")
    body = await run_blocking(
        ctx, read_published_file, ctx.settings, path, job_id, "graph/graph.md"
    )
    return _document_response(ctx, body, "text/markdown")
