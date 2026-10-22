from __future__ import annotations

import json
import logging

import pytest

from finance_context.jobqueue.queue import (
    DuplicateEnqueue,
    QueueUnavailable,
    RQJobQueue,
    job_belongs,
    redis_target,
    rq_job_id,
)
from finance_context.observability import JsonFormatter
from finance_context.settings import Settings

_BOOK = "ab" * 32
_OTHER = "cd" * 32


def test_job_id_keeps_a_dashed_session_from_matching_a_shorter_one() -> None:
    book = _BOOK
    assert rq_job_id("ab--cd", book) == f"ab--cd--{book}"
    assert job_belongs("ab--cd", f"ab--cd--{book}")
    assert not job_belongs("ab", f"ab--cd--{book}")
    assert job_belongs("ab", f"ab--{book}")
    assert not job_belongs("ab", f"ab--{book}--extra")
    with pytest.raises(ValueError):
        rq_job_id("local", "not-hex")


def test_redis_target_drops_the_password() -> None:
    fields = redis_target("redis://:s3cret@redis.internal:6379/2")
    assert fields == {
        "redis_scheme": "redis",
        "redis_host": "redis.internal",
        "redis_port": 6379,
        "redis_db": "2",
    }
    assert "s3cret" not in json.dumps(fields)


def test_enqueue_is_unique_and_counts_the_session(redis_settings: Settings, caplog) -> None:
    queue = RQJobQueue.from_settings(redis_settings, connect_timeout=True)
    payload = {
        "session_id": "local",
        "job_id": _BOOK,
        "data_dir": "/tmp/finance-context-test",
        "generation": 4,
    }
    caplog.set_level(logging.INFO, logger="finance_context.jobqueue")
    rq_id = queue.enqueue(payload)
    assert rq_id == f"local--{_BOOK}"
    with pytest.raises(DuplicateEnqueue):
        queue.enqueue(payload)
    assert queue.status(rq_id) == "queued"
    assert queue.session_inflight("local") == 1
    assert queue.session_inflight("other") == 0
    assert queue.session_inflight("loc") == 0
    events = [record for record in caplog.records if record.event == "job_enqueued"]
    assert len(events) == 1
    line = JsonFormatter().format(events[0])
    body = json.loads(line)
    assert body["event"] == "job_enqueued"
    assert body["job_id"] == _BOOK
    assert body["rq_job_id"] == rq_id
    assert body["generation"] == 4
    assert "redis://" not in line


def test_delete_drops_a_started_registry_entry(redis_settings: Settings) -> None:
    """A dead STARTED hash must not keep unique=True from accepting the same id."""
    from redis import Redis
    from rq.job import Job, JobStatus
    from rq.registry import StartedJobRegistry
    from rq.serializers import JSONSerializer

    queue = RQJobQueue.from_settings(redis_settings, connect_timeout=True)
    payload = {
        "session_id": "local",
        "job_id": _BOOK,
        "data_dir": "/tmp/finance-context-test",
        "generation": 1,
    }
    rq_id = queue.enqueue(payload)
    client = Redis.from_url(redis_settings.redis_url)
    job = Job.fetch(rq_id, connection=client, serializer=JSONSerializer)
    job.set_status(JobStatus.STARTED)
    registry = StartedJobRegistry(
        redis_settings.queue_name,
        connection=client,
        serializer=JSONSerializer,
    )
    client.zadd(registry.key, {f"{rq_id}:exec-stale": "+inf"})
    queue.delete(rq_id)
    assert queue.status(rq_id) is None
    raw_members = client.zrange(registry.key, 0, -1)
    members = [item.decode() if isinstance(item, bytes) else str(item) for item in raw_members]
    assert rq_id not in members
    assert all(not item.startswith(f"{rq_id}:") for item in members)
    assert queue.session_inflight("local") == 0
    assert queue.enqueue(payload) == rq_id
    assert queue.status(rq_id) == "queued"


def test_delete_lets_the_same_id_enqueue_again(redis_settings: Settings) -> None:
    queue = RQJobQueue.from_settings(redis_settings, connect_timeout=True)
    payload = {
        "session_id": "local",
        "job_id": _OTHER,
        "data_dir": "/tmp/finance-context-test",
        "generation": 1,
    }
    rq_id = queue.enqueue(payload)
    queue.request_stop(rq_id)
    assert queue.status(rq_id) == "canceled"
    queue.delete(rq_id)
    assert queue.status(rq_id) is None
    assert queue.enqueue(payload) == rq_id


def test_lost_retry_is_stored_on_the_job(redis_settings: Settings) -> None:
    from redis import Redis
    from rq.job import Job
    from rq.serializers import JSONSerializer

    queue = RQJobQueue.from_settings(redis_settings, connect_timeout=True)
    rq_id = queue.enqueue(
        {
            "session_id": "local",
            "job_id": _BOOK,
            "data_dir": "/tmp/finance-context-test",
            "generation": 1,
        }
    )
    client = Redis.from_url(redis_settings.redis_url)
    job = Job.fetch(rq_id, connection=client, serializer=JSONSerializer)
    assert job.retries_left == 1
    assert job.timeout == redis_settings.queue_job_timeout_sec()


def test_redis_down_is_unavailable() -> None:
    settings = Settings(
        _env_file=None,
        redis_url="redis://127.0.0.1:1/15",
        redis_connect_timeout_sec=0.2,
    )
    queue = RQJobQueue.from_settings(settings, connect_timeout=True)
    with pytest.raises(QueueUnavailable):
        queue.health()
    with pytest.raises(QueueUnavailable):
        queue.enqueue(
            {
                "session_id": "local",
                "job_id": _BOOK,
                "data_dir": "/tmp/finance-context-test",
                "generation": 1,
            }
        )


def test_ping_uses_the_client(redis_settings: Settings) -> None:
    queue = RQJobQueue.from_settings(redis_settings, connect_timeout=True)
    assert queue.health() is True


def test_non_local_redis_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://example.com:6379/0")
    from tests.jobqueue.conftest import local_test_redis_url

    with pytest.raises(RuntimeError, match="non-local"):
        local_test_redis_url()
