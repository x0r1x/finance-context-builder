from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from finance_context.api.errors import ApiError
from finance_context.api.http import (
    _ALLOWED,
    _LOGGER,
    _STAGE_RANK,
    _check_job_id,
    _ctx,
    _errors,
)
from finance_context.api.schemas import HealthBody, JobBody, JobListItem, ReadyBody
from finance_context.api.state import AppContext
from finance_context.app.artifacts import clear_downstream_artifacts
from finance_context.app.ids import job_id_for, sha256_bytes
from finance_context.app.jobs import list_session_jobs
from finance_context.app.pipeline import mark_job_failed
from finance_context.app.publisher import publisher_changed, publisher_matches, stale_from_meta
from finance_context.errors import ContextError
from finance_context.excel.zip_guard import open_xlsx_zip
from finance_context.models.context import JobStatus
from finance_context.observability import log_event
from finance_context.store.fs import atomic_write_bytes, file_lock, write_json

router = APIRouter()

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
