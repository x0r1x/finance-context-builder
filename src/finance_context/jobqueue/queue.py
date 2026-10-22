"""RQ adapter. Callers talk to ``JobQueue`` and do not import RQ."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Protocol, TypeVar
from urllib.parse import urlsplit

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Retry
from rq.command import send_stop_job_command
from rq.exceptions import DuplicateJobError, InvalidJobOperation, NoSuchJobError
from rq.job import Job, JobStatus, parse_job_id
from rq.registry import DeferredJobRegistry, ScheduledJobRegistry, StartedJobRegistry
from rq.serializers import JSONSerializer

from finance_context.observability import log_event
from finance_context.settings import Settings

_LOGGER = logging.getLogger("finance_context.jobqueue")
_T = TypeVar("_T")

_BOOK_ID = re.compile(r"^[0-9a-f]{64}$")
_EXECUTE = "finance_context.jobqueue.jobs.execute_job"

# Queued, running, or waiting for a delayed retry. These hold a session slot.
INFLIGHT_STATUSES = frozenset(
    {
        JobStatus.QUEUED.value,
        JobStatus.STARTED.value,
        JobStatus.DEFERRED.value,
        JobStatus.SCHEDULED.value,
    }
)
# meta.json is queued or running, the run lock is free, and RQ looks like this:
# the job is gone. ``stopped`` is not here: the horse may still be exiting.
LOST_STATUSES = frozenset(
    {
        None,
        JobStatus.FAILED.value,
        JobStatus.CANCELED.value,
        JobStatus.FINISHED.value,
    }
)


class QueueUnavailable(Exception):
    """Redis cannot be reached. The API maps this to 503."""


class DuplicateEnqueue(Exception):
    """A job with this id is already in Redis. The caller decides dedup or replace."""

    def __init__(self, rq_id: str) -> None:
        super().__init__(rq_id)
        self.rq_id = rq_id


class JobQueue(Protocol):
    def enqueue(self, payload: dict[str, object]) -> str: ...

    def status(self, rq_id: str) -> str | None: ...

    def session_inflight(self, session_id: str) -> int: ...

    def request_stop(self, rq_id: str) -> None: ...

    def delete(self, rq_id: str) -> None: ...

    def health(self) -> bool: ...


def rq_job_id(session_id: str, job_id: str) -> str:
    """``{SESSION_ID}--{job_id}``. The book id is 64 hex characters."""
    if _BOOK_ID.fullmatch(job_id) is None:
        raise ValueError("job_id must be 64 hex characters")
    return f"{session_id}--{job_id}"


def job_belongs(session_id: str, rq_id: str) -> bool:
    """True when ``rq_id`` is this session's book.

    A session may itself contain ``--``, so a prefix check is not enough.
    The remainder after ``{session}--`` has to be exactly 64 hex characters.
    """
    prefix = f"{session_id}--"
    if not rq_id.startswith(prefix):
        return False
    return _BOOK_ID.fullmatch(rq_id[len(prefix) :]) is not None


def redis_target(url: str) -> dict[str, object]:
    """Scheme, host, port, and db. The userinfo, which may be a password, is dropped."""
    parts = urlsplit(url)
    if parts.scheme == "unix":
        return {"redis_scheme": "unix", "path": parts.path}
    db = parts.path.lstrip("/")
    fields: dict[str, object] = {
        "redis_scheme": parts.scheme,
        "redis_host": parts.hostname,
        "redis_port": parts.port,
    }
    if db:
        fields["redis_db"] = db
    return fields


class RQJobQueue:
    """One shared RQ queue. The session lives in the job id and the payload."""

    def __init__(self, settings: Settings, *, timeout: float | None = None) -> None:
        self._settings = settings
        self._serializer = JSONSerializer
        kwargs: dict[str, float] = {}
        if timeout is not None:
            kwargs["socket_connect_timeout"] = timeout
            kwargs["socket_timeout"] = timeout
        self._redis = Redis.from_url(settings.redis_url, **kwargs)
        self._queue = Queue(
            settings.queue_name,
            connection=self._redis,
            serializer=self._serializer,
        )

    @classmethod
    def from_settings(cls, settings: Settings, *, connect_timeout: bool = False) -> RQJobQueue:
        timeout = settings.redis_connect_timeout_sec if connect_timeout else None
        return cls(settings, timeout=timeout)

    def enqueue(self, payload: dict[str, object]) -> str:
        session_id = str(payload["session_id"])
        job_id = str(payload["job_id"])
        rq_id = rq_job_id(session_id, job_id)
        settings = self._settings
        retry = None
        if settings.job_retry_on_lost > 0:
            # interval 0 requeues the same job object. enqueue_in drops unique (rq#2499).
            retry = Retry(max=settings.job_retry_on_lost, interval=0)
        ttl = settings.queue_job_ttl_sec or None

        def _enqueue() -> Job:
            return self._queue.enqueue(
                _EXECUTE,
                payload,
                job_id=rq_id,
                unique=True,
                job_timeout=settings.queue_job_timeout_sec(),
                result_ttl=settings.queue_result_ttl_sec,
                failure_ttl=settings.queue_failure_ttl_sec,
                ttl=ttl,
                retry=retry,
            )

        try:
            job = self._guard(_enqueue)
        except DuplicateJobError as exc:
            raise DuplicateEnqueue(rq_id) from exc
        log_event(
            _LOGGER,
            logging.INFO,
            "job_enqueued",
            "job enqueued",
            job_id=job_id,
            rq_job_id=job.id,
            session_id=session_id,
            queue=settings.queue_name,
            generation=payload.get("generation"),
        )
        return job.id

    def status(self, rq_id: str) -> str | None:
        def _status() -> str | None:
            try:
                job = Job.fetch(rq_id, connection=self._redis, serializer=self._serializer)
            except (NoSuchJobError, InvalidJobOperation):
                return None
            current = job.get_status()
            return str(current.value)

        return self._guard(_status)

    def session_inflight(self, session_id: str) -> int:
        def _count() -> int:
            ids: set[str] = set()
            for raw in self._queue.get_job_ids():
                ids.add(_text(raw))
            for registry_cls in (
                StartedJobRegistry,
                ScheduledJobRegistry,
                DeferredJobRegistry,
            ):
                registry = registry_cls(
                    name=self._queue.name,
                    connection=self._redis,
                    serializer=self._serializer,
                )
                for raw in registry.get_job_ids(cleanup=False):
                    ids.add(parse_job_id(_text(raw)))
            return sum(1 for rq_id in ids if job_belongs(session_id, rq_id))

        return self._guard(_count)

    def request_stop(self, rq_id: str) -> None:
        """Cancel a queued job, or tell the worker to stop a started one."""

        def _stop() -> None:
            current = self.status(rq_id)
            if current is None:
                return
            if current == JobStatus.STARTED:
                try:
                    send_stop_job_command(self._redis, rq_id, serializer=self._serializer)
                except (InvalidJobOperation, NoSuchJobError):
                    return
                return
            if current not in {
                JobStatus.QUEUED,
                JobStatus.SCHEDULED,
                JobStatus.DEFERRED,
                JobStatus.CREATED,
            }:
                return
            try:
                job = Job.fetch(rq_id, connection=self._redis, serializer=self._serializer)
                job.cancel()
            except (InvalidJobOperation, NoSuchJobError):
                return

        self._guard(_stop)

    def delete(self, rq_id: str) -> None:
        """Drop the Redis record so unique=True does not block a real rerun."""

        def _delete() -> None:
            try:
                job = Job.fetch(rq_id, connection=self._redis, serializer=self._serializer)
            except NoSuchJobError:
                return
            job.delete()

        self._guard(_delete)

    def health(self) -> bool:
        def _ping() -> bool:
            return bool(self._redis.ping())

        return self._guard(_ping)

    def _guard(self, fn: Callable[[], _T]) -> _T:
        try:
            return fn()
        except DuplicateJobError:
            raise
        except (RedisError, TimeoutError, ConnectionError, OSError) as exc:
            raise QueueUnavailable("Redis is unavailable") from exc


def _text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)
