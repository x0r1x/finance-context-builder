"""Best-effort job progress on a Redis stream.

``meta.json`` stays the source of truth. A Redis error is logged once and
later publishes in that process are skipped. The job itself is not failed.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from finance_context.jobqueue.queue import rq_job_id
from finance_context.observability import log_event
from finance_context.settings import Settings

_LOGGER = logging.getLogger("finance_context.jobqueue")

TERMINAL_EVENTS = frozenset({"succeeded", "failed", "queue_lost"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "degraded", "needs_input"})

_progress_enabled: ContextVar[bool] = ContextVar("finance_context_job_events", default=False)
_publishers: dict[str, ProgressPublisher] = {}
_publishers_lock = threading.Lock()


def progress_enabled() -> bool:
    return _progress_enabled.get()


def enable_progress() -> Any:
    return _progress_enabled.set(True)


def reset_progress(token: Any) -> None:
    _progress_enabled.reset(token)


def events_key(settings: Settings, session_id: str, job_id: str) -> str:
    """``{queue}:events:{SESSION_ID}--{job_id}``, the same id shape as RQ."""
    return f"{settings.queue_name}:events:{rq_job_id(session_id, job_id)}"


class ProgressPublisher:
    """One stream writer. ``publish_now`` is synchronous. ``emit`` does not wait."""

    def __init__(self, settings: Settings, client: Redis | None = None) -> None:
        self._settings = settings
        self._client = client
        self._warned = False
        self._pending: queue.SimpleQueue[dict[str, object]] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def emit(self, **fields: object) -> None:
        if self._warned:
            return
        self._start()
        self._pending.put(fields)

    def publish_now(self, **fields: object) -> str | None:
        return self._send(fields)

    def _start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._loop,
                name="job-progress",
                daemon=True,
            )
            self._thread.start()

    def _loop(self) -> None:
        while True:
            self._send(self._pending.get())

    def _send(self, fields: Mapping[str, object]) -> str | None:
        if self._warned:
            return None
        try:
            client = self._redis()
            key = events_key(
                self._settings,
                str(fields["session_id"]),
                str(fields["job_id"]),
            )
            body = _stream_fields(fields)
            entry = client.xadd(
                key,
                body,
                maxlen=self._settings.job_events_maxlen,
                approximate=True,
            )
            if str(fields.get("event")) in TERMINAL_EVENTS:
                client.expire(key, int(self._settings.job_events_ttl_sec))
        except (RedisError, OSError, TimeoutError):
            self._warned = True
            log_event(
                _LOGGER,
                logging.WARNING,
                "job_progress_unavailable",
                "job progress publish failed; further events are skipped",
            )
            return None
        return _text(entry)

    def _redis(self) -> Redis:
        if self._client is None:
            self._client = _client_for(self._settings)
        return self._client


def emit_progress(settings: Settings, **fields: object) -> None:
    """Queue one event. Returns immediately. Redis errors never escape."""
    _publisher_for(settings).emit(**fields)


def publish_progress(
    settings: Settings,
    *,
    client: Redis | None = None,
    **fields: object,
) -> str | None:
    """Write one event and return its stream id. Used by tests and resume."""
    if client is not None:
        return ProgressPublisher(settings, client).publish_now(**fields)
    return _publisher_for(settings).publish_now(**fields)


def read_events(
    client: Redis,
    key: str,
    last_id: str,
    block_ms: int,
) -> list[tuple[str, dict[str, str]]]:
    """Entries strictly after ``last_id``. ``$`` waits for new ones. Empty on timeout."""
    try:
        found = client.xread({key: last_id}, count=50, block=block_ms)
    except (RedisError, OSError, TimeoutError):
        return []
    rows: list[tuple[str, dict[str, str]]] = []
    for _key, entries in found or []:
        del _key
        for entry_id, raw in entries:
            fields = {_text(key): _text(value) for key, value in raw.items()}
            rows.append((_text(entry_id), fields))
    return rows


def _publisher_for(settings: Settings) -> ProgressPublisher:
    url, _credentials = settings.redis_from_url_args()
    with _publishers_lock:
        found = _publishers.get(url)
        if found is None:
            found = ProgressPublisher(settings)
            _publishers[url] = found
        return found


def _client_for(settings: Settings) -> Redis:
    url, credentials = settings.redis_from_url_args()
    # One short attempt. After a failure the publisher stops calling Redis.
    return Redis.from_url(
        url,
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
        **credentials,
    )


def _stream_fields(fields: Mapping[str, object]) -> dict[str, str]:
    error = fields.get("error")
    return {
        "event": str(fields.get("event") or ""),
        "job_id": str(fields["job_id"]),
        "status": str(fields.get("status") or ""),
        "stage": str(fields.get("stage") or ""),
        "generation": str(int(fields.get("generation") or 0)),
        "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "error": "" if error is None else str(error),
    }


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)
