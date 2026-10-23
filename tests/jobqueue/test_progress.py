"""Redis stream progress: best-effort publish, key shape, MAXLEN, and TTL."""

from __future__ import annotations

import logging

import pytest
from redis.exceptions import RedisError

from finance_context.jobqueue.progress import ProgressPublisher, events_key
from finance_context.jobqueue.queue import redis_client
from finance_context.settings import Settings

_JOB = "ab" * 32
_LOGGER = logging.getLogger("finance_context.jobqueue")


class _Down:
    def __init__(self) -> None:
        self.calls = 0

    def xadd(self, *_args: object, **_kwargs: object) -> str:
        self.calls += 1
        raise RedisError("down")

    def expire(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("expire after a failed publish")


class _Recording:
    def __init__(self, inner: object) -> None:
        self.inner = inner
        self.kwargs: list[dict[str, object]] = []
        self.expires: list[tuple[str, int]] = []

    def xadd(self, key: str, fields: dict[str, str], **kwargs: object) -> object:
        self.kwargs.append(dict(kwargs))
        return self.inner.xadd(key, fields, **kwargs)  # type: ignore[attr-defined]

    def expire(self, key: str, ttl: int) -> object:
        self.expires.append((key, ttl))
        return self.inner.expire(key, ttl)  # type: ignore[attr-defined]


def _fields(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "session_id": "local",
        "job_id": _JOB,
        "event": "stage_start",
        "status": "running",
        "stage": "compile",
        "generation": 1,
    }
    body.update(overrides)
    return body


def test_publish_is_best_effort_and_logs_once() -> None:
    client = _Down()
    settings = Settings(_env_file=None, job_events_maxlen=4, job_events_ttl_sec=30)
    publisher = ProgressPublisher(settings, client)  # type: ignore[arg-type]
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Capture(level=logging.WARNING)
    _LOGGER.addHandler(handler)
    try:
        assert publisher.publish_now(**_fields()) is None
        assert publisher.publish_now(**_fields(event="succeeded", status="succeeded")) is None
    finally:
        _LOGGER.removeHandler(handler)
    assert client.calls == 1
    failed = [row for row in records if row.getMessage().startswith("job progress publish failed")]
    assert len(failed) == 1
    assert failed[0].event == "job_progress_unavailable"  # type: ignore[attr-defined]


def test_stream_key_maxlen_and_terminal_ttl(redis_settings: Settings) -> None:
    settings = Settings(
        _env_file=None,
        redis_url=redis_settings.redis_url,
        queue_name="finance-context",
        job_events_maxlen=4,
        job_events_ttl_sec=90,
    )
    key = events_key(settings, "local", _JOB)
    assert key == f"finance-context:events:local--{_JOB}"
    raw = redis_client(settings, socket_connect_timeout=2, socket_timeout=2)
    recorded = _Recording(raw)
    publisher = ProgressPublisher(settings, recorded)  # type: ignore[arg-type]
    try:
        first = publisher.publish_now(**_fields())
        assert first is not None
        assert raw.ttl(key) == -1
        assert recorded.expires == []
        done = publisher.publish_now(**_fields(event="succeeded", status="succeeded", stage="done"))
        assert done is not None
        ttl = raw.ttl(key)
        assert 0 < ttl <= 90
        assert recorded.expires == [(key, 90)]
        assert recorded.kwargs
        assert all(item["maxlen"] == 4 and item["approximate"] is True for item in recorded.kwargs)
        rows = raw.xrange(key)
        assert len(rows) == 2
        _entry_id, fields = rows[0]

        def _text(value: object) -> str:
            if isinstance(value, bytes):
                return value.decode()
            return str(value)

        decoded = {_text(name): _text(value) for name, value in fields.items()}
        assert decoded["event"] == "stage_start"
        assert decoded["job_id"] == _JOB
    finally:
        raw.close()


def test_events_key_rejects_a_short_job_id() -> None:
    settings = Settings(_env_file=None)
    with pytest.raises(ValueError):
        events_key(settings, "local", "not-a-job")
