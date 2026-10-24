"""POST decisions that must not wait on the pipeline lock."""

from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from finance_context.adapters.disk_store import DiskStore
from finance_context.api.errors import ApiError
from finance_context.api.routes.jobs import _accept_upload, _queue_lost, _read_job
from finance_context.api.state import AppContext
from finance_context.app.pipeline import Pipeline
from finance_context.jobqueue.spawn import HORSE_FILE, HORSE_LOCK, horse_owner_alive
from finance_context.settings import Settings
from finance_context.store.fs import file_lock, write_json
from finance_context.store.paths import session_job_dir

_JOB = "ab" * 32
_DIGEST = _JOB


class _Queue:
    def __init__(self, status: str | None) -> None:
        self.status_value = status
        self.stopped = 0
        self.deleted = 0
        self.enqueued = 0

    def status(self, _rq_id: str) -> str | None:
        return self.status_value

    def request_stop(self, _rq_id: str) -> None:
        self.stopped += 1

    def delete(self, _rq_id: str) -> None:
        self.deleted += 1
        self.status_value = None

    def enqueue(self, _payload: dict) -> None:
        self.enqueued += 1

    def session_inflight(self, _session: str) -> int:
        return 0


def _ctx(tmp_path: Path, queue: _Queue, **overrides: object) -> AppContext:
    settings = Settings(
        data_dir=tmp_path / "data",
        job_parent_watch_interval_sec=0.01,
        job_terminate_grace_sec=0.05,
        job_kill_grace_sec=0.05,
        _env_file=None,
        **overrides,  # type: ignore[arg-type]
    )
    return AppContext(
        settings=settings,
        store=DiskStore(settings.data_dir, session_id=settings.session_id),
        pipeline=Pipeline(settings),
        queue=queue,  # type: ignore[arg-type]
        max_upload_bytes=1024 * 1024,
    )


def _meta(ctx: AppContext, **fields: object) -> Path:
    dest = session_job_dir(ctx.settings.data_dir, _JOB, ctx.settings.session_id)
    dest.mkdir(parents=True, exist_ok=True)
    body = {"job_id": _JOB, "status": "running", "stage": "parse", "generation": 1}
    body.update(fields)
    (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "meta" / "meta.json").write_text(json.dumps(body), encoding="utf-8")
    return dest


def _no_sleep(_seconds: float) -> None:
    raise AssertionError("liveness slept")


def _dead_pid() -> int:
    for candidate in range(2_147_000_001, 2_147_000_040):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except PermissionError:
            continue
    raise AssertionError("no unused pid")


def _horse(dest: Path, pid: int, generation: int = 1) -> None:
    write_json(dest / HORSE_FILE, {"pid": pid, "generation": generation})


