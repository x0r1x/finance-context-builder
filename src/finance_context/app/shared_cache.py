"""Shared book cache: one lock per book, in-place invalidation, atomic publish.

The cache lives at ``data/shared/books/{job_id}/``. ``job_id`` is the content
hash, so two sessions share one directory. Paths inside it stay
``source.xlsx``, ``raw/``, ``ir/``, and ``layout.json``. ``stages.json`` is the
stage-fingerprint sidecar (same role as ``.lock``): it is not a published
artifact. ``.lock`` is never unlinked.

POST records ``rebuild_from`` and the bus generation on the session meta,
keeps the stage map, and does not delete shared files. The job child calls
:func:`invalidate_for_run` while it holds ``shared/books/{job_id}/.lock``.

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

The child does not wait inside :meth:`Pipeline.run`. The RQ spawn child
exits 75 (``EX_TEMPFAIL``) on :class:`SharedCacheBusy`. The horse returns
``rq.Retry(interval=...)`` for that same job (rq#2499: ``enqueue_in`` drops
``unique``). Attempts are ``meta.json`` ``shared_cache_busy_retries``, not
RQ's lost-horse counter. After ``max_retries`` requeues the next busy result
fails the job with error ``shared_cache_busy``. While a retry is scheduled,
meta stays ``queued`` and GET does not mark ``queue_lost``. Do not sleep in
the work horse: that would hold the slot.

CLI ``build`` still calls :func:`finance_context.app.pipeline.execute_job`,
which sleeps ``retry_after_sec`` and calls ``Pipeline.run`` again, at most
``max_retries`` extra times. A sleep that would reach ``JOB_TIMEOUT_SEC``,
counted from process start, raises ``ContextError("shared_cache_busy")``
instead of waiting. Each wait logs ``shared_cache_busy_requeue`` (``attempt``,
``retry_after_sec``, ``max_retries``, ``job_id``). A deletion logs
``shared_cache_invalidate`` (``stale_from``, ``job_id``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NamedTuple

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


class SharedInvalidation(NamedTuple):
    """What the child should stamp after a run under the book lock."""

    rebuilt_from: str | None
    trusted_stages: dict | None
    adopt: bool


class SharedCacheBusy(Exception):
    """The shared book lock is held. PR3 maps this to ``rq.Retry``.

    See the module docstring for the fields and the retry contract.
    """

    def __init__(self, job_id: str, *, retry_after_sec: float, max_retries: int) -> None:
        self.job_id = job_id
        self.retry_after_sec = retry_after_sec
        self.max_retries = max_retries
        super().__init__(
            f"shared cache busy for {job_id}: retry in {retry_after_sec}s up to {max_retries} times"
        )


def record_rebuild_stage(
    dest: Path, stale_from: str | None, *, generation: int | None = None
) -> None:
    """Remember which session stage the next run must rebuild.

    Shared files are left in place. A missing meta and a null stage write
    nothing, so a first POST does not invent a document. A recorded stage
    marks the session job queued and keeps the stage map already on the
    document. ``generation`` is the bus generation for this launch; startup
    reads it if the process dies during the busy wait.
    """
    meta_path = dest / "meta" / "meta.json"
    if stale_from is None and generation is None and not meta_path.is_file():
        return

    def mutate(current: dict) -> dict:
        updated = dict(current)
        if stale_from is None:
            updated.pop(REBUILD_FROM, None)
        else:
            updated[REBUILD_FROM] = stale_from
            _mark_queued(updated)
        if generation is not None:
            updated["generation"] = generation
            _mark_queued(updated)
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


def invalidate_for_run(dest: Path, book: Path, *, shared: bool, job_id: str) -> SharedInvalidation:
    """Drop stale artifacts. The caller holds the book lock and the session lock.

    Shared freshness comes from ``stages.json``. A missing or unreadable
    sidecar uses the recorded stage when that is compile or layout, so a
    compile-code change drops formula IR and ``raw/``. Session publications
    follow the recorded ``rebuild_from`` when it is set, otherwise the session
    stage map. A shared deletion is the only path that logs
    ``shared_cache_invalidate``. Stamp inputs are captured before session meta
    is removed.
    """
    meta = _read_meta(dest)
    session_stale = session_stale_stage(meta)
    trusted = _stage_map(meta)
    if shared:
        adopt = publisher_matches(meta)
        # A cache this run creates has no previous hash. Stamp it as current.
        adopt = adopt or not _cache_has_body(book)
        stale = shared_stale_stage(book, adopt_current=adopt, recorded=session_stale)
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
        return SharedInvalidation(rebuilt_from=stale, trusted_stages=trusted, adopt=adopt)
    if session_stale is not None:
        clear_downstream_artifacts(dest, stale_from=session_stale)
    return SharedInvalidation(rebuilt_from=None, trusted_stages=None, adopt=False)


def shared_stale_stage(
    book: Path, *, adopt_current: bool, recorded: str | None = None
) -> str | None:
    """First stale shared stage, or None when the cache matches this code.

    ``adopt_current`` means this session's publisher stamp matches the code,
    so a cache written before ``stages.json`` existed is kept and stamped
    by the caller after the run. A missing or unreadable sidecar otherwise
    follows ``recorded`` when that is compile or layout, and layout otherwise.
    """
    path = book / STAGES_NAME
    if not path.is_file():
        if adopt_current or not _cache_has_body(book):
            return None
        return _recorded_or_layout(recorded)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _recorded_or_layout(recorded)
    if not isinstance(payload, dict):
        return _recorded_or_layout(recorded)
    return stale_from_meta(payload)


def session_stale_stage(meta: dict | None) -> str | None:
    """Recorded ``rebuild_from`` wins. Otherwise use the session stage map.

    POST keeps the map and writes ``rebuild_from`` from it. A missing map
    means layout, which would widen a publish or graph rebuild, so the
    recorded stage is the one the child uses.
    """
    if isinstance(meta, dict):
        value = meta.get(REBUILD_FROM)
        if isinstance(value, str) and value in STAGE_ORDER:
            return value
    return stale_from_meta(meta)


def write_shared_stamp(
    book: Path,
    *,
    rebuilt_from: str | None = None,
    trusted: dict | None = None,
    adopt: bool = True,
) -> None:
    """Record stage fingerprints for the cache this process just published.

    A stage gets the current hash when this run rebuilt it, when the previous
    sidecar or ``trusted`` map already had that hash, or when ``adopt`` says
    the cache was built by this code. Anything else stays unverified so the
    next run does not treat an old compile as current.
    """
    if not _cache_has_body(book):
        return
    current = stamped_stages()
    previous = _stage_map(_read_json_object(book / STAGES_NAME))
    trusted_stages = trusted if isinstance(trusted, dict) else {}
    rank = STAGE_ORDER.index(rebuilt_from) if rebuilt_from in STAGE_ORDER else None
    stages: dict[str, str] = {}
    for index, name in enumerate(STAGE_ORDER):
        if rank is not None and index >= rank:
            stages[name] = current[name]
            continue
        prev = previous.get(name) if previous else None
        trust = trusted_stages.get(name)
        if prev == current[name] or trust == current[name]:
            stages[name] = current[name]
        elif isinstance(prev, str) and prev:
            stages[name] = prev
        elif adopt:
            stages[name] = current[name]
        else:
            stages[name] = "unverified"
    complete = all(stages[name] == current[name] for name in STAGE_ORDER)
    write_json(
        book / STAGES_NAME,
        {"publisher": publisher_stamp() if complete else "partial", "stages": stages},
    )


def _mark_queued(meta: dict) -> None:
    meta["status"] = "queued"
    meta["stage"] = "queued"
    # A previous process_lost must not stay on the document GET reads.
    meta.pop("error", None)
    meta.pop("detail", None)


def _recorded_or_layout(recorded: str | None) -> str:
    if recorded in STAGE_ORDER and STAGE_ORDER.index(recorded) <= STAGE_ORDER.index("layout"):
        return recorded
    return "layout"


def _stage_map(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    nested = payload.get("stages")
    if isinstance(nested, dict):
        return nested
    return None


def _read_json_object(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _cache_has_body(book: Path) -> bool:
    return (
        (book / "layout" / "layout.json").is_file()
        or (book / "raw" / "workbook.json").is_file()
        or (book / "ir" / "cells.parquet").is_file()
    )


def _read_meta(dest: Path) -> dict | None:
    path = dest / "meta" / "meta.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None
