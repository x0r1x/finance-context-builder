from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse, Response

from finance_context.api.errors import ApiError
from finance_context.api.etag import json_bytes
from finance_context.api.http import _MARKDOWN, _check_job_id, _ctx, _errors
from finance_context.api.state import AppContext
from finance_context.api.threads import run_blocking
from finance_context.graph.models import TraceDocument
from finance_context.graph.trace import trace_graph
from finance_context.models.observation import ObservationPrecedent
from finance_context.render.graph import render_trace_markdown

router = APIRouter()

_From = Annotated[
    str,
    Query(alias="from", description="Cell address, row_key, or label."),
]
_Direction = Annotated[
    Literal["precedents", "dependents"],
    Query(description="precedents walks inputs; dependents walks results."),
]
_Depth = Annotated[int, Query(description="How many hops to walk.")]
_RangeMembers = Annotated[
    int | None,
    Query(
        ge=0,
        description=(
            "Cells of one large range a precedents walk follows. 0 follows all. "
            "Default TRACE_RANGE_MEMBERS. The edge carries members_total and members_shown."
        ),
    ),
]


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
    range_members: _RangeMembers = None,
) -> Response:
    _check_job_id(job_id)
    ctx = _ctx(request)
    body = await run_blocking(
        ctx, _trace_json, ctx, job_id, origin, direction, depth, range_members
    )
    return Response(content=body, media_type="application/json")


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
    range_members: _RangeMembers = None,
) -> PlainTextResponse:
    _check_job_id(job_id)
    ctx = _ctx(request)
    body = await run_blocking(
        ctx, _trace_markdown, ctx, job_id, origin, direction, depth, range_members
    )
    return PlainTextResponse(body, media_type="text/markdown")


def _trace_json(
    ctx: AppContext,
    job_id: str,
    origin: str,
    direction: str,
    depth: int,
    range_members: int | None = None,
) -> bytes:
    doc = _trace(ctx, job_id, origin, direction, depth, range_members)
    return json_bytes(doc.model_dump(mode="json"))


def _trace_markdown(
    ctx: AppContext,
    job_id: str,
    origin: str,
    direction: str,
    depth: int,
    range_members: int | None = None,
) -> str:
    doc = _trace(ctx, job_id, origin, direction, depth, range_members)
    return render_trace_markdown(doc)


def _trace(
    ctx: AppContext,
    job_id: str,
    origin: str,
    direction: str,
    depth: int,
    range_members: int | None = None,
):
    dest = ctx.store.dest_dir(job_id)
    if not dest.exists():
        raise ApiError(404, "not_found")
    if not (dest / "graph" / "graph.json").is_file():
        raise ApiError(409, "report_not_ready")
    return trace_graph(
        dest,
        origin=origin,
        direction=direction,
        depth=depth,
        book_dir=ctx.store.shared_dir(job_id),
        range_members=(
            ctx.settings.trace_range_members if range_members is None else range_members
        ),
    )


def _precedents(dest: Path, book: Path, range_members: int):
    def lookup(origin: str, depth: int) -> list[ObservationPrecedent]:
        try:
            traced = trace_graph(
                dest,
                origin=origin,
                direction="precedents",
                depth=depth,
                book_dir=book,
                range_members=range_members,
            )
        except FileNotFoundError as exc:
            raise ApiError(409, "report_not_ready") from exc
        return [
            ObservationPrecedent(
                row_key=node.row_key,
                period_id=node.period_id,
                value=node.cached_value,
                cell=node.addr,
                depth=node.depth,
            )
            for node in traced.nodes
            if node.depth >= 1
        ]

    return lookup
