from __future__ import annotations

import json
from pathlib import Path

from finance_context.api.processes import reap_process
from finance_context.app.pipeline import mark_job_failed


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
    original = {"job_id": "job", "status": "running", "stage": "mapping", "generation": 2}
    (dest / "meta.json").write_text(json.dumps(original), encoding="utf-8")
    mark_job_failed(dest, "job", "TimeoutError", 1)
    stored = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert stored["status"] == "running"
    assert stored["stage"] == "mapping"
    assert stored["generation"] == 2


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
