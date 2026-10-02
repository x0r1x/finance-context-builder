from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated, Literal

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response

from finance_context.api.context import AppContext
from finance_context.api.errors import ApiError
from finance_context.api.etag import cached_json, etag_matches, not_modified, strong_etag
from finance_context.api.schemas import ErrorBody, HealthBody, JobBody, JobListItem, ReadyBody
from finance_context.app.artifacts import clear_downstream_artifacts
from finance_context.app.ids import job_id_for, sha256_bytes
from finance_context.app.jobs import list_session_jobs
from finance_context.app.pipeline import mark_job_failed
from finance_context.app.publisher import publisher_changed, publisher_matches, stale_from_meta
from finance_context.context.catalog import build_catalog
from finance_context.context.observations import build_observations
from finance_context.context.summary import build_summary
from finance_context.errors import ContextError
from finance_context.excel.zip_guard import open_xlsx_zip
from finance_context.graph.models import FormulaLink, GraphDocument, TraceDocument
from finance_context.graph.trace import trace_graph
from finance_context.models.catalog import CatalogDocument
from finance_context.models.context import ContextDocument, JobStatus, TimelinePhase
from finance_context.models.observation import ObservationDocument, ObservationPrecedent
from finance_context.models.summary import SummaryDocument
from finance_context.observability import log_event
from finance_context.render.markdown import refresh_context_markdown
from finance_context.store.fs import atomic_write_bytes, file_lock, write_json

router = APIRouter()
_LOGGER = logging.getLogger("finance_context.api")
_ALLOWED = {".xlsx", ".xlsm"}
_JOB_ID = re.compile(r"^[0-9a-f]{64}$")
_OWN_LABEL = "Case-insensitive substring of the row's own label."
_From = Annotated[
    str,
    Query(alias="from", description="Cell address, row_key, or label."),
]
_Direction = Annotated[
    Literal["precedents", "dependents"],
    Query(description="precedents walks inputs; dependents walks results."),
]
_Depth = Annotated[int, Query(description="How many hops to walk.")]
_MARKDOWN = {
    "content": {"text/markdown": {"schema": {"type": "string"}}},
    "description": "Markdown rendering of the same document.",
}


def _errors(*codes: int) -> dict[int, dict[str, object]]:
    return {code: {"model": ErrorBody} for code in codes}
_STAGE_RANK = {
    "queued": 0,
    "parse": 1,
    "compile": 2,
    "layout": 3,
    "mapping": 4,
    "graph": 5,
    "build": 6,
    "render": 7,
    "done": 8,
}


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


@router.get("/healthz", response_model=HealthBody, summary="Process is up")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/readyz",
    response_model=ReadyBody,
    summary="Process can accept work",
    responses={503: {"model": ReadyBody}},
)
async def readyz(request: Request) -> JSONResponse:
    ctx = _ctx(request)
    settings = ctx.settings
    writable = _data_dir_writable(settings.data_dir)
    body = {
        "status": "ready" if writable else "unavailable",
        "llm": settings.llm_configured(),
        "embeddings": settings.embed_configured(),
        "queue": "in_process",
        "jobs": ctx.processes.alive_count(),
    }
    return JSONResponse(body, status_code=200 if writable else 503)


_JobStatusQuery = Annotated[
    JobStatus | None,
    Query(description="Keep jobs with this status."),
]
_JobQuery = Annotated[
    str | None,
    Query(description="Case-insensitive substring of source_filename."),
]


@router.get(
    "/v1/context-jobs",
    response_model=list[JobListItem],
    summary="List session jobs",
)
async def list_jobs(
    request: Request,
    status: _JobStatusQuery = None,
    q: _JobQuery = None,
) -> list[dict[str, str | None]]:
    store = _ctx(request).store
    jobs_root = store.dest_dir("_").parent
    return await asyncio.to_thread(list_session_jobs, jobs_root, status=status, q=q)


def _data_dir_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=path, prefix=".ready-", delete=True):
            return True
    except OSError:
        return False


