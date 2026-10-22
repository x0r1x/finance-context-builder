"""The RQ job. The horse preflights, then spawns the existing pipeline child."""

from __future__ import annotations

import logging
import os
import signal
from pathlib import Path

from redis.exceptions import RedisError
from rq import Retry, get_current_job
from rq.job import Job

from finance_context.app.pipeline import mark_job_failed
from finance_context.jobqueue.parents import HORSE_ENV, arm_horse_death
from finance_context.jobqueue.spawn import (
    ChildResult,
    hold_horse_lock,
    read_meta,
    release_horse_record,
    run_child,
    spawn_child,
    watch_child,
    write_horse_owner,
)
from finance_context.memory import (
    admission_blocked,
    budget_for_settings,
    cgroup_memory_anon_bytes,
    cgroup_memory_limit_bytes,
    cgroup_oom_kill_count,
    over_rss_detail,
    prefer_oom_kill,
    unusable_cap_detail,
)
from finance_context.observability import log_event
from finance_context.settings import Settings
from finance_context.store.fs import update_json
from finance_context.store.paths import session_job_dir

_LOGGER = logging.getLogger("finance_context.jobqueue")
_PIPELINE = logging.getLogger("finance_context.pipeline")

_TERMINAL = {"succeeded", "degraded", "needs_input", "failed"}
_BUSY_RETRIES = "shared_cache_busy_retries"
_BUSY_DETAIL = "shared book cache stayed busy"


def execute_job(payload: dict[str, object]) -> Retry | None:
    """Run one book. Return ``Retry`` only to release the worker and try later.

    Pipeline errors are recorded by the spawn child and do not come back here,
    so they do not consume ``JOB_RETRY_ON_LOST``. A lost horse is handled by
    ``on_horse_killed``, not by an exception handler.
    """
    if not isinstance(payload, dict):
        raise TypeError("job payload must be a dict")
    settings = Settings()
    if os.environ.get(HORSE_ENV) == "1":
        arm_horse_death(settings.job_parent_watch_interval_sec)
        prefer_oom_kill(score=0)
    session_id = str(payload["session_id"])
    job_id = str(payload["job_id"])
    data_dir = str(payload["data_dir"])
    generation = int(payload["generation"])  # type: ignore[arg-type]
    current = get_current_job()
    oom_before = cgroup_oom_kill_count()
    if current is not None:
        current.meta["oom_kill_before"] = oom_before
        current.meta["session_id"] = session_id
        current.meta["book_job_id"] = job_id
        current.meta["data_dir"] = data_dir
        current.meta["generation"] = generation
        current.save_meta()
    log_event(
        _LOGGER,
        logging.INFO,
        "job_dequeued",
        "job dequeued",
        job_id=job_id,
        session_id=session_id,
        queue=settings.queue_name,
        generation=generation,
        rq_job_id=None if current is None else current.id,
    )
    dest = session_job_dir(Path(data_dir), job_id, session_id)
    # The API lives in another container, so a PID check cannot see this horse.
    # The lock is on the shared volume and is held through every return,
    # including an admission retry. A kill drops it; the kernel releases flocks.
    with hold_horse_lock(dest):
        write_horse_owner(dest, generation)
        budget = budget_for_settings(settings)
        if _admission_waiting(settings, budget):
            release_horse_record(dest)
            interval = max(1, int(settings.job_admission_retry_sec))
            seen = 0
            if current is not None and current.number_of_retries:
                seen = int(current.number_of_retries)
            log_event(
                _LOGGER,
                logging.INFO,
                "job_requeued",
                "job requeued: cgroup has no room",
                job_id=job_id,
                session_id=session_id,
                queue=settings.queue_name,
                generation=generation,
                retry_in_sec=interval,
                reason="admission",
                budget_bytes=budget,
            )
            # max grows with each deferral. Admission has no product cap.
            # interval > 0 schedules this same job (rq#2499: enqueue_in drops unique).
            return Retry(max=seen + 1, interval=interval)
        detail = unusable_cap_detail(settings)
        if detail:
            mark_job_failed(dest, job_id, "workbook_too_large", generation, detail=detail)
            return None
        proc = spawn_child(
            run_child,
            (data_dir, job_id, generation, session_id, os.getpid()),
            malloc_arena_max=settings.job_malloc_arena_max,
            name=f"finance-job-{job_id[:8]}",
        )
        result = watch_child(
            proc,
            timeout_sec=settings.job_timeout_sec,
            budget_bytes=budget,
            watch_interval_sec=settings.job_memory_watch_interval_sec,
            terminate_grace_sec=settings.job_terminate_grace_sec,
            kill_grace_sec=settings.job_kill_grace_sec,
        )
        if result.shared_cache_busy:
            # Same job object, interval > 0. enqueue_in/enqueue_at ignore unique (rq#2499).
            return _requeue_shared_cache(settings, dest, job_id, session_id, generation, current)
        return _finish(result, dest, job_id, generation, budget, current)


