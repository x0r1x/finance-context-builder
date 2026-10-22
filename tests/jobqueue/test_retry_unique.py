"""rq#2499: enqueue_in/enqueue_at ignore unique in RQ 2.12.

A delayed requeue has to return ``Retry(interval=...)``. That schedules the
same job object. ``enqueue_in`` would create a second id.
"""

from __future__ import annotations

from redis import Redis
from rq import Queue, Retry, SimpleWorker
from rq.job import Job, JobStatus
from rq.serializers import JSONSerializer

from finance_context.settings import Settings

_BOOK = "ef" * 32


def _defer() -> Retry:
    return Retry(max=3, interval=60)


def test_retry_interval_does_not_create_a_second_job(redis_settings: Settings) -> None:
    client = Redis.from_url(
        redis_settings.redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    queue = Queue(
        redis_settings.queue_name,
        connection=client,
        serializer=JSONSerializer,
    )
    job_id = f"local--{_BOOK}"
    queue.enqueue(
        "tests.jobqueue.test_retry_unique._defer",
        job_id=job_id,
        unique=True,
        result_ttl=500,
    )
    worker = SimpleWorker([queue], connection=client, serializer=JSONSerializer)
    worker.work(burst=True, with_scheduler=True)
    job = Job.fetch(job_id, connection=client, serializer=JSONSerializer)
    assert job.get_status() == JobStatus.SCHEDULED
    keys = sorted(client.scan_iter("rq:job:*"))
    assert keys == [f"rq:job:{job_id}".encode()]
    client.close()
