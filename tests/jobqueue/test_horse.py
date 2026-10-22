from __future__ import annotations

import json
from pathlib import Path

from rq import Retry

from finance_context.jobqueue.jobs import execute_job, on_horse_killed
from finance_context.store.paths import session_job_dir

_BOOK = "a1" * 32


class _Job:
    def __init__(self, meta: dict) -> None:
        self.meta = meta
        self.retries_left = 1
        self.id = f"local--{_BOOK}"
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


def _meta(tmp_path: Path, *, before: int) -> dict:
    return {
        "oom_kill_before": before,
        "session_id": "local",
        "book_job_id": _BOOK,
        "data_dir": str(tmp_path),
        "generation": 2,
    }


def test_oom_kill_zeroes_retries_and_writes_oom_killed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "finance_context.jobqueue.jobs.cgroup_oom_kill_count",
        lambda: 4,
    )
    job = _Job(_meta(tmp_path, before=1))
    on_horse_killed(job, 0, 9, None)  # type: ignore[arg-type]
    assert job.retries_left == 0
    assert job.saved == 1
    meta = json.loads((session_job_dir(tmp_path, _BOOK, "local") / "meta.json").read_text())
    assert meta["status"] == "failed"
    assert meta["error"] == "oom_killed"
    assert meta["generation"] == 2
    assert "publisher" not in meta


def test_lost_horse_keeps_the_retry_and_writes_process_lost(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "finance_context.jobqueue.jobs.cgroup_oom_kill_count",
        lambda: 1,
    )
    job = _Job(_meta(tmp_path, before=1))
    on_horse_killed(job, 0, 9, None)  # type: ignore[arg-type]
    assert job.retries_left == 1
    assert job.saved == 0
    meta = json.loads((session_job_dir(tmp_path, _BOOK, "local") / "meta.json").read_text())
    assert meta["error"] == "process_lost"
    assert meta["generation"] == 2


def test_admission_returns_retry_on_the_same_call(monkeypatch) -> None:
    monkeypatch.setattr(
        "finance_context.jobqueue.jobs._admission_waiting",
        lambda _settings, _budget: True,
    )
    monkeypatch.setattr("finance_context.jobqueue.jobs.get_current_job", lambda: None)
    monkeypatch.setenv("JOB_ADMISSION_RETRY_SEC", "30")
    result = execute_job(
        {
            "session_id": "local",
            "job_id": _BOOK,
            "data_dir": "/tmp/finance-context-test",
            "generation": 1,
        }
    )
    assert isinstance(result, Retry)
    assert result.intervals == [30]
    assert result.max == 1