@router.post(
    "/v1/context-jobs",
    status_code=202,
    response_model=JobBody,
    summary="Upload a workbook and start or reuse its job",
    responses=_errors(400, 413, 422, 429),
)
async def post_job(
    request: Request,
    file: Annotated[UploadFile | None, File()] = None,
) -> JSONResponse:
    ctx = _ctx(request)
    if file is None:
        raise ContextError("empty_file")
    filename = Path(file.filename or "upload.xlsx").name
    data = await file.read()
    await asyncio.to_thread(_validate_upload, filename, data, ctx.max_upload_bytes)
    digest = await asyncio.to_thread(sha256_bytes, data)
    job_id = job_id_for(digest)
    dest = ctx.store.dest_dir(job_id)
    shared = ctx.store.shared_dir(job_id)
    dest.mkdir(parents=True, exist_ok=True)
    shared.mkdir(parents=True, exist_ok=True)
    source = shared / "source.xlsx"
    if not source.exists():
        atomic_write_bytes(source, data)
    owner_path = dest / "owner.json"
    if not owner_path.exists():
        write_json(
            owner_path,
            {"content_sha256": digest, "source_filename": filename},
        )
    if _stop_for_publisher_change(ctx, job_id, dest):
        ctx.processes.stop(job_id)
    elif ctx.processes.is_alive(job_id):
        return JSONResponse(_running_body(job_id, dest), status_code=202)
    with file_lock(dest / ".lock", blocking=False) as acquired:
        if not acquired:
            return JSONResponse(_running_body(job_id, dest), status_code=202)
        live = ctx.bus.get(job_id)
        if live is not None and live.status in {"queued", "running"}:
            if _stop_for_publisher_change(ctx, job_id, dest):
                ctx.processes.stop(job_id)
            elif ctx.processes.is_alive(job_id):
                return JSONResponse(
                    {"job_id": job_id, "status": live.status, "stage": live.stage},
                    status_code=202,
                )
            elif not ctx.bus.abandon(job_id, live.generation, "process_lost"):
                return JSONResponse(
                    {"job_id": job_id, "status": live.status, "stage": live.stage},
                    status_code=202,
                )
        ready = _ready_meta(dest)
        if ready is not None:
            log_event(
                _LOGGER,
                logging.INFO,
                "job_reuse",
                "finished job reused",
                job_id=job_id,
                status=ready.get("status"),
                stage=ready.get("stage"),
            )
            return JSONResponse(
                {
                    "job_id": job_id,
                    "status": ready.get("status"),
                    "stage": ready.get("stage"),
                },
                status_code=202,
            )
        if not ctx.processes.try_acquire(job_id):
            raise ApiError(429, "too_many_jobs")
        try:
            clear_downstream_artifacts(
                dest, stale_from=stale_from_meta(_load_meta(dest)), shared=shared
            )
            log_event(
                _LOGGER,
                logging.INFO,
                "snapshot_invalidated",
                "layout and mapping artifacts invalidated",
                job_id=job_id,
            )
            started = ctx.bus.enqueue(job_id)
            launched = ctx.processes.launch(job_id) if started else False
        except Exception:
            ctx.processes.release_reservation(job_id)
            raise
        if not launched:
            ctx.processes.release_reservation(job_id)
            if started:
                ctx.bus.abandon(job_id, ctx.bus.current_generation(job_id), "too_many_jobs")
                raise ApiError(429, "too_many_jobs")
    rec = ctx.bus.get(job_id)
    return JSONResponse(
        {
            "job_id": job_id,
            "status": rec.status if rec else "queued",
            "stage": rec.stage if rec else "queued",
        },
        status_code=202,
    )


_READY_ARTIFACTS = ("context.json", "context.md", "graph.json", "graph.md")


def _running_body(job_id: str, dest: Path) -> dict[str, object]:
    meta_path = dest / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = None
        if isinstance(meta, dict) and meta.get("stage"):
            return {
                "job_id": job_id,
                "status": meta.get("status") or "running",
                "stage": meta.get("stage"),
            }
    return {"job_id": job_id, "status": "running", "stage": "running"}


def _orphan_running(meta: dict, *, live: object, alive: bool) -> bool:
    if meta.get("status") not in {"queued", "running"} or alive:
        return False
    return getattr(live, "status", None) not in {"queued", "running"}


def _load_meta(dest: Path) -> dict | None:
    meta_path = dest / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict):
        return None
    return meta


def _stop_for_publisher_change(ctx: AppContext, job_id: str, dest: Path) -> bool:
    return ctx.processes.is_alive(job_id) and publisher_changed(_load_meta(dest))


