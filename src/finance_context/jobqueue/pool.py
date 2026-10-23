"""The worker pool. ``finance-context worker`` starts it.

``finance-context serve`` starts that same command as a child process.
"""

from __future__ import annotations

import logging
import os

from rq import Worker
from rq.serializers import JSONSerializer
from rq.worker_pool import WorkerPool

from finance_context.jobqueue.jobs import on_horse_killed
from finance_context.jobqueue.parents import HORSE_ENV
from finance_context.jobqueue.queue import QueueUnavailable, RQJobQueue, redis_client, redis_target
from finance_context.memory import budget_for_settings, prefer_oom_kill
from finance_context.observability import log_event
from finance_context.settings import Settings
from finance_context.store.filesystem import DataDirNotLocalError, ensure_data_dir_local

_LOGGER = logging.getLogger("finance_context.jobqueue")


class ContextWorker(Worker):
    """Forking worker that records a lost horse on the book, not via an exc handler.

    ``WorkerPool.run_worker`` does not forward ``work_horse_killed_handler``,
    so the subclass sets it. The horse env is published only around the fork
    and removed in the parent, so the worker process itself never looks like
    a horse.
    """

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._work_horse_killed_handler = on_horse_killed

    def fork_work_horse(self, job: object, queue: object) -> None:
        os.environ[HORSE_ENV] = "1"
        try:
            super().fork_work_horse(job, queue)  # type: ignore[arg-type]
        finally:
            os.environ.pop(HORSE_ENV, None)


def serve_workers(settings: Settings) -> None:
    """Block in a pool of ``MAX_CONCURRENT_JOBS`` workers, scheduler included.

    The pool's ``start`` always runs each worker with ``with_scheduler=True``.
    The worker and the horse ask to be OOM-killed last (score 0). The spawn
    child still sets ``JOB_OOM_SCORE_ADJ``.
    """
    prefer_oom_kill(score=0)
    _log_queue_config(settings)
    connection = redis_client(settings)
    pool = WorkerPool(
        [settings.queue_name],
        connection=connection,
        num_workers=max(1, settings.max_concurrent_jobs),
        worker_class=ContextWorker,
        serializer=JSONSerializer,
    )
    pool.start(burst=False, logging_level=settings.log_level)


def worker_check(settings: Settings) -> int:
    """Exit 0 when ``DATA_DIR`` is local and Redis answers. Used by the healthcheck.

    This does not prove a long-running worker is alive. It proves this host
    can see the data directory and the queue.
    """
    try:
        ensure_data_dir_local(
            settings.data_dir,
            allow_network=settings.data_dir_allow_network_fs,
        )
    except DataDirNotLocalError:
        return 1
    try:
        ready = RQJobQueue.from_settings(settings, connect_timeout=True).health()
    except QueueUnavailable:
        return 1
    return 0 if ready else 1


def _log_queue_config(settings: Settings) -> None:
    budget = budget_for_settings(settings)
    log_event(
        _LOGGER,
        logging.INFO,
        "queue_config",
        "effective queue settings",
        queue=settings.queue_name,
        slots=settings.max_concurrent_jobs,
        budget_bytes=budget,
        timeout_sec=settings.queue_job_timeout_sec(),
        margin_sec=settings.queue_job_timeout_margin_sec,
        ttl_sec=settings.queue_job_ttl_sec,
        result_ttl_sec=settings.queue_result_ttl_sec,
        failure_ttl_sec=settings.queue_failure_ttl_sec,
        **redis_target(settings.redis_url),
    )
