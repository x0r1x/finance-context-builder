from __future__ import annotations

import json
import math
import multiprocessing
import time
from pathlib import Path

from finance_context.adapters.memory_bus import MemoryJobBus
from finance_context.api.processes import JobProcesses, reap_process
from finance_context.app.pipeline import mark_job_failed
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


def test_reap_kills_when_terminate_leaves_the_process_alive() -> None:
    proc = _StubProcess()
    reap_process(proc, join_sec=5)
    assert proc.events[0] == "terminate"
    assert ("join", 5) in proc.events
    assert "kill" in proc.events
    assert proc.alive is False


def test_mark_job_failed_keeps_a_newer_generation(tmp_path: Path) -> None:
    dest = tmp_path / "job"
    dest.mkdir()
    original = {"job_id": "job", "status": "running", "stage": "layout", "generation": 2}
    (dest / "meta.json").write_text(json.dumps(original), encoding="utf-8")
    mark_job_failed(dest, "job", "TimeoutError", 1)
    stored = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert stored["status"] == "running"
    assert stored["stage"] == "layout"
    assert stored["generation"] == 2


def _exit_immediately() -> None:
    return


def _block_for_timeout() -> None:
    time.sleep(30)


def _running_meta(tmp_path: Path) -> tuple[MemoryJobBus, str, int, Path]:
    bus = MemoryJobBus()
    job_id = "job"
    assert bus.enqueue(job_id)
    generation = bus.current_generation(job_id)
    dest = session_job_dir(tmp_path, job_id)
    dest.mkdir(parents=True)
    (dest / "meta.json").write_text(
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
    return bus, job_id, generation, dest


def test_watch_does_not_time_out_a_child_another_thread_already_reaped(tmp_path: Path) -> None:
    """GET's is_alive() can reap the child. waitpid then raises ECHILD and poll()
    leaves returncode unset, so a later is_alive() still reports the child as alive.
    """
    proc = multiprocessing.get_context("spawn").Process(target=_exit_immediately)
    proc.start()
    proc.join(5)
    assert proc.exitcode == 0
    assert proc._popen is not None
    proc._popen.returncode = None
    assert proc.is_alive()

    bus, job_id, generation, dest = _running_meta(tmp_path)
    JobProcesses(data_dir=tmp_path, bus=bus, timeout_sec=30)._watch(proc, job_id, generation)

    stored = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert stored["status"] == "failed"
    assert stored["error"] == "pipeline_failed"
    record = bus.get(job_id)
    assert record is not None
    assert record.error == "pipeline_failed"


def test_watch_marks_a_real_timeout_failed(tmp_path: Path) -> None:
    proc = multiprocessing.get_context("spawn").Process(target=_block_for_timeout)
    proc.start()
    try:
        bus, job_id, generation, dest = _running_meta(tmp_path)
        JobProcesses(data_dir=tmp_path, bus=bus, timeout_sec=0.2)._watch(proc, job_id, generation)
        stored = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
        assert stored["status"] == "failed"
        assert stored["error"] == "TimeoutError"
        record = bus.get(job_id)
        assert record is not None
        assert record.error == "TimeoutError"
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


def test_watch_kills_when_reported_rss_exceeds_the_budget(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "finance_context.api.processes.process_rss_bytes",
        lambda _pid: 10**12,
    )
    proc = multiprocessing.get_context("spawn").Process(target=_sleep_until_killed)
    proc.start()
    try:
        bus, job_id, generation, dest = _running_meta(tmp_path)
        JobProcesses(
            data_dir=tmp_path,
            bus=bus,
            timeout_sec=10,
            memory_budget_bytes=1024,
        )._watch(proc, job_id, generation)
        stored = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
        assert stored["status"] == "failed"
        assert stored["error"] == "workbook_too_large"
        assert "resident" in stored["detail"]
        record = bus.get(job_id)
        assert record is not None
        assert record.error == "workbook_too_large"
        assert not proc.is_alive()
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(5)


def test_watch_kills_a_child_whose_real_rss_exceeds_the_budget(tmp_path: Path) -> None:
    proc = multiprocessing.get_context("spawn").Process(target=_allocate_and_sleep)
    proc.start()
    try:
        bus, job_id, generation, dest = _running_meta(tmp_path)
        JobProcesses(
            data_dir=tmp_path,
            bus=bus,
            timeout_sec=15,
            memory_budget_bytes=32 * 1024 * 1024,
        )._watch(proc, job_id, generation)
        stored = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
        assert stored["error"] == "workbook_too_large"
        assert not proc.is_alive()
    finally:
        if proc.is_alive():
            proc.kill()
            proc.join(5)


def test_watch_interval_is_stored_without_a_fallback(tmp_path: Path) -> None:
    bus = MemoryJobBus()
    kept = JobProcesses(data_dir=tmp_path, bus=bus, timeout_sec=1, watch_interval_sec=0.05)
    assert kept._watch_interval == 0.05
    untouched = JobProcesses(
        data_dir=tmp_path, bus=bus, timeout_sec=1, watch_interval_sec=float("nan")
    )
    assert math.isnan(untouched._watch_interval)


def test_mark_job_failed_keeps_a_terminal_snapshot(tmp_path: Path) -> None:
    dest = tmp_path / "job"
    dest.mkdir()
    for name in ("context.json", "context.md", "graph.json", "graph.md"):
        (dest / name).write_text("{}\n", encoding="utf-8")
    original = {"job_id": "job", "status": "succeeded", "stage": "done", "generation": 3}
    (dest / "meta.json").write_text(json.dumps(original), encoding="utf-8")
    mark_job_failed(dest, "job", "TimeoutError", 3)
    stored = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert stored["status"] == "succeeded"
    assert stored["generation"] == 3
