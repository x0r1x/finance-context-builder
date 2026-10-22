"""rq#2495: a horse that called setpgrp must not outlive a SIGKILLed worker."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from finance_context.jobqueue.parents import arm_horse_death


def _dead(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def test_horse_watch_stays_disarmed_without_the_horse_env() -> None:
    arm_horse_death(0.05)
    assert os.environ.get("FINANCE_CONTEXT_RQ_HORSE") != "1"


def test_sigkill_of_the_worker_reaps_horse_and_grandchild(tmp_path: Path) -> None:
    ready = tmp_path / "pids.json"
    script = Path(__file__).with_name("parent_repro.py")
    proc = subprocess.Popen(
        [sys.executable, str(script), str(ready)],
        stderr=subprocess.PIPE,
        text=True,
    )
    horse = child = None
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not ready.is_file():
            if proc.poll() is not None:
                _stderr = proc.stderr.read() if proc.stderr is not None else ""
                pytest.fail(f"repro exited {proc.returncode}: {_stderr}")
            time.sleep(0.05)
        assert ready.is_file()
        pids = json.loads(ready.read_text(encoding="utf-8"))
        horse = int(pids["horse"])
        child = int(pids["child"])
        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        deadline = time.time() + 5
        while time.time() < deadline:
            if _dead(horse) and _dead(child):
                return
            time.sleep(0.05)
        pytest.fail(f"horse={horse} child={child} outlived the worker")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        for pid in (horse, child):
            if pid is None:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
