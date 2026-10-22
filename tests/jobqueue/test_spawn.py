from __future__ import annotations

import fcntl
import os
import subprocess
import threading
import time
from pathlib import Path

from finance_context.jobqueue.spawn import (
    hold_for_probe,
    run_lock_busy,
    spawn_child,
    stop_child,
)


def _live_environ(pid: int) -> str:
    path = Path(f"/proc/{pid}/environ")
    if path.exists():
        try:
            raw = path.read_bytes()
        except OSError:
            raw = b""
        if raw:
            return raw.replace(b"\0", b"\n").decode("utf-8", "replace")
    completed = subprocess.run(
        ["ps", "eww", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _wait_for_token(pid: int, token: str) -> str:
    seen = ""
    deadline = time.time() + 5
    while time.time() < deadline:
        seen = _live_environ(pid)
        if token in seen:
            return seen
        time.sleep(0.05)
    return seen


def test_live_child_environ_has_malloc_arena_max(monkeypatch) -> None:
    monkeypatch.delenv("MALLOC_ARENA_MAX", raising=False)
    proc = spawn_child(hold_for_probe, (30,), malloc_arena_max=2, name="arena-probe")
    try:
        seen = _wait_for_token(proc.pid, "MALLOC_ARENA_MAX=2")
        assert "MALLOC_ARENA_MAX=2" in seen
        assert os.environ.get("MALLOC_ARENA_MAX") is None
    finally:
        stop_child(proc, terminate_grace_sec=1, kill_grace_sec=1)


def test_disabled_arena_keeps_the_parent_value_on_the_live_child(monkeypatch) -> None:
    monkeypatch.setenv("MALLOC_ARENA_MAX", "8")
    proc = spawn_child(hold_for_probe, (30,), malloc_arena_max=None, name="arena-keep")
    try:
        seen = _wait_for_token(proc.pid, "MALLOC_ARENA_MAX=8")
        assert "MALLOC_ARENA_MAX=8" in seen
        assert "MALLOC_ARENA_MAX=2" not in seen
        assert os.environ["MALLOC_ARENA_MAX"] == "8"
    finally:
        stop_child(proc, terminate_grace_sec=1, kill_grace_sec=1)


def test_arena_starts_do_not_overlap(monkeypatch) -> None:
    monkeypatch.setenv("MALLOC_ARENA_MAX", "8")
    seen: list[str | None] = []
    first_inside = threading.Event()
    release_first = threading.Event()

    class _Proc:
        def start(self) -> None:
            value = os.environ.get("MALLOC_ARENA_MAX")
            index = len(seen)
            seen.append(value)
            if index == 0:
                first_inside.set()
                assert release_first.wait(5)

        def is_alive(self) -> bool:
            return False

    class _Context:
        def Process(self, *, target, args, name):
            del target, args, name
            return _Proc()

    monkeypatch.setattr(
        "finance_context.jobqueue.spawn.multiprocessing.get_context",
        lambda _method: _Context(),
    )
    errors: list[BaseException] = []

    def _go(arena: int) -> None:
        try:
            spawn_child(hold_for_probe, (1,), malloc_arena_max=arena, name=f"arena-{arena}")
        except BaseException as exc:
            errors.append(exc)
            release_first.set()

    threads = [
        threading.Thread(target=_go, args=(2,)),
        threading.Thread(target=_go, args=(5,)),
    ]
    for thread in threads:
        thread.start()
    assert first_inside.wait(5)
    # The second start is blocked on the arena lock, inside start() only after
    # the first releases. Give it a moment to reach the lock.
    time.sleep(0.1)
    release_first.set()
    for thread in threads:
        thread.join(5)
    assert errors == []
    assert sorted(seen) == ["2", "5"]
    assert os.environ["MALLOC_ARENA_MAX"] == "8"


def test_run_lock_busy_does_not_create_a_missing_file(tmp_path: Path) -> None:
    assert run_lock_busy(tmp_path) is False
    assert not (tmp_path / ".run.lock").exists()


def test_run_lock_busy_while_held(tmp_path: Path) -> None:
    path = tmp_path / ".run.lock"
    path.touch()
    handle = path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    try:
        assert run_lock_busy(tmp_path) is True
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    assert run_lock_busy(tmp_path) is False
