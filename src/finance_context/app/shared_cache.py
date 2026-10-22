"""Shared book cache: one lock per book, in-place invalidation, atomic publish.

The cache lives at ``data/shared/books/{job_id}/``. ``job_id`` is the content
hash, so two sessions share one directory. Paths inside it stay
``source.xlsx``, ``raw/``, ``ir/``, and ``layout.json``. ``stages.json`` is the
stage-fingerprint sidecar (same role as ``.lock``): it is not a published
artifact. ``.lock`` is never unlinked.

POST records ``rebuild_from`` on the session meta and does not delete shared
files. The job child calls :func:`invalidate_for_run` while it holds
``shared/books/{job_id}/.lock``.

Busy signal for PR3
-------------------
:class:`SharedCacheBusy` is raised by :meth:`Pipeline.run
<finance_context.app.pipeline.Pipeline.run>` when ``flock(LOCK_EX|LOCK_NB)``
on that lock fails. Attributes:

- ``job_id``: the book id
- ``retry_after_sec``: ``Settings.shared_cache_busy_retry_sec``
  (``SHARED_CACHE_BUSY_RETRY_SEC``, default 30)
- ``max_retries``: ``Settings.shared_cache_busy_max_retries``
  (``SHARED_CACHE_BUSY_MAX_RETRIES``, default 120)

The child does not wait inside :meth:`Pipeline.run`. PR3 catches this
exception in the RQ worker and raises ``rq.Retry(interval=exc.retry_after_sec)``.
Count attempts in the queue. After ``max_retries`` requeues, fail the job
with error ``shared_cache_busy``. Do not sleep in the work horse: that would
hold the slot.

Until that queue exists, :func:`finance_context.app.pipeline.execute_job`
sleeps ``retry_after_sec`` and calls ``Pipeline.run`` again, at most
``max_retries`` extra times, then raises ``ContextError("shared_cache_busy")``.
Each wait logs ``shared_cache_busy_requeue`` (``attempt``, ``retry_after_sec``,
``max_retries``, ``job_id``). A deletion logs ``shared_cache_invalidate``
(``stale_from``, ``job_id``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from finance_context.app.artifacts import clear_downstream_artifacts, clear_shared_artifacts
from finance_context.app.publisher import (
    STAGE_ORDER,
    publisher_matches,
    publisher_stamp,
    stale_from_meta,
    stamped_stages,
)
from finance_context.observability import log_event
from finance_context.store.fs import file_lock, update_json, write_json

_LOGGER = logging.getLogger("finance_context.pipeline")
STAGES_NAME = "stages.json"
REBUILD_FROM = "rebuild_from"
_LOCK_NAME = ".lock"


class SharedCacheBusy(Exception):
    """The shared book lock is held. PR3 maps this to ``rq.Retry``.

    See the module docstring for the fields and the retry contract.
    """

    def __init__(self, job_id: str, *, retry_after_sec: float, max_retries: int) -> None:
        self.job_id = job_id
        self.retry_after_sec = retry_after_sec
        self.max_retries = max_retries
        super().__init__(
            f"shared cache busy for {job_id}: retry in {retry_after_sec}s "
            f"up to {max_retries} times"
        )


def record_rebuild_stage(dest: Path, stale_from: str | None) -> None:
    """Remember which session stage the next run must rebuild.

    Shared files are left in place. A missing meta and a null stage write
    nothing, so a first POST does not invent a document. A recorded stage
    marks the session job queued: the previous success must not stay visible
    while the child has not started.
    """
    meta_path = dest / "meta.json"
    if stale_from is None and not meta_path.is_file():
        return

    def mutate(current: dict) -> dict:
        updated = dict(current)
        if stale_from is None:
            updated.pop(REBUILD_FROM, None)
        else:
            updated[REBUILD_FROM] = stale_from
            updated["status"] = "queued"
            updated["stage"] = "queued"
        return updated

    update_json(meta_path, mutate)


@contextmanager
def shared_book_lock(
    book: Path, *, job_id: str, retry_after_sec: float, max_retries: int
) -> Iterator[None]:
    """Exclusive non-blocking lock on ``book/.lock``. Raises :class:`SharedCacheBusy`."""
    with file_lock(book / _LOCK_NAME, blocking=False) as acquired:
        if not acquired:
            raise SharedCacheBusy(
                job_id,
                retry_after_sec=retry_after_sec,
                max_retries=max_retries,
            )
        yield


def invalidate_for_run(dest: Path, book: Path, *, shared: bool, job_id: str) -> None:
    """Drop stale artifacts. The caller holds the book lock and the session lock.

    Shared freshness comes from ``stages.json``. A missing sidecar is stale
    from layout only when this session cannot show the cache was built by the
    current code. Session publications follow the recorded ``rebuild_from``
    when it is set, otherwise the session stage map. A shared deletion is the
    only path that logs
    ``shared_cache_invalidate``.
    """
    meta = _read_meta(dest)
    session_stale = session_stale_stage(meta)
    if shared:
        adopt = publisher_matches(meta)
        stale = shared_stale_stage(book, adopt_current=adopt)
        if stale is not None and clear_shared_artifacts(book, stale):
            log_event(
                _LOGGER,
                logging.INFO,
                "shared_cache_invalidate",
                "stale shared cache invalidated",
                job_id=job_id,
                stale_from=stale,
            )
        if session_stale is not None:
            clear_downstream_artifacts(dest, stale_from=session_stale)
        return
    if session_stale is not None:
        clear_downstream_artifacts(dest, stale_from=session_stale)


def shared_stale_stage(book: Path, *, adopt_current: bool) -> str | None:
    """First stale shared stage, or None when the cache matches this code.

    ``adopt_current`` means this session's publisher stamp matches the code,
    so a cache written before ``stages.json`` existed is kept and stamped
    by the caller after the run.
    """
    path = book / STAGES_NAME
    if not path.is_file():
        if adopt_current or not _cache_has_body(book):
            return None
        return "layout"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "layout"
    if not isinstance(payload, dict):
        return "layout"
    return stale_from_meta(payload)


def session_stale_stage(meta: dict | None) -> str | None:
    """Recorded ``rebuild_from`` wins. Otherwise use the session stage map.

    POST drops ``meta.json`` before recording the stage, so the replacement
    document has no stage map. A missing map means layout, which would widen a
    publish or graph rebuild. The recorded stage is the one computed from the
    map that was just removed.
    """
    if isinstance(meta, dict):
        value = meta.get(REBUILD_FROM)
        if isinstance(value, str) and value in STAGE_ORDER:
            return value
    return stale_from_meta(meta)


def write_shared_stamp(book: Path) -> None:
    """Record the stage fingerprints of the cache this process just published."""
    if not _cache_has_body(book):
        return
    write_json(
        book / STAGES_NAME,
        {"publisher": publisher_stamp(), "stages": stamped_stages()},
    )


def _cache_has_body(book: Path) -> bool:
    return (
        (book / "layout.json").is_file()
        or (book / "raw" / "workbook.json").is_file()
        or (book / "ir" / "cells.parquet").is_file()
    )


def _read_meta(dest: Path) -> dict | None:
    path = dest / "meta.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
