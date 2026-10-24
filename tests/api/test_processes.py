from __future__ import annotations

import json
import multiprocessing
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from tests.api.conftest import _kill_worker_group

from finance_context.app.pipeline import mark_job_failed
from finance_context.jobqueue.jobs import _finish
from finance_context.jobqueue.spawn import stop_child, watch_child
from finance_context.store.paths import session_job_dir


class _StubProcess:
    def __init__(self) -> None:
        self.alive = True
        self.events: list[object] = []

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.events.append("terminate")

    def kill(self) -> None:
        self.events.append("kill")
        self.alive = False

    def join(self, timeout: float | None = None) -> None:
        self.events.append(("join", timeout))


def test_kill_worker_group_reaps_a_horse_that_called_setpgrp() -> None:
    """killpg on the worker misses a horse that left the group (rq#2495)."""
    script = (
        "import os, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.setpgrp()\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    member: int | None = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and member is None:
            listed = subprocess.check_output(["ps", "-axo", "pid=,ppid=,pgid="], text=True)
            for line in listed.splitlines():
                parts = line.split()
                if len(parts) < 3:
                    continue
                child, parent, group = (int(parts[0]), int(parts[1]), int(parts[2]))
                if parent == proc.pid and group != proc.pid:
                    member = child
                    break
            time.sleep(0.05)
        assert member is not None
        _kill_worker_group(proc.pid)
        proc.wait(timeout=5)
        gone = time.monotonic() + 5
        while time.monotonic() < gone:
            try:
                os.kill(member, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        raise AssertionError("setpgrp child still alive")
    finally:
        if proc.poll() is None:
            _kill_worker_group(proc.pid)
            proc.wait(timeout=5)


def test_stop_kills_when_terminate_leaves_the_process_alive() -> None:
    proc = _StubProcess()
    stop_child(proc, terminate_grace_sec=5, kill_grace_sec=5)  # type: ignore[arg-type]
    assert proc.events[0] == "terminate"
    assert ("join", 5) in proc.events
    assert "kill" in proc.events
    assert proc.alive is False


def test_mark_job_failed_keeps_a_newer_generation(tmp_path: Path) -> None:
    dest = tmp_path / "job"
    dest.mkdir()
    original = {"job_id": "job", "status": "running", "stage": "layout", "generation": 2}
    (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "meta" / "meta.json").write_text(json.dumps(original), encoding="utf-8")
    mark_job_failed(dest, "job", "TimeoutError", 1)
    stored = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert stored["status"] == "running"
    assert stored["stage"] == "layout"
    assert stored["generation"] == 2


def _exit_immediately() -> None:
    return


def _block_for_timeout() -> None:
    time.sleep(30)


def _running_meta(tmp_path: Path) -> tuple[str, int, Path]:
    job_id = "job"
    generation = 1
    dest = session_job_dir(tmp_path, job_id)
    dest.mkdir(parents=True)
    (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "meta" / "meta.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "status": "running",
                "stage": "layout",
                "generation": generation,
            }
        ),
        encoding="utf-8",
    )
    return job_id, generation, dest


def _stored(dest: Path) -> dict:
    return json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))


def test_watch_does_not_time_out_a_child_another_thread_already_reaped(tmp_path: Path) -> None:
    """A waiter can reap the child. waitpid then raises ECHILD and poll() leaves
    the process looking alive. The sentinel, not is_alive(), decides the exit.
    """
    proc = multiprocessing.get_context("spawn").Process(target=_exit_immediately)
    proc.start()
    proc.join(5)
    assert proc.exitcode == 0
    assert proc._popen is not None
    proc._popen.returncode = None
    assert proc.is_alive()

    job_id, generation, dest = _running_meta(tmp_path)
    result = watch_child(
        proc,
        timeout_sec=30,
        budget_bytes=None,
        watch_interval_sec=0.05,
        terminate_grace_sec=1,
        kill_grace_sec=1,
    )
    assert result.timed_out is False
    _finish(result, dest, job_id, generation, 1024, None)
    stored = _stored(dest)
    assert stored["status"] == "failed"
    assert stored["error"] == "pipeline_failed"