def _ready_meta(dest: Path) -> dict | None:
    if not all((dest / name).is_file() for name in _READY_ARTIFACTS):
        return None
    meta_path = dest / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict) or meta.get("status") in {"queued", "running", None}:
        return None
    if not publisher_matches(meta):
        return None
    return meta


@router.get(
    "/v1/context-jobs/{job_id}",
    response_model=JobBody,
    summary="Job status",
    responses={202: {"model": JobBody}, **_errors(404)},
)
async def get_job(request: Request, job_id: str) -> JSONResponse:
    _check_job_id(job_id)
    ctx = _ctx(request)
    dest = ctx.store.dest_dir(job_id)
    if not dest.exists():
        raise ApiError(404, "not_found")
    live = ctx.bus.get(job_id)
    meta_path = dest / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if isinstance(meta, dict) and _orphan_running(
            meta, live=live, alive=ctx.processes.is_alive(job_id)
        ):
            try:
                generation = int(meta.get("generation") or 0)
            except (TypeError, ValueError):
                generation = 0
            mark_job_failed(dest, job_id, "process_lost", generation)
            reloaded = _load_meta(dest)
            if reloaded is not None:
                meta = reloaded
        artifacts_ready = (dest / "context.json").exists() and (dest / "context.md").exists() and (
            dest / "graph.json"
        ).exists() and (dest / "graph.md").exists()
        if (
            artifacts_ready
            and meta.get("status") in {"queued", "running", None}
            and live is not None
            and live.status not in {"queued", "running"}
        ):
            meta["status"] = live.status
            meta["stage"] = live.stage
            meta["error"] = live.error
        elif live is not None and live.status in {"queued", "running"} and not artifacts_ready:
            if _STAGE_RANK.get(str(live.stage or ""), -1) > _STAGE_RANK.get(
                str(meta.get("stage") or ""), -1
            ):
                meta["status"] = live.status
                meta["stage"] = live.stage
                meta["error"] = live.error
        return JSONResponse(_job_body(job_id, meta))
    if live is not None:
        return JSONResponse(
            {"job_id": job_id, "status": live.status, "stage": live.stage},
            status_code=202,
        )
    raise ApiError(404, "not_found")


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


_NOT_MODIFIED = {304: {"description": "Not modified"}}


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


def _schema_id(model: type) -> str:
    return str(model.model_fields["schema_version"].default)


def _content_sha256(dest: Path) -> str:
    meta = _load_meta(dest)
    if meta is not None and isinstance(meta.get("content_sha256"), str):
        return meta["content_sha256"]
    return ""


def _read_json(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiError(409, "report_not_ready") from exc
    if not isinstance(loaded, dict):
        raise ApiError(409, "report_not_ready")
    return loaded


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


def _check_job_id(job_id: str) -> None:
    if _JOB_ID.fullmatch(job_id) is None:
        raise ApiError(404, "not_found")


def _require_artifact(request: Request, job_id: str, name: str) -> Path:
    _check_job_id(job_id)
    dest = _ctx(request).store.dest_dir(job_id)
    path = dest / name
    if not dest.exists():
        raise ApiError(404, "not_found")
    if not path.exists():
        raise ApiError(409, "report_not_ready")
    return path


def _job_body(job_id: str, meta: dict) -> dict:
    body = {
        "job_id": job_id,
        "status": meta.get("status"),
        "stage": meta.get("stage"),
        "warnings": meta.get("warnings") or [],
        "error": meta.get("error"),
    }
    if meta.get("status") not in {"queued", "running", None}:
        body["context_json_url"] = f"/v1/context-jobs/{job_id}/context.json"
        body["context_md_url"] = f"/v1/context-jobs/{job_id}/context.md"
        body["graph_json_url"] = f"/v1/context-jobs/{job_id}/graph.json"
        body["graph_md_url"] = f"/v1/context-jobs/{job_id}/graph.md"
    return body


def _validate_upload(filename: str, data: bytes, max_bytes: int) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED:
        raise ContextError("unsupported_media_type", suffix or "missing")
    if not data:
        raise ContextError("empty_file")
    if len(data) > max_bytes:
        raise ContextError("file_too_large")
    with NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        zf = open_xlsx_zip(Path(tmp.name))
        zf.close()