def _requeue_shared_cache(
    settings: Settings,
    dest: Path,
    job_id: str,
    session_id: str,
    generation: int,
    current: Job | None,
) -> Retry | None:
    """Release the slot while another session holds the shared book.

    The product cap lives in meta, not in ``Retry.max``. RQ marks the Redis
    job failed when ``number_of_retries >= retry.max`` without calling this
    function again, and admission retries share that counter. Growing ``max``
    by one keeps the job scheduled. ``meta.json`` stays queued until the cap
    is spent, then ``shared_cache_busy`` is written here.
    """
    cap = int(settings.shared_cache_busy_max_retries)
    seen = _busy_seen(read_meta(dest / "meta.json"))
    if seen >= cap:
        mark_job_failed(dest, job_id, "shared_cache_busy", generation, detail=_BUSY_DETAIL)
        log_event(
            _LOGGER,
            logging.WARNING,
            "job_failed",
            f"shared_cache_busy: {_BUSY_DETAIL}",
            job_id=job_id,
            session_id=session_id,
            queue=settings.queue_name,
            generation=generation,
            error_code="shared_cache_busy",
            reason=_BUSY_DETAIL,
            attempt=seen,
            max_retries=cap,
        )
        return None
    release_horse_record(dest)
    configured = float(settings.shared_cache_busy_retry_sec)
    interval = max(1, int(configured))
    attempt = seen + 1

    def stamp(document: dict) -> dict:
        updated = dict(document)
        updated[_BUSY_RETRIES] = attempt
        return updated

    update_json(dest / "meta.json", stamp)
    log_event(
        _PIPELINE,
        logging.INFO,
        "shared_cache_busy_requeue",
        "shared cache busy",
        job_id=job_id,
        attempt=attempt,
        retry_after_sec=configured,
        max_retries=cap,
    )
    log_event(
        _LOGGER,
        logging.INFO,
        "job_requeued",
        "job requeued: shared cache busy",
        job_id=job_id,
        session_id=session_id,
        queue=settings.queue_name,
        generation=generation,
        retry_in_sec=interval,
        reason="shared_cache_busy",
        attempt=attempt,
        max_retries=cap,
    )
    rq_seen = 0
    if current is not None and current.number_of_retries:
        rq_seen = int(current.number_of_retries)
    return Retry(max=rq_seen + 1, interval=interval)


def _busy_seen(meta: dict) -> int:
    raw = meta.get(_BUSY_RETRIES)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return 0
    return raw


def on_horse_killed(job: Job, retpid: int, ret_val: int, rusage: object) -> None:
    """Write ``oom_killed`` or ``process_lost`` before RQ decides on a retry.

    RQ calls this from ``monitor_work_horse`` and then ``handle_job_failure``.
    ``ret_val`` is the ``os.wait4`` status. An OOM is that status reporting
    SIGKILL and the cgroup ``oom_kill`` counter moving; that zeroes
    ``retries_left``. Any other death keeps the ``JOB_RETRY_ON_LOST`` retry
    and leaves ``meta.json`` unchanged while ``retries_left`` is still
    positive. ``process_lost`` is written only when that retry is already
    spent. A stopped job does not come through here.
    """
    del retpid, rusage
    # The horse saved oom_kill_before after the fork. The parent's Job.meta
    # is still the pre-fork copy, so an OOM would look like a plain lost worker.
    meta = _refreshed_meta(job)
    before = meta.get("oom_kill_before")
    after = cgroup_oom_kill_count()
    oom = (
        _sigkill_wait_status(ret_val)
        and isinstance(before, int)
        and after is not None
        and after > before
    )
    if oom:
        job.retries_left = 0
        _save(job)
        _mark_from_meta(meta, "oom_killed")
        log_event(
            _LOGGER,
            logging.WARNING,
            "job_lost",
            "work horse killed: oom",
            job_id=meta.get("book_job_id"),
            session_id=meta.get("session_id"),
            rq_job_id=getattr(job, "id", None),
            generation=meta.get("generation"),
            error_code="oom_killed",
            oom_kill=after,
        )
        return
    remaining = getattr(job, "retries_left", None)
    if isinstance(remaining, int) and remaining > 0:
        # RQ requeues this same generation after the handler returns.
        # A terminal process_lost here would show failed while that retry runs.
        log_event(
            _LOGGER,
            logging.INFO,
            "job_requeued",
            "work horse killed: retry remains",
            job_id=meta.get("book_job_id"),
            session_id=meta.get("session_id"),
            rq_job_id=getattr(job, "id", None),
            generation=meta.get("generation"),
            reason="worker_lost",
            retry_in_sec=0,
        )
        return
    _mark_from_meta(meta, "process_lost")
    log_event(
        _LOGGER,
        logging.WARNING,
        "job_lost",
        "work horse killed",
        job_id=meta.get("book_job_id"),
        session_id=meta.get("session_id"),
        rq_job_id=getattr(job, "id", None),
        generation=meta.get("generation"),
        error_code="process_lost",
    )


