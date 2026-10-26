from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, Response

from finance_context.api.http import _LOGGER, _MARKDOWN, _ctx, _errors, _require_artifact
from finance_context.api.threads import run_blocking
from finance_context.graph.models import GraphDocument
from finance_context.models.context import ContextDocument
from finance_context.observability import log_event
from finance_context.render.markdown import commit_renderer_stamp, refresh_context_markdown
from finance_context.settings import Settings
from finance_context.store.duckdb_io import read_blob_bytes, read_published_file, republish_file

router = APIRouter()


def _document_bytes(settings: Settings, path: Path, job_id: str, name: str) -> bytes:
    return read_published_file(settings, path, job_id, name)


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
async def get_context_json(request: Request, job_id: str) -> Response:
    ctx = _ctx(request)
    path = await run_blocking(ctx, _require_artifact, request, job_id, "context/context.json")
    body = await run_blocking(
        ctx, _document_bytes, ctx.settings, path, job_id, "context/context.json"
    )
    return Response(content=body, media_type="application/json")


@router.get(
    "/v1/context-jobs/{job_id}/context.md",
    response_class=PlainTextResponse,
    summary="Context Markdown",
    responses={200: _MARKDOWN, **_errors(404, 409)},
)
async def get_context_md(request: Request, job_id: str) -> Response:
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
            body = await run_blocking(ctx, read_blob_bytes, str(path))
            return Response(content=body, media_type="text/markdown")
        await run_blocking(ctx, commit_renderer_stamp, path.parent)
    body = await run_blocking(
        ctx, _document_bytes, ctx.settings, path, job_id, "context/context.md"
    )
    return Response(content=body, media_type="text/markdown")


@router.get(
    "/v1/context-jobs/{job_id}/graph.json",
    response_model=GraphDocument,
    summary="Formula graph JSON",
    responses=_errors(404, 409),
)
async def get_graph_json(request: Request, job_id: str) -> Response:
    ctx = _ctx(request)
    path = await run_blocking(ctx, _require_artifact, request, job_id, "graph/graph.json")
    body = await run_blocking(ctx, _document_bytes, ctx.settings, path, job_id, "graph/graph.json")
    return Response(content=body, media_type="application/json")


@router.get(
    "/v1/context-jobs/{job_id}/graph.md",
    response_class=PlainTextResponse,
    summary="Formula graph Markdown",
    responses={200: _MARKDOWN, **_errors(404, 409)},
)
async def get_graph_md(request: Request, job_id: str) -> Response:
    ctx = _ctx(request)
    path = await run_blocking(ctx, _require_artifact, request, job_id, "graph/graph.md")
    body = await run_blocking(ctx, _document_bytes, ctx.settings, path, job_id, "graph/graph.md")
    return Response(content=body, media_type="text/markdown")
