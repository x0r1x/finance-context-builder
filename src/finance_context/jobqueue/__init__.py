"""Job queue behind one interface.

RQ is the implementation. A Dramatiq backend would implement the same
``JobQueue`` methods and nothing else in the API would change. There is no
path that runs a job without Redis: when Redis is down the API answers 503.
"""

from finance_context.jobqueue.queue import (
    INFLIGHT_STATUSES,
    LOST_STATUSES,
    DuplicateEnqueue,
    JobQueue,
    QueueUnavailable,
    RQJobQueue,
    job_belongs,
    redis_target,
    rq_job_id,
)

__all__ = [
    "INFLIGHT_STATUSES",
    "LOST_STATUSES",
    "DuplicateEnqueue",
    "JobQueue",
    "QueueUnavailable",
    "RQJobQueue",
    "job_belongs",
    "redis_target",
    "rq_job_id",
]
