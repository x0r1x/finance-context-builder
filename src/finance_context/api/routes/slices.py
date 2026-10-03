from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from finance_context.api.errors import ApiError
from finance_context.api.etag import cached_json, etag_matches, not_modified, strong_etag
from finance_context.api.http import (
    _content_sha256,
    _ctx,
    _errors,
    _read_json,
    _require_artifact,
    _schema_id,
)
from finance_context.api.routes.jobs import _load_meta
from finance_context.api.routes.trace import _precedents
from finance_context.context.catalog import build_catalog
from finance_context.context.observations import build_observations
from finance_context.context.summary import build_summary
from finance_context.graph.models import FormulaLink
from finance_context.models.catalog import CatalogDocument
from finance_context.models.context import ContextDocument, TimelinePhase
from finance_context.models.observation import ObservationDocument
from finance_context.models.summary import SummaryDocument

router = APIRouter()

_NOT_MODIFIED = {304: {"description": "Not modified"}}
_OWN_LABEL = "Case-insensitive substring of the row's own label."

@router.get(
    "/v1/context-jobs/{job_id}/catalog",
    response_model=CatalogDocument,
    summary="Row catalog without values",
    responses={**_NOT_MODIFIED, **_errors(404, 409)},
)
async def get_catalog(
    request: Request,
    job_id: str,
    q: Annotated[str | None, Query(description=_OWN_LABEL)] = None,
    concept_id: Annotated[list[str] | None, Query()] = None,
    sheet: Annotated[str | None, Query()] = None,
    disposition: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=10000)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    path = _require_artifact(request, job_id, "context.json")
    etag = strong_etag(_schema_id(CatalogDocument), _content_sha256(path.parent), path)
    if etag_matches(request.headers.get("if-none-match"), etag):
        return not_modified(etag)
    payload = await asyncio.to_thread(_read_json, path)
    context = ContextDocument.model_validate(payload)
    doc = await asyncio.to_thread(
        build_catalog,
        context,
        q=q,
        concept_ids=concept_id,
        sheet=sheet,
        disposition=disposition,
        limit=limit,
        offset=offset,
    )
    return cached_json(doc.model_dump(mode="json", by_alias=True), etag)


router.add_api_route(
    "/v1/context-jobs/{job_id}/catalog",
    get_catalog,
    methods=["HEAD"],
    include_in_schema=False,
    response_model=CatalogDocument,
)


@router.get(
    "/v1/context-jobs/{job_id}/summary",
    response_model=SummaryDocument,
    summary="Workbook passport without links or cell caches",
    responses={**_NOT_MODIFIED, **_errors(404, 409)},
)
async def get_summary(request: Request, job_id: str) -> Response:
    context_path = _require_artifact(request, job_id, "context.json")
    graph_path = _require_artifact(request, job_id, "graph.json")
    dest = context_path.parent
    etag = strong_etag(
        _schema_id(SummaryDocument),
        _content_sha256(dest),
        context_path,
        graph_path,
    )
    if etag_matches(request.headers.get("if-none-match"), etag):
        return not_modified(etag)
    context_payload = await asyncio.to_thread(_read_json, context_path)
    graph_payload = await asyncio.to_thread(_read_json, graph_path)
    doc = await asyncio.to_thread(
        build_summary,
        context_payload,
        graph_payload,
        meta=_load_meta(dest),
    )
    return cached_json(doc.model_dump(mode="json", by_alias=True), etag)


router.add_api_route(
    "/v1/context-jobs/{job_id}/summary",
    get_summary,
    methods=["HEAD"],
    include_in_schema=False,
    response_model=SummaryDocument,
)


@router.get(
    "/v1/context-jobs/{job_id}/observations",
    response_model=ObservationDocument,
    summary="Selected row-period observations",
    responses={**_NOT_MODIFIED, **_errors(400, 404, 409)},
)
async def get_observations(
    request: Request,
    job_id: str,
    row_key: Annotated[list[str] | None, Query()] = None,
    concept_id: Annotated[list[str] | None, Query()] = None,
    q: Annotated[str | None, Query(description=_OWN_LABEL)] = None,
    period_id: Annotated[list[str] | None, Query()] = None,
    phase: Annotated[list[TimelinePhase] | None, Query()] = None,
    precedent_depth: Annotated[int, Query(ge=0, le=3)] = 0,
    limit: Annotated[int, Query(ge=1, le=48)] = 24,
) -> Response:
    context_path = _require_artifact(request, job_id, "context.json")
    graph_path = _require_artifact(request, job_id, "graph.json")
    if not (row_key or concept_id or (q and q.strip())):
        raise ApiError(400, "selector_required")
    dest = context_path.parent
    etag = strong_etag(
        _schema_id(ObservationDocument),
        _content_sha256(dest),
        context_path,
        graph_path,
    )
    if etag_matches(request.headers.get("if-none-match"), etag):
        return not_modified(etag)
    context_payload = await asyncio.to_thread(_read_json, context_path)
    graph_payload = await asyncio.to_thread(_read_json, graph_path)
    context = ContextDocument.model_validate(context_payload)
    raw_links = graph_payload.get("links") or []
    links = [
        FormulaLink.model_validate(item)
        for item in raw_links
        if isinstance(raw_links, list) and isinstance(item, dict)
    ]
    lookup = _precedents(dest, _ctx(request).store.shared_dir(job_id))
    doc = await asyncio.to_thread(
        build_observations,
        context,
        links,
        row_keys=row_key,
        concept_ids=concept_id,
        q=q,
        period_ids=period_id,
        phases=phase,
        precedent_depth=precedent_depth,
        limit=limit,
        precedents=lookup if precedent_depth else None,
    )
    return cached_json(doc.model_dump(mode="json", by_alias=True), etag)


router.add_api_route(
    "/v1/context-jobs/{job_id}/observations",
    get_observations,
    methods=["HEAD"],
    include_in_schema=False,
    response_model=ObservationDocument,
)
