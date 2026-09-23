from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from finance_context.api.context import AppContext
from finance_context.api.errors import ApiError
from finance_context.api.schemas import ErrorBody, HealthBody, JobBody, ReadyBody
from finance_context.app.ids import job_id_for, sha256_bytes
from finance_context.errors import ContextError
from finance_context.excel.zip_guard import open_xlsx_zip
from finance_context.graph.models import GraphDocument, TraceDocument
from finance_context.models.context import ContextDocument
from finance_context.observability import log_event
from finance_context.store.fs import atomic_write_bytes, file_lock, write_json

router = APIRouter()
_LOGGER = logging.getLogger("finance_context.api")
_ALLOWED = {".xlsx", ".xlsm"}
_From = Annotated[
    str,
    Query(alias="from", description="Cell address, row_key, or concept_id."),
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


@router.get("/readyz", response_model=ReadyBody, summary="Process can accept work")
async def readyz(request: Request) -> dict[str, object]:
    settings = _ctx(request).settings
    return {
        "status": "ready",
        "llm": settings.llm_configured(),
        "embeddings": settings.embed_configured(),
        "queue": "in_process",
    }


@router.post(
    "/v1/context-jobs",
    status_code=202,
    response_model=JobBody,
    summary="Upload a workbook and start or reuse its job",
    responses=_errors(400, 413, 422),
)
async def post_job(
    request: Request,
    file: Annotated[UploadFile | None, File()] = None,
    remap: Annotated[
        str | None,
        Query(
            description=(
                "Rebuild layout, mapping, and context when the value is 1, true, or yes. "
                "A live process for this book is stopped first."
            ),
        ),
    ] = None,
) -> JSONResponse:
    ctx = _ctx(request)
    if file is None:
        raise ContextError("empty_file")
    filename = Path(file.filename or "upload.xlsx").name
    data = await file.read()
    _validate_upload(filename, data, ctx.max_upload_bytes)
    digest = sha256_bytes(data)
    job_id = job_id_for(digest)
    dest = ctx.store.dest_dir(job_id)
    dest.mkdir(parents=True, exist_ok=True)
    source = dest / "source.xlsx"
    if not source.exists():
        atomic_write_bytes(source, data)
    owner_path = dest / "owner.json"
    if not owner_path.exists():
        write_json(
            owner_path,
            {"content_sha256": digest, "source_filename": filename},
        )
    do_remap = _remap_on(remap)
    if ctx.processes.is_alive(job_id) and not do_remap:
        return JSONResponse(_running_body(job_id, dest), status_code=202)
    if do_remap and ctx.processes.is_alive(job_id):
        ctx.processes.stop(job_id)
    started = False
    with file_lock(dest / ".lock", blocking=False) as acquired:
        if not acquired:
            return JSONResponse(_running_body(job_id, dest), status_code=202)
        live = ctx.bus.get(job_id)
        if not do_remap and live is not None and live.status in {"queued", "running"}:
            return JSONResponse(
                {"job_id": job_id, "status": live.status, "stage": live.stage},
                status_code=202,
            )
        if not do_remap:
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
        _clear_downstream_artifacts(dest)
        log_event(
            _LOGGER,
            logging.INFO,
            "job_remap",
            "layout and mapping artifacts invalidated",
            job_id=job_id,
        )
        started = await ctx.bus.enqueue(job_id)
    if started:
        ctx.processes.launch(job_id)
    rec = ctx.bus.get(job_id)
    return JSONResponse(
        {
            "job_id": job_id,
            "status": rec.status if rec else "queued",
            "stage": rec.stage if rec else "queued",
        },
        status_code=202,
    )


def _clear_downstream_artifacts(dest: Path) -> None:
    for name in (
        "layout.json",
        "mapping.json",
        "context.json",
        "context.md",
        "meta.json",
        "graph.json",
        "graph.md",
        "graph-edges.json",
        "graph-dangling.json",
        "formulas.json",
    ):
        (dest / name).unlink(missing_ok=True)
    ir = dest / "ir"
    # Formula IR depends only on the workbook bytes. A remap of the same source keeps it.
    if not (dest / "source.xlsx").is_file():
        for name in ("cells.parquet", "edges.parquet", "cell_edges.parquet"):
            (ir / name).unlink(missing_ok=True)
    (ir / "graph_index.parquet").unlink(missing_ok=True)
    (ir / "graph_edges.parquet").unlink(missing_ok=True)


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


def _remap_on(raw: str | None) -> bool:
    return (raw or "").strip().lower() in {"1", "true", "yes"}


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
    return meta


@router.get(
    "/v1/context-jobs/{job_id}",
    response_model=JobBody,
    summary="Job status",
    responses={202: {"model": JobBody}, **_errors(404)},
)
async def get_job(request: Request, job_id: str) -> JSONResponse:
    ctx = _ctx(request)
    dest = ctx.store.dest_dir(job_id)
    if not dest.exists():
        raise ApiError(404, "not_found")
    live = ctx.bus.get(job_id)
    meta_path = dest / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
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
async def get_context_json(request: Request, job_id: str) -> JSONResponse:
    path = _require_artifact(request, job_id, "context.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return JSONResponse(payload)


@router.get(
    "/v1/context-jobs/{job_id}/context.md",
    response_class=PlainTextResponse,
    summary="Context Markdown",
    responses={200: _MARKDOWN, **_errors(404, 409)},
)
async def get_context_md(request: Request, job_id: str) -> PlainTextResponse:
    path = _require_artifact(request, job_id, "context.md")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown")


@router.get(
    "/v1/context-jobs/{job_id}/graph.json",
    response_model=GraphDocument,
    summary="Formula graph JSON",
    responses=_errors(404, 409),
)
async def get_graph_json(request: Request, job_id: str) -> JSONResponse:
    path = _require_artifact(request, job_id, "graph.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return JSONResponse(payload)


@router.get(
    "/v1/context-jobs/{job_id}/graph.md",
    response_class=PlainTextResponse,
    summary="Formula graph Markdown",
    responses={200: _MARKDOWN, **_errors(404, 409)},
)
async def get_graph_md(request: Request, job_id: str) -> PlainTextResponse:
    path = _require_artifact(request, job_id, "graph.md")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown")


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
    dest = _ctx(request).store.dest_dir(job_id)
    if not dest.exists():
        raise ApiError(404, "not_found")
    if not (dest / "graph.json").is_file():
        raise ApiError(409, "report_not_ready")
    from finance_context.graph.trace import trace_graph

    doc = trace_graph(dest, origin=origin, direction=direction, depth=depth)
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
    dest = _ctx(request).store.dest_dir(job_id)
    if not dest.exists():
        raise ApiError(404, "not_found")
    if not (dest / "graph.json").is_file():
        raise ApiError(409, "report_not_ready")
    from finance_context.graph.trace import trace_graph
    from finance_context.render.graph import render_trace_markdown

    doc = trace_graph(dest, origin=origin, direction=direction, depth=depth)
    return PlainTextResponse(render_trace_markdown(doc), media_type="text/markdown")


def _require_artifact(request: Request, job_id: str, name: str) -> Path:
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
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        zf = open_xlsx_zip(Path(tmp.name))
        zf.close()


