from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from finance_context.api.errors import ApiError
from finance_context.api.http import _MARKDOWN, _check_job_id, _ctx, _errors
from finance_context.graph.models import TraceDocument
from finance_context.graph.trace import trace_graph
from finance_context.models.observation import ObservationPrecedent

router = APIRouter()

_From = Annotated[
    str,
    Query(alias="from", description="Cell address, row_key, or concept_id."),
]
_Direction = Annotated[
    Literal["precedents", "dependents"],
    Query(description="precedents walks inputs; dependents walks results."),
]
_Depth = Annotated[int, Query(description="How many hops to walk.")]

@router.get(
    "/v1/context-jobs/{job_id}/graph/trace",
    response_model=TraceDocument,
    summary="Walk formula precedents or dependents",
    responses=_errors(404, 409),
)
async def get_graph_trace(
    request: Request,
    job_id: str,
    origin: _From,
    direction: _Direction = "precedents",
    depth: _Depth = 8,
) -> JSONResponse:
    _check_job_id(job_id)
    dest = _ctx(request).store.dest_dir(job_id)
    if not dest.exists():
        raise ApiError(404, "not_found")
    if not (dest / "graph.json").is_file():
        raise ApiError(409, "report_not_ready")
    from finance_context.graph.trace import trace_graph

    doc = await asyncio.to_thread(
        trace_graph,
        dest,
        origin=origin,
        direction=direction,
        depth=depth,
        book_dir=_ctx(request).store.shared_dir(job_id),
    )
    return JSONResponse(doc.model_dump(mode="json"))


@router.get(
    "/v1/context-jobs/{job_id}/graph/trace.md",
    response_class=PlainTextResponse,
    summary="Walk formula precedents or dependents as Markdown",
    responses={200: _MARKDOWN, **_errors(404, 409)},
)
async def get_graph_trace_md(
    request: Request,
    job_id: str,
    origin: _From,
    direction: _Direction = "precedents",
    depth: _Depth = 8,
) -> PlainTextResponse:
    _check_job_id(job_id)
    dest = _ctx(request).store.dest_dir(job_id)
    if not dest.exists():
        raise ApiError(404, "not_found")
    if not (dest / "graph.json").is_file():
        raise ApiError(409, "report_not_ready")
    from finance_context.graph.trace import trace_graph
    from finance_context.render.graph import render_trace_markdown

    doc = await asyncio.to_thread(
        trace_graph,
        dest,
        origin=origin,
        direction=direction,
        depth=depth,
        book_dir=_ctx(request).store.shared_dir(job_id),
    )
    body = await asyncio.to_thread(render_trace_markdown, doc)
    return PlainTextResponse(body, media_type="text/markdown")

def _precedents(dest: Path, book: Path):
    def lookup(origin: str, depth: int) -> list[ObservationPrecedent]:
        try:
            traced = trace_graph(
                dest,
                origin=origin,
                direction="precedents",
                depth=depth,
                book_dir=book,
            )
        except FileNotFoundError as exc:
            raise ApiError(409, "report_not_ready") from exc
        return [
            ObservationPrecedent(
                row_key=node.row_key,
                concept_id=node.concept_id,
                period_id=node.period_id,
                value=node.cached_value,
                cell=node.addr,
                depth=node.depth,
            )
            for node in traced.nodes
            if node.depth >= 1
        ]

    return lookup