def _refreshed_meta(job: Job) -> dict:
    """Meta from Redis when this Job can reload it, else the in-memory copy."""
    refresh = getattr(job, "get_meta", None)
    if callable(refresh):
        try:
            loaded = refresh(refresh=True)
        except (RedisError, OSError, TypeError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            return loaded
    meta = getattr(job, "meta", None)
    return meta if isinstance(meta, dict) else {}


def _sigkill_wait_status(ret_val: object) -> bool:
    """True when an ``os.wait4`` status is SIGKILL.

    RQ passes that status through, not the bare signal number. A normal
    non-zero exit and SIGTERM both stay false.
    """
    if isinstance(ret_val, bool) or not isinstance(ret_val, int) or ret_val == 0:
        return False
    try:
        return bool(os.WIFSIGNALED(ret_val) and os.WTERMSIG(ret_val) == signal.SIGKILL)
    except (OSError, OverflowError, ValueError):
        return False


def _admission_waiting(settings: Settings, budget: int) -> bool:
    if not settings.job_admission_cgroup_check:
        return False
    return admission_blocked(
        enabled=True,
        limit_bytes=cgroup_memory_limit_bytes(),
        anon_bytes=cgroup_memory_anon_bytes(),
        budget_bytes=budget,
    )


def _finish(
    result: ChildResult,
    dest: Path,
    job_id: str,
    generation: int,
    budget: int,
    current: Job | None,
) -> None:
    if result.kernel_oom:
        if current is not None:
            current.retries_left = 0
            _save(current)
        mark_job_failed(dest, job_id, "oom_killed", generation)
        log_event(
            _LOGGER,
            logging.WARNING,
            "job_lost",
            "spawn child oom-killed",
            job_id=job_id,
            generation=generation,
            error_code="oom_killed",
        )
        return
    if result.over_budget:
        detail = over_rss_detail(result.rss_bytes, int(budget))
        mark_job_failed(dest, job_id, "workbook_too_large", generation, detail=detail)
        log_event(
            _LOGGER,
            logging.WARNING,
            "job_failed",
            f"workbook_too_large: {detail}",
            job_id=job_id,
            generation=generation,
            error_code="workbook_too_large",
            reason=detail,
            budget_bytes=budget,
        )
        return
    if result.timed_out:
        mark_job_failed(dest, job_id, "TimeoutError", generation)
        return
    meta = read_meta(dest / "meta.json")
    status = meta.get("status")
    if status not in _TERMINAL:
        mark_job_failed(
            dest,
            job_id,
            str(meta.get("error") or "pipeline_failed"),
            generation,
        )


def _mark_from_meta(meta: dict, error: str) -> None:
    data_dir = meta.get("data_dir")
    session_id = meta.get("session_id")
    book_id = meta.get("book_job_id")
    if not isinstance(data_dir, str) or not isinstance(session_id, str):
        return
    if not isinstance(book_id, str):
        return
    generation = meta.get("generation")
    if not isinstance(generation, int):
        return
    dest = session_job_dir(Path(data_dir), book_id, session_id)
    mark_job_failed(dest, book_id, error, generation)


def _save(job: Job) -> None:
    save = getattr(job, "save", None)
    if callable(save):
        save()