def _hold(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    return handle


def test_busy_pipeline_lock_returns_the_running_job_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A held pipeline lock must not block. Sleep or a blocking flock fails the test."""
    monkeypatch.setattr("finance_context.api.routes.jobs.time.sleep", _no_sleep)
    seen: list[tuple[str, bool]] = []

    @contextmanager
    def _spy(path: Path, *, blocking: bool = True) -> Iterator[bool]:
        seen.append((path.name, blocking))
        if path.name == ".lock" and blocking:
            raise AssertionError("pipeline lock acquired blocking")
        with file_lock(path, blocking=blocking) as locked:
            yield locked

    monkeypatch.setattr("finance_context.api.routes.jobs.file_lock", _spy)
    queue = _Queue("started")
    ctx = _ctx(tmp_path, queue)
    dest = _meta(ctx)
    run_lock = _hold(dest / ".run.lock")
    pipeline_lock = _hold(dest / ".lock")
    try:
        body = _accept_upload(ctx, "model.xlsx", b"PK", _DIGEST, _JOB)
    finally:
        fcntl.flock(run_lock.fileno(), fcntl.LOCK_UN)
        run_lock.close()
        fcntl.flock(pipeline_lock.fileno(), fcntl.LOCK_UN)
        pipeline_lock.close()
    assert all(not blocking for name, blocking in seen if name == ".lock")
    assert body["status"] == "running"
    assert queue.stopped == 0
    assert queue.deleted == 0
    assert queue.enqueued == 0


def test_publisher_change_stops_before_the_pipeline_lock_and_does_not_replace(
    tmp_path: Path,
) -> None:
    queue = _Queue("started")
    ctx = _ctx(tmp_path, queue)
    dest = _meta(ctx, publisher="stale-stamp")
    run_lock = _hold(dest / ".run.lock")
    pipeline_lock = _hold(dest / ".lock")
    try:
        started = time.monotonic()
        with pytest.raises(ApiError) as caught:
            _accept_upload(ctx, "model.xlsx", b"PK", _DIGEST, _JOB)
        elapsed = time.monotonic() - started
    finally:
        fcntl.flock(run_lock.fileno(), fcntl.LOCK_UN)
        run_lock.close()
        fcntl.flock(pipeline_lock.fileno(), fcntl.LOCK_UN)
        pipeline_lock.close()
    assert elapsed < 1
    assert caught.value.http_status == 409
    assert caught.value.code == "job_still_running"
    assert caught.value.headers == {"Retry-After": "30"}
    assert queue.stopped == 1
    assert queue.deleted == 0
    assert queue.enqueued == 0


def test_started_without_a_run_lock_is_enqueued_again(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "finance_context.api.routes.jobs.time.sleep",
        _no_sleep,
    )
    queue = _Queue("started")
    ctx = _ctx(tmp_path, queue)
    dest = _meta(ctx)
    _horse(dest, _dead_pid())
    body = _accept_upload(ctx, "model.xlsx", b"PK", _DIGEST, _JOB)
    assert body["status"] == "queued"
    assert body["stage"] == "queued"
    assert queue.deleted == 1
    assert queue.enqueued == 1
    stored = json.loads(
        (session_job_dir(ctx.settings.data_dir, _JOB, "local") / "meta" / "meta.json").read_text()
    )
    assert stored["generation"] == 2


def test_get_does_not_mark_lost_while_post_holds_the_enqueue_lock(tmp_path: Path) -> None:
    queue = _Queue(None)
    ctx = _ctx(tmp_path, queue)
    dest = _meta(ctx, status="queued", stage="queued")
    held = _hold(dest / ".lock")
    try:
        body = _read_job(ctx, _JOB)
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()
    assert body is not None
    assert body["status"] == "queued"
    assert "error" not in body
    stored = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert stored["status"] == "queued"


def test_get_marks_lost_when_the_enqueue_lock_is_free(tmp_path: Path) -> None:
    queue = _Queue(None)
    ctx = _ctx(tmp_path, queue)
    dest = _meta(ctx, status="queued", stage="queued")
    body = _read_job(ctx, _JOB)
    assert body is not None
    assert body["status"] == "failed"
    assert body["error"] == "queue_lost"
    stored = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert stored["error"] == "queue_lost"


def test_unreadable_horse_lock_is_not_lost(tmp_path: Path, monkeypatch) -> None:
    """An access error on ``.horse.lock`` is not a dead horse."""
    queue = _Queue("started")
    ctx = _ctx(tmp_path, queue)
    dest = _meta(ctx, status="queued", stage="queued")
    _horse(dest, _dead_pid())
    (dest / HORSE_LOCK).write_bytes(b"")
    real_open = Path.open

    def _open(self: Path, *args: object, **kwargs: object):
        if self.name == HORSE_LOCK:
            raise PermissionError(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _open)
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert horse_owner_alive(dest, 1) is None
    assert _queue_lost(ctx, _JOB, dest, meta) is False
    disk = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert disk.get("error") != "queue_lost"


def test_get_marks_a_started_job_with_a_free_run_lock_lost(tmp_path: Path) -> None:
    queue = _Queue("started")
    ctx = _ctx(tmp_path, queue)
    dest = _meta(ctx)
    _horse(dest, _dead_pid())
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert _queue_lost(ctx, _JOB, dest, meta) is True


def test_live_horse_is_not_replaced_before_the_child_takes_the_lock(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "finance_context.api.routes.jobs.time.sleep",
        _no_sleep,
    )
    queue = _Queue("started")
    ctx = _ctx(tmp_path, queue)
    dest = _meta(ctx)
    # A dead pid must not matter. The API and the worker do not share PIDs.
    _horse(dest, _dead_pid())
    held = _hold(dest / HORSE_LOCK)
    try:
        body = _accept_upload(ctx, "model.xlsx", b"PK", _DIGEST, _JOB)
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()
    assert body["status"] == "running"
    assert queue.deleted == 0
    assert queue.enqueued == 0


def _queue_lost_meta(ctx: AppContext) -> Path:
    return _meta(ctx, status="failed", stage="failed", error="queue_lost", generation=1)


@pytest.mark.parametrize("rq_status", ["started", "queued", "deferred", "scheduled"])
def test_stale_queue_lost_relaunches(tmp_path: Path, rq_status: str) -> None:
    queue = _Queue(rq_status)
    ctx = _ctx(tmp_path, queue)
    dest = _queue_lost_meta(ctx)
    _horse(dest, _dead_pid())
    body = _accept_upload(ctx, "model.xlsx", b"PK", _DIGEST, _JOB)
    assert body["status"] == "queued"
    assert body["stage"] == "queued"
    assert queue.deleted == 1
    assert queue.enqueued == 1
    stored = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert stored["status"] == "queued"
    assert stored["generation"] == 2
    assert "error" not in stored


def test_alive_horse_on_queue_lost_is_still_running(tmp_path: Path) -> None:
    queue = _Queue("started")
    ctx = _ctx(tmp_path, queue)
    dest = _queue_lost_meta(ctx)
    _horse(dest, _dead_pid())
    held = _hold(dest / HORSE_LOCK)
    try:
        with pytest.raises(ApiError) as caught:
            _accept_upload(ctx, "model.xlsx", b"PK", _DIGEST, _JOB)
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()
    assert caught.value.http_status == 409
    assert caught.value.code == "job_still_running"
    assert queue.deleted == 0
    assert queue.enqueued == 0


def test_unreadable_horse_lock_on_queue_lost_is_not_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = _Queue("started")
    ctx = _ctx(tmp_path, queue)
    dest = _queue_lost_meta(ctx)
    _horse(dest, _dead_pid())
    (dest / HORSE_LOCK).write_bytes(b"")
    real_open = Path.open

    def _open(self: Path, *args: object, **kwargs: object):
        if self.name == HORSE_LOCK:
            raise PermissionError(self)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _open)
    body = _accept_upload(ctx, "model.xlsx", b"PK", _DIGEST, _JOB)
    assert body["status"] == "failed"
    assert queue.deleted == 0
    assert queue.enqueued == 0


def test_missing_horse_record_on_queue_lost_is_not_dead(tmp_path: Path) -> None:
    queue = _Queue("started")
    ctx = _ctx(tmp_path, queue)
    _queue_lost_meta(ctx)
    body = _accept_upload(ctx, "model.xlsx", b"PK", _DIGEST, _JOB)
    assert body["status"] == "failed"
    assert queue.deleted == 0
    assert queue.enqueued == 0


def test_started_without_a_horse_record_is_not_replaced(tmp_path: Path) -> None:
    queue = _Queue("started")
    ctx = _ctx(tmp_path, queue)
    _meta(ctx)
    body = _accept_upload(ctx, "model.xlsx", b"PK", _DIGEST, _JOB)
    assert body["status"] == "running"
    assert queue.deleted == 0
    assert queue.enqueued == 0


def test_get_keeps_a_job_that_finishes_during_the_recheck(tmp_path: Path) -> None:
    queue = _Queue("started")
    ctx = _ctx(tmp_path, queue)
    dest = _meta(ctx)
    _horse(dest, _dead_pid())
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    original = queue.status

    def _finish(_rq_id: str) -> str | None:
        (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
        (dest / "meta" / "meta.json").write_text(
            json.dumps({"job_id": _JOB, "status": "succeeded", "generation": 1}),
            encoding="utf-8",
        )
        return "started"

    queue.status = _finish  # type: ignore[method-assign]
    try:
        assert _queue_lost(ctx, _JOB, dest, meta) is False
    finally:
        queue.status = original  # type: ignore[method-assign]
