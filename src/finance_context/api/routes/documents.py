from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, PlainTextResponse

from finance_context.api.http import _LOGGER, _MARKDOWN, _errors, _require_artifact
from finance_context.graph.models import GraphDocument
from finance_context.models.context import ContextDocument
from finance_context.observability import log_event
from finance_context.render.markdown import refresh_context_markdown

router = APIRouter()

@router.get(
    "/v1/context-jobs/{job_id}/context.json",
    response_model=ContextDocument,
    summary="Context JSON",
    responses=_errors(404, 409),
)
async def get_context_json(request: Request, job_id: str) -> FileResponse:
    path = _require_artifact(request, job_id, "context.json")
    return FileResponse(path, media_type="application/json")


@router.get(
    "/v1/context-jobs/{job_id}/context.md",
    response_class=PlainTextResponse,
    summary="Context Markdown",
    responses={200: _MARKDOWN, **_errors(404, 409)},
)
async def get_context_md(request: Request, job_id: str) -> FileResponse:
    path = _require_artifact(request, job_id, "context.md")
    if await asyncio.to_thread(refresh_context_markdown, path.parent):
        log_event(
            _LOGGER,
            logging.INFO,
            "context_md_refresh",
            "context.md rewritten from context.json",
            job_id=job_id,
        )
    return FileResponse(path, media_type="text/markdown")


@router.get(
    "/v1/context-jobs/{job_id}/graph.json",
    response_model=GraphDocument,
    summary="Formula graph JSON",
    responses=_errors(404, 409),
)
async def get_graph_json(request: Request, job_id: str) -> FileResponse:
    path = _require_artifact(request, job_id, "graph.json")
    return FileResponse(path, media_type="application/json")


@router.get(
    "/v1/context-jobs/{job_id}/graph.md",
    response_class=PlainTextResponse,
    summary="Formula graph Markdown",
    responses={200: _MARKDOWN, **_errors(404, 409)},
)
async def get_graph_md(request: Request, job_id: str) -> FileResponse:
    path = _require_artifact(request, job_id, "graph.md")
    return FileResponse(path, media_type="text/markdown")
