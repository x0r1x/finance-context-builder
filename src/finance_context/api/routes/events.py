"""SSE view of one job. The first event is meta.json. Later events are the stream."""

from __future__ import annotations

import asyncio
import json
import re
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from finance_context.api.errors import ApiError
from finance_context.api.http import _check_job_id, _ctx, _errors
from finance_context.api.threads import run_blocking
from finance_context.jobqueue.progress import (
    TERMINAL_EVENTS,
    TERMINAL_STATUSES,
    events_key,
    read_events,
)
from finance_context.jobqueue.queue import QueueUnavailable, redis_client

router = APIRouter()

_STREAM_ID = re.compile(r"^\d+-\d+$")
_gate_lock = threading.Lock()


class _StreamGate:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.current = 0
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        with self._lock:
            if self.current >= self.limit:
                return False
            self.current += 1
            return True

    def release(self) -> None:
        with self._lock:
            self.current = max(0, self.current - 1)


def _gate(request: Request) -> _StreamGate:
    with _gate_lock:
        gate = getattr(request.app.state, "event_streams", None)
        if gate is None:
            limit = _ctx(request).settings.job_events_max_connections
            gate = _StreamGate(limit)
            request.app.state.event_streams = gate
        return gate


@router.get(
    "/v1/context-jobs/{job_id}/events",
    summary="Live job progress",
    responses=_errors(404, 503),
)
async def job_events(request: Request, job_id: str) -> StreamingResponse:
    _check_job_id(job_id)
    ctx = _ctx(request)
    dest = ctx.store.dest_dir(job_id)
    meta = await run_blocking(ctx, _read_meta, dest)
    if meta is None:
        raise ApiError(404, "not_found")
    try:
        up = await run_blocking(ctx, ctx.queue.health)
    except QueueUnavailable as exc:
        raise ApiError(
            503,
            "queue_unavailable",
            headers={"Retry-After": str(ctx.settings.job_retry_after_sec)},
        ) from exc
    if not up:
        raise ApiError(
            503,
            "queue_unavailable",
            headers={"Retry-After": str(ctx.settings.job_retry_after_sec)},
        )
    gate = _gate(request)
    if not gate.acquire():
        raise ApiError(
            503,
            "events_unavailable",
            headers={"Retry-After": str(ctx.settings.job_retry_after_sec)},
        )
    last_id = request.headers.get("last-event-id", "").strip()
    if not _STREAM_ID.fullmatch(last_id):
        last_id = "$"
    return StreamingResponse(
        _stream(request, job_id, meta, last_id, gate),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _read_meta(dest: Path) -> dict | None:
    if not dest.exists():
        return None
    path = dest / "meta.json"
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded


async def _stream(
    request: Request,
    job_id: str,
    meta: dict,
    last_id: str,
    gate: _StreamGate,
) -> AsyncIterator[str]:
    ctx = _ctx(request)
    settings = ctx.settings
    block_ms = max(1, int(settings.job_events_heartbeat_sec * 1000))
    client = redis_client(
        settings,
        socket_connect_timeout=settings.redis_connect_timeout_sec,
        socket_timeout=settings.job_events_heartbeat_sec + 5,
    )
    key = events_key(settings, settings.session_id, job_id)
    try:
        payload = _snapshot(job_id, meta)
        yield _frame("snapshot", payload)
        if payload["status"] in TERMINAL_STATUSES:
            return
        while True:
            if await request.is_disconnected():
                return
            rows = await asyncio.to_thread(read_events, client, key, last_id, block_ms)
            if not rows:
                yield ": heartbeat\n\n"
                continue
            for entry_id, fields in rows:
                last_id = entry_id
                event = fields.get("event") or "message"
                yield _frame(event, _payload(fields), entry_id)
                if event in TERMINAL_EVENTS or fields.get("status") in TERMINAL_STATUSES:
                    return
    finally:
        gate.release()
        client.close()


def _snapshot(job_id: str, meta: dict) -> dict[str, object]:
    error = meta.get("error")
    return {
        "job_id": job_id,
        "status": meta.get("status") or "queued",
        "stage": meta.get("stage") or "",
        "generation": _generation(meta.get("generation")),
        "ts": _now(),
        "error": error if isinstance(error, str) and error else None,
    }


def _payload(fields: dict[str, str]) -> dict[str, object]:
    error = fields.get("error") or ""
    return {
        "job_id": fields.get("job_id") or "",
        "status": fields.get("status") or "",
        "stage": fields.get("stage") or "",
        "generation": _generation(fields.get("generation")),
        "ts": fields.get("ts") or _now(),
        "error": error or None,
    }


def _generation(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _frame(event: str, payload: dict[str, object], entry_id: str | None = None) -> str:
    lines: list[str] = []
    if entry_id:
        lines.append(f"id: {entry_id}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(payload, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"
