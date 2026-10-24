"""SharedCacheBusy becomes Retry on the same RQ job, then a failed meta."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rq import Queue, Retry, SimpleWorker
from rq.job import Job, JobStatus
from rq.serializers import JSONSerializer

from finance_context.adapters.disk_store import DiskStore
from finance_context.api.routes.jobs import _job_body, _read_job
from finance_context.api.state import AppContext
from finance_context.app.pipeline import SHARED_CACHE_BUSY_EXIT, Pipeline, run_queued_job
from finance_context.app.shared_cache import SharedCacheBusy
from finance_context.jobqueue.jobs import execute_job
from finance_context.jobqueue.queue import RQJobQueue, redis_client
from finance_context.jobqueue.spawn import HORSE_FILE, ChildResult, spawn_child, watch_child
from finance_context.settings import Settings
from finance_context.store.paths import session_job_dir

_BOOK = "c3" * 32


class _Job:
    def __init__(self) -> None:
        self.meta: dict = {}
        self.id = f"local--{_BOOK}"
        self.number_of_retries = 0
        self.saved = 0

    def save_meta(self) -> None:
        self.saved += 1


def _payload(tmp_path: Path, generation: int = 1) -> dict[str, object]:
    return {
        "session_id": "local",
        "job_id": _BOOK,
        "data_dir": str(tmp_path),
        "generation": generation,
    }


def _prepare(tmp_path: Path, *, generation: int = 1, retries: int = 0) -> Path:
    dest = session_job_dir(tmp_path, _BOOK, "local")
    dest.mkdir(parents=True)
    body: dict[str, object] = {
        "job_id": _BOOK,
        "status": "queued",
        "stage": "queued",
        "generation": generation,
    }
    if retries:
        body["shared_cache_busy_retries"] = retries
    (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "meta" / "meta.json").write_text(json.dumps(body), encoding="utf-8")
    return dest


def _busy_horse(monkeypatch: pytest.MonkeyPatch, job: _Job | None) -> None:
    monkeypatch.setattr(
        "finance_context.jobqueue.jobs._admission_waiting",
        lambda _settings, _budget: False,
    )
    monkeypatch.setattr(
        "finance_context.jobqueue.jobs.spawn_child",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "finance_context.jobqueue.jobs.watch_child",
        lambda *_args, **_kwargs: ChildResult(shared_cache_busy=True),
    )
    monkeypatch.setattr(
        "finance_context.jobqueue.jobs.get_current_job",
        lambda: job,
    )


def test_busy_requeues_with_the_configured_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = _Job()
    _busy_horse(monkeypatch, job)
    monkeypatch.setenv("SHARED_CACHE_BUSY_RETRY_SEC", "17")
    monkeypatch.setenv("SHARED_CACHE_BUSY_MAX_RETRIES", "4")
    dest = _prepare(tmp_path)
    (dest / HORSE_FILE).write_text('{"pid": 1, "generation": 1}', encoding="utf-8")
    result = execute_job(_payload(tmp_path))
    assert isinstance(result, Retry)
    assert result.intervals == [17]
    assert result.max == 1
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "queued"
    assert meta["stage"] == "queued"
    assert meta["shared_cache_busy_retries"] == 1
    assert "error" not in meta
    assert not (dest / HORSE_FILE).exists()

    job.number_of_retries = 3
    again = execute_job(_payload(tmp_path))
    assert isinstance(again, Retry)
    assert again.intervals == [17]
    assert again.max == 4
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "queued"
    assert meta["shared_cache_busy_retries"] == 2
    assert "error" not in meta


@pytest.mark.parametrize(("cap", "seen"), [(0, 0), (3, 3)])
def test_busy_exhausted_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cap: int, seen: int
) -> None:
    _busy_horse(monkeypatch, _Job())
    monkeypatch.setenv("SHARED_CACHE_BUSY_RETRY_SEC", "9")
    monkeypatch.setenv("SHARED_CACHE_BUSY_MAX_RETRIES", str(cap))
    dest = _prepare(tmp_path, generation=2, retries=seen)
    result = execute_job(_payload(tmp_path, generation=2))
    assert result is None
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["error"] == "shared_cache_busy"
    assert meta["detail"] == "shared book cache stayed busy"
    assert meta["generation"] == 2
    body = _job_body(_BOOK, meta)
    assert body["error"] == "shared_cache_busy"
    assert body["detail"] == "shared book cache stayed busy"


def test_requeue_does_not_create_a_second_job(
    redis_settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Leave get_current_job alone so SimpleWorker runs the real job object.
    monkeypatch.setattr(
        "finance_context.jobqueue.jobs._admission_waiting",
        lambda _settings, _budget: False,
    )
    monkeypatch.setattr(
        "finance_context.jobqueue.jobs.spawn_child",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "finance_context.jobqueue.jobs.watch_child",
        lambda *_args, **_kwargs: ChildResult(shared_cache_busy=True),
    )
    monkeypatch.setenv("SHARED_CACHE_BUSY_RETRY_SEC", "60")
    monkeypatch.setenv("SHARED_CACHE_BUSY_MAX_RETRIES", "5")
    monkeypatch.setenv("REDIS_URL", redis_settings.redis_url)
    dest = _prepare(tmp_path)
    client = redis_client(
        redis_settings,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    queue = Queue(
        redis_settings.queue_name,
        connection=client,
        serializer=JSONSerializer,
    )
    rq_id = f"local--{_BOOK}"
    queue.enqueue(
        "finance_context.jobqueue.jobs.execute_job",
        _payload(tmp_path),
        job_id=rq_id,
        unique=True,
        result_ttl=500,
    )
    worker = SimpleWorker([queue], connection=client, serializer=JSONSerializer)
    worker.work(burst=True, with_scheduler=True)
    job = Job.fetch(rq_id, connection=client, serializer=JSONSerializer)
    assert job.get_status() == JobStatus.SCHEDULED
    keys = sorted(client.scan_iter("rq:job:*"))
    assert keys == [f"rq:job:{rq_id}".encode()]
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "queued"
    assert "error" not in meta
    assert meta["shared_cache_busy_retries"] == 1

    settings = Settings(data_dir=tmp_path, _env_file=None)
    ctx = AppContext(
        settings=settings,
        store=DiskStore(settings.data_dir, session_id=settings.session_id),
        pipeline=Pipeline(settings),
        queue=RQJobQueue(settings),
        max_upload_bytes=1024 * 1024,
    )
    loaded = _read_job(ctx, _BOOK)
    assert loaded is not None
    assert loaded["status"] == "queued"
    assert loaded.get("error") is None
    disk = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert disk["status"] == "queued"
    assert "error" not in disk
    client.close()


class _Queue:
    def __init__(self, status: str) -> None:
        self.status_value = status

    def status(self, _rq_id: str) -> str:
        return self.status_value


@pytest.mark.parametrize("status", ["scheduled", "deferred", "started"])
def test_get_during_busy_wait_does_not_report_lost(tmp_path: Path, status: str) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        job_parent_watch_interval_sec=0.01,
        _env_file=None,
    )
    ctx = AppContext(
        settings=settings,
        store=DiskStore(settings.data_dir, session_id=settings.session_id),
        pipeline=Pipeline(settings),
        queue=_Queue(status),  # type: ignore[arg-type]
        max_upload_bytes=1024 * 1024,
    )
    dest = session_job_dir(settings.data_dir, _BOOK, settings.session_id)
    dest.mkdir(parents=True)
    (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "meta" / "meta.json").write_text(
        json.dumps(
            {
                "job_id": _BOOK,
                "status": "queued",
                "stage": "queued",
                "generation": 1,
                "shared_cache_busy_retries": 1,
            }
        ),
        encoding="utf-8",
    )
    loaded = _read_job(ctx, _BOOK)
    assert loaded is not None
    assert loaded["status"] == "queued"
    assert loaded.get("error") is None
    disk = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert disk["status"] == "queued"
    assert "error" not in disk


def _exit_tempfail() -> None:
    raise SystemExit(SHARED_CACHE_BUSY_EXIT)


def test_watch_child_reports_shared_cache_busy() -> None:
    proc = spawn_child(_exit_tempfail, (), malloc_arena_max=None, name="busy-exit")
    result = watch_child(
        proc,
        timeout_sec=5,
        budget_bytes=None,
        watch_interval_sec=0.05,
        terminate_grace_sec=1,
        kill_grace_sec=1,
    )
    assert result.shared_cache_busy is True
    assert result.timed_out is False
    assert result.kernel_oom is False


def test_queued_child_exits_tempfail_without_failing_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise_busy(self: Pipeline, dest_dir: Path, *, job_id: str, **_kwargs: object) -> None:
        del self, dest_dir
        raise SharedCacheBusy(job_id, retry_after_sec=1.5, max_retries=2)

    monkeypatch.setattr(Pipeline, "run", _raise_busy)
    dest = _prepare(tmp_path)
    with pytest.raises(SystemExit) as caught:
        run_queued_job(str(tmp_path), _BOOK, 1, "local")
    assert caught.value.code == SHARED_CACHE_BUSY_EXIT
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "queued"
    assert "error" not in meta