def test_watch_marks_a_real_timeout_failed(tmp_path: Path) -> None:
    proc = multiprocessing.get_context("spawn").Process(target=_block_for_timeout)
    proc.start()
    try:
        job_id, generation, dest = _running_meta(tmp_path)
        result = watch_child(
            proc,
            timeout_sec=0.2,
            budget_bytes=None,
            watch_interval_sec=0.05,
            terminate_grace_sec=1,
            kill_grace_sec=1,
        )
        assert result.timed_out is True
        _finish(result, dest, job_id, generation, 1024, None)
        stored = _stored(dest)
        assert stored["status"] == "failed"
        assert stored["error"] == "TimeoutError"
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join(5)


def _sleep_until_killed() -> None:
    time.sleep(30)


def _allocate_and_sleep() -> None:
    blob = bytearray(64 * 1024 * 1024)
    for index in range(0, len(blob), 4096):
        blob[index] = 1
    time.sleep(30)


def test_watch_kills_when_reported_rss_exceeds_the_budget(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "finance_context.jobqueue.spawn.process_rss_bytes",
        lambda _pid: 10**12,
    )
    proc = multiprocessing.get_context("spawn").Process(target=_sleep_until_killed)
    proc.start()
    try:
        job_id, generation, dest = _running_meta(tmp_path)
        result = watch_child(
            proc,
            timeout_sec=10,
            budget_bytes=1024,
            watch_interval_sec=0.05,
            terminate_grace_sec=1,
            kill_grace_sec=1,
        )
        assert result.over_budget is True
        _finish(result, dest, job_id, generation, 1024, None)
        stored = _stored(dest)
        assert stored["status"] == "failed"
        assert stored["error"] == "workbook_too_large"
        assert "resident" in stored["detail"]
        assert not proc.is_alive()
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(5)


def _die_by_sigkill() -> None:
    os.kill(os.getpid(), signal.SIGKILL)


def test_watch_ignores_a_cgroup_oom_that_did_not_sigkill_this_child(monkeypatch) -> None:
    seen = {"n": 0}

    def count() -> int:
        seen["n"] += 1
        return 1 if seen["n"] == 1 else 4

    monkeypatch.setattr("finance_context.jobqueue.spawn.cgroup_oom_kill_count", count)
    proc = multiprocessing.get_context("spawn").Process(target=_exit_immediately)
    proc.start()
    result = watch_child(
        proc,
        timeout_sec=10,
        budget_bytes=None,
        watch_interval_sec=0.05,
        terminate_grace_sec=1,
        kill_grace_sec=1,
    )
    assert proc.exitcode == 0
    assert result.kernel_oom is False


def test_watch_marks_sigkill_with_an_oom_counter_as_kernel_oom(monkeypatch) -> None:
    seen = {"n": 0}

    def count() -> int:
        seen["n"] += 1
        return 1 if seen["n"] == 1 else 4

    monkeypatch.setattr("finance_context.jobqueue.spawn.cgroup_oom_kill_count", count)
    proc = multiprocessing.get_context("spawn").Process(target=_die_by_sigkill)
    proc.start()
    result = watch_child(
        proc,
        timeout_sec=10,
        budget_bytes=None,
        watch_interval_sec=0.05,
        terminate_grace_sec=1,
        kill_grace_sec=1,
    )
    assert proc.exitcode == -signal.SIGKILL
    assert result.kernel_oom is True


def test_watch_kills_a_child_whose_real_rss_exceeds_the_budget(tmp_path: Path) -> None:
    proc = multiprocessing.get_context("spawn").Process(target=_allocate_and_sleep)
    proc.start()
    try:
        job_id, generation, dest = _running_meta(tmp_path)
        result = watch_child(
            proc,
            timeout_sec=15,
            budget_bytes=32 * 1024 * 1024,
            watch_interval_sec=0.05,
            terminate_grace_sec=1,
            kill_grace_sec=1,
        )
        _finish(result, dest, job_id, generation, 32 * 1024 * 1024, None)
        stored = _stored(dest)
        assert stored["error"] == "workbook_too_large"
        assert not proc.is_alive()
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(5)


def test_mark_job_failed_keeps_a_terminal_snapshot(tmp_path: Path) -> None:
    dest = tmp_path / "job"
    dest.mkdir()
    for name in (
        "context/context.json",
        "context/context.md",
        "graph/graph.json",
        "graph/graph.md",
    ):
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    original = {"job_id": "job", "status": "succeeded", "stage": "done", "generation": 3}
    (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "meta" / "meta.json").write_text(json.dumps(original), encoding="utf-8")
    mark_job_failed(dest, "job", "TimeoutError", 3)
    stored = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert stored["status"] == "succeeded"
    assert stored["generation"] == 3
