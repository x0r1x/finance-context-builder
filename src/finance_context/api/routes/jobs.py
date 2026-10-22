from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Annotated

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from finance_context.api.errors import ApiError
from finance_context.api.http import (
    _ALLOWED,
    _LOGGER,
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
from finance_context.app.shared_cache import record_rebuild_stage
from finance_context.errors import ContextError
from finance_context.excel.legacy import XlsConverter, check_legacy_workbook, source_name
from finance_context.excel.ole2 import is_ole2
from finance_context.excel.zip_guard import open_xlsx_zip
from finance_context.jobqueue.queue import (
    INFLIGHT_STATUSES,
    LOST_STATUSES,
    DuplicateEnqueue,
    QueueUnavailable,
    rq_job_id,
)
from finance_context.jobqueue.spawn import horse_owner_alive, run_lock_busy
from finance_context.models.context import JobStatus
from finance_context.observability import log_event
from finance_context.store.fs import atomic_write_bytes, file_lock, update_json, write_json

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
    writable = await asyncio.to_thread(_data_dir_writable, settings.data_dir)
    try:
        jobs = await asyncio.to_thread(ctx.queue.session_inflight, settings.session_id)
    except QueueUnavailable:
        jobs = 0
    body = {
        "status": "ready" if writable else "unavailable",
        "queue": settings.queue_name,
        "jobs": jobs,
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
    responses=_errors(400, 409, 413, 422, 429, 503),
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
    await asyncio.to_thread(
        _validate_upload, filename, data, ctx.max_upload_bytes, ctx.pipeline.converter
    )
    digest = await asyncio.to_thread(sha256_bytes, data)
    job_id = job_id_for(digest)
    # Fsync, the publisher hash, and the enqueue share one worker thread.
    body = await asyncio.to_thread(_accept_upload, ctx, filename, data, digest, job_id)
    return JSONResponse(body, status_code=202)


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


def _disk_generation(meta: dict | None) -> int:
    if not isinstance(meta, dict):
        return 0
    try:
        return int(meta.get("generation") or 0)
    except (TypeError, ValueError):
        return 0


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


def _retry_after(ctx: AppContext) -> dict[str, str]:
    return {"Retry-After": str(ctx.settings.job_retry_after_sec)}


class _JobStillRunning(Exception):
    """The previous id is still in flight. Do not enqueue over it."""


def _accept_upload(
    ctx: AppContext,
    filename: str,
    data: bytes,
    digest: str,
    job_id: str,
) -> dict[str, object]:
    """Fsync the book, hash the publisher, and enqueue. One thread.

    Dedup and stop run before ``dest/.lock``. The pipeline child holds that
    lock for the whole run, so a blocking acquire would wait until the book
    finished and could neither dedup nor cancel.
    """
    dest = ctx.store.dest_dir(job_id)
    shared = ctx.store.shared_dir(job_id)
    dest.mkdir(parents=True, exist_ok=True)
    shared.mkdir(parents=True, exist_ok=True)
    source = shared / source_name(data)
    if not source.exists():
        atomic_write_bytes(source, data)
    owner_path = dest / "owner.json"
    if not owner_path.exists():
        write_json(owner_path, {"content_sha256": digest, "source_filename": filename})
    try:
        early = _inflight_response(ctx, job_id, dest)
        if early is not None:
            return early
        with file_lock(dest / ".lock", blocking=False) as locked:
            if not locked or run_lock_busy(dest):
                return _dedup(ctx, job_id, dest)
            return _enqueue_or_reuse(ctx, filename, digest, job_id, dest, shared)
    except _JobStillRunning as exc:
        raise ApiError(
            409,
            "job_still_running",
            headers=_retry_after(ctx),
        ) from exc
    except QueueUnavailable as exc:
        raise ApiError(
            503,
            "queue_unavailable",
            headers=_retry_after(ctx),
        ) from exc


def _inflight_response(ctx: AppContext, job_id: str, dest: Path) -> dict[str, object] | None:
    """Return the current job, or None when the caller may enqueue.

    A ``started`` hash with a free run lock is abandoned after one recheck:
    the worker that would run ``on_horse_killed`` is already gone, and RQ
    keeps the hash until a later maintenance pass.
    """
    settings = ctx.settings
    rq_id = rq_job_id(settings.session_id, job_id)
    status = ctx.queue.status(rq_id)
    stored = _load_meta(dest)
    if _abandoned_started(ctx, rq_id, dest, stored, status):
        return None
    if not _still_inflight(status, dest):
        return None
    if not publisher_changed(stored):
        return _dedup(ctx, job_id, dest)
    ctx.queue.request_stop(rq_id)
    if not _wait_until_clear(ctx, rq_id, dest):
        raise _JobStillRunning
    return None


def _still_inflight(status: str | None, dest: Path) -> bool:
    return status in INFLIGHT_STATUSES or run_lock_busy(dest)


def _started_without_child(status: str | None, dest: Path, stored: dict | None) -> bool:
    if status != "started" or run_lock_busy(dest):
        return False
    if not isinstance(stored, dict):
        return False
    return stored.get("status") in {"queued", "running"}


def _abandoned_started(
    ctx: AppContext,
    rq_id: str,
    dest: Path,
    stored: dict | None,
    status: str | None,
) -> bool:
    """True when the horse that owns this generation has exited.

    ``started`` plus a free run lock is not enough. The child takes the lock
    only after spawn, and that import can outlast
    ``JOB_PARENT_WATCH_INTERVAL_SEC``. The horse holds ``.horse.lock`` on the
    shared volume for the whole call. A held lock is still starting or
    finishing. No record yet is not a dead worker. PIDs are not compared:
    the API and the worker do not share a PID namespace.
    A second status read drops a horse that just returned and left ``started``.
    Redis down on that read is not abandoned.
    """
    if not _started_without_child(status, dest, stored):
        return False
    if horse_owner_alive(dest, _disk_generation(stored)) is not False:
        return False
    try:
        again = ctx.queue.status(rq_id)
    except QueueUnavailable:
        return False
    reloaded = _load_meta(dest)
    if not _started_without_child(again, dest, reloaded):
        return False
    return horse_owner_alive(dest, _disk_generation(reloaded)) is False


def _dedup(ctx: AppContext, job_id: str, dest: Path) -> dict[str, object]:
    stored = _load_meta(dest)
    log_event(
        _LOGGER,
        logging.INFO,
        "job_dedup",
        "duplicate post dropped",
        job_id=job_id,
        session_id=ctx.settings.session_id,
        queue=ctx.settings.queue_name,
        rq_job_id=rq_job_id(ctx.settings.session_id, job_id),
        status=None if stored is None else stored.get("status"),
    )
    return _running_body(job_id, dest)


def _enqueue_or_reuse(
    ctx: AppContext,
    filename: str,
    digest: str,
    job_id: str,
    dest: Path,
    shared: Path,
) -> dict[str, object]:
    settings = ctx.settings
    queue = ctx.queue
    rq_id = rq_job_id(settings.session_id, job_id)
    status = queue.status(rq_id)
    stored = _load_meta(dest)
    # Re-check under the lock. Do not wait here: the pipeline child needs
    # this same lock, and a wait would deadlock a stop.
    if run_lock_busy(dest) or (
        status in INFLIGHT_STATUSES and not _started_without_child(status, dest, stored)
    ):
        if publisher_changed(stored):
            raise _JobStillRunning
        return _dedup(ctx, job_id, dest)
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
        return {
            "job_id": job_id,
            "status": ready.get("status"),
            "stage": ready.get("stage"),
        }
    if status is not None:
        queue.delete(rq_id)
    if queue.session_inflight(settings.session_id) >= settings.job_queue_max_per_session:
        raise ApiError(429, "too_many_jobs", headers=_retry_after(ctx))
    generation = max(1, _disk_generation(stored) + 1)
    # Session publications only. Keep meta so the stage map and generation
    # survive. Shared files stay until the child holds shared/books/{job_id}/.lock.
    shared.mkdir(parents=True, exist_ok=True)
    stale = stale_from_meta(stored)
    clear_downstream_artifacts(dest, stale_from=stale, drop_meta=False)
    record_rebuild_stage(dest, stale, generation=generation)
    if stale is not None:
        log_event(
            _LOGGER,
            logging.INFO,
            "snapshot_invalidated",
            "stale session artifacts invalidated",
            job_id=job_id,
            stale_from=stale,
        )

    def stamp(current: dict) -> dict:
        updated = dict(current)
        updated["job_id"] = job_id
        updated["source_filename"] = filename
        updated["content_sha256"] = digest
        updated["generation"] = generation
        updated["status"] = "queued"
        updated["stage"] = "queued"
        updated.pop("error", None)
        updated.pop("detail", None)
        return updated

    update_json(dest / "meta.json", stamp)
    payload = {
        "session_id": settings.session_id,
        "job_id": job_id,
        "data_dir": str(settings.data_dir),
        "generation": generation,
    }
    try:
        queue.enqueue(payload)
    except DuplicateEnqueue:
        log_event(
            _LOGGER,
            logging.INFO,
            "job_dedup",
            "duplicate post dropped",
            job_id=job_id,
            session_id=settings.session_id,
            queue=settings.queue_name,
            rq_job_id=rq_id,
        )
        return _running_body(job_id, dest)
    return {"job_id": job_id, "status": "queued", "stage": "queued"}


def _wait_until_clear(ctx: AppContext, rq_id: str, dest: Path) -> bool:
    """True when the id is gone and the run lock is free.

    False when the grace expires and the id is still in flight. The caller
    must not delete that id or enqueue another generation over it.
    """
    settings = ctx.settings
    deadline = time.monotonic() + settings.job_terminate_grace_sec + settings.job_kill_grace_sec
    while True:
        status = ctx.queue.status(rq_id)
        if status not in INFLIGHT_STATUSES and not run_lock_busy(dest):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _queue_lost(ctx: AppContext, job_id: str, dest: Path, meta: dict) -> bool:
    if meta.get("status") not in {"queued", "running"}:
        return False
    if run_lock_busy(dest):
        return False
    rq_id = rq_job_id(ctx.settings.session_id, job_id)
    try:
        status = ctx.queue.status(rq_id)
    except QueueUnavailable:
        return False
    if status in LOST_STATUSES:
        return True
    return _abandoned_started(ctx, rq_id, dest, meta, status)


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
    # Redis and the job directory stay off the event loop. A slow Redis must
    # not stall readyz or every other request on this process.
    meta = await asyncio.to_thread(_read_job, ctx, job_id)
    if meta is None:
        raise ApiError(404, "not_found")
    # A missing meta.json during a live run is still working, same as 202 on POST.
    code = 202 if meta.pop("_inflight_without_meta", None) else 200
    return JSONResponse(_job_body(job_id, meta), status_code=code)


def _read_job(ctx: AppContext, job_id: str) -> dict | None:
    dest = ctx.store.dest_dir(job_id)
    if not dest.exists():
        return None
    meta = _load_meta(dest)
    if meta is None:
        # The child unlinks meta.json while it rebuilds. A missing or
        # unreadable file is the live run, not an HTTP 500 and not a loss.
        return _inflight_without_meta(ctx, job_id, dest)
    # The lost probe stays outside dest/.lock. The pipeline child holds that
    # lock for the whole run, and the write below must not wait on it.
    if not _queue_lost(ctx, job_id, dest, meta):
        return meta
    # POST writes queued meta and enqueues under this same lock. Marking
    # lost in that window would report failed for a job that is about to run.
    # A busy lock means that window, or the pipeline, is in progress.
    with file_lock(dest / ".lock", blocking=False) as locked:
        if not locked:
            return _load_meta(dest) or meta
        current = _load_meta(dest)
        if current is None or not _lost_now(ctx, job_id, dest, current):
            return current
        generation = _disk_generation(current)
        mark_job_failed(dest, job_id, "queue_lost", generation)
        log_event(
            _LOGGER,
            logging.WARNING,
            "job_queue_lost",
            "queued job is missing from the queue",
            job_id=job_id,
            session_id=ctx.settings.session_id,
            queue=ctx.settings.queue_name,
            generation=generation,
            error_code="queue_lost",
        )
        reloaded = _load_meta(dest)
        return reloaded if reloaded is not None else current


def _inflight_without_meta(ctx: AppContext, job_id: str, dest: Path) -> dict | None:
    """A live queue job whose meta.json is gone for a moment.

    ``scheduled`` and ``deferred`` are the shared-cache wait. They stay
    queued. Redis down is a 404, not a 500 and not ``queue_lost``.
    """
    running = run_lock_busy(dest)
    try:
        status = ctx.queue.status(rq_job_id(ctx.settings.session_id, job_id))
    except QueueUnavailable:
        status = "started" if running else None
    if not running and status not in INFLIGHT_STATUSES:
        return None
    live = "running" if running or status == "started" else "queued"
    return {
        "job_id": job_id,
        "status": live,
        "stage": live,
        "_inflight_without_meta": True,
    }


def _lost_now(ctx: AppContext, job_id: str, dest: Path, meta: dict) -> bool:
    """Same as ``_queue_lost`` without the abandoned-job pause.

    The caller already waited once. This is the check under ``dest/.lock``.
    """
    if meta.get("status") not in {"queued", "running"}:
        return False
    if run_lock_busy(dest):
        return False
    try:
        status = ctx.queue.status(rq_job_id(ctx.settings.session_id, job_id))
    except QueueUnavailable:
        return False
    if status in LOST_STATUSES:
        return True
    # A started job is lost only when its horse lock is free. A held lock
    # means the horse is still importing the child. The check is a flock on
    # the shared volume, not a PID in this container.
    return (
        _started_without_child(status, dest, meta)
        and horse_owner_alive(dest, _disk_generation(meta)) is False
    )


# Short sentences this process writes itself. Converter stderr and workbook
# paths stay in the log and in meta.json, not in the job response.
_PUBLIC_DETAIL = frozenset({"workbook_too_large"})


def _job_body(job_id: str, meta: dict) -> dict:
    body = {
        "job_id": job_id,
        "status": meta.get("status"),
        "stage": meta.get("stage"),
        "warnings": meta.get("warnings") or [],
        "error": meta.get("error"),
    }
    detail = meta.get("detail")
    if detail and meta.get("error") in _PUBLIC_DETAIL:
        body["detail"] = detail
    if meta.get("status") not in {"queued", "running", None}:
        body["context_json_url"] = f"/v1/context-jobs/{job_id}/context.json"
        body["context_md_url"] = f"/v1/context-jobs/{job_id}/context.md"
        body["graph_json_url"] = f"/v1/context-jobs/{job_id}/graph.json"
        body["graph_md_url"] = f"/v1/context-jobs/{job_id}/graph.md"
    return body


def _validate_upload(filename: str, data: bytes, max_bytes: int, converter: XlsConverter) -> None:
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED:
        raise ContextError("unsupported_media_type", suffix or "missing")
    if not data:
        raise ContextError("empty_file")
    if len(data) > max_bytes:
        raise ContextError("file_too_large")
    legacy = is_ole2(data)
    if suffix == ".xls" and not legacy and not data.startswith(b"PK"):
        raise ContextError("unsupported_media_type", "not an Excel workbook")
    with NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        if legacy:
            # Converted in the job process; refuse here what cannot be converted.
            check_legacy_workbook(Path(tmp.name), converter)
            return
        zf = open_xlsx_zip(Path(tmp.name))
        zf.close()
