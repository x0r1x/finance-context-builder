"""Spawn one pipeline child and watch its RSS, timeout, and cgroup OOM."""

from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
import signal
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from multiprocessing.connection import wait as wait_for_exit
from pathlib import Path

from finance_context.app.pipeline import SHARED_CACHE_BUSY_EXIT, run_queued_job
from finance_context.jobqueue.parents import arm_child_death
from finance_context.memory import cgroup_oom_kill_count, process_rss_bytes
from finance_context.settings import Settings
from finance_context.store.fs import write_json
from finance_context.store.paths import session_job_dir

# os.environ is process-global. Two horses must not start children at once.
_ARENA_LOCK = threading.Lock()

RUN_LOCK = ".run.lock"
HORSE_FILE = ".horse.json"
HORSE_LOCK = ".horse.lock"


@dataclass(frozen=True)
class ChildResult:
    timed_out: bool = False
    over_budget: bool = False
    rss_bytes: int = 0
    kernel_oom: bool = False
    shared_cache_busy: bool = False


def hold_for_probe(seconds: float) -> None:
    """Stay alive so a test can read this process's environ. Not a pipeline."""
    time.sleep(seconds)


def run_child(
    data_dir: str,
    job_id: str,
    generation: int,
    session_id: str,
    horse_pid: int,
) -> None:
    """Hold the run lock, watch the horse, and run one book.

    The parent check and the death watch run before the blocking flock. A
    child whose horse is already gone must not sit on the lock, then start
    the pipeline after the previous holder releases it. The lock file is
    never unlinked. GET treats a free lock plus a missing RQ job as
    ``queue_lost``. ``os._exit`` from the parent watch skips this
    ``finally``; the kernel drops the flock. A busy shared book exits
    :data:`SHARED_CACHE_BUSY_EXIT` after this ``finally`` releases the lock.
    """
    if os.getppid() != horse_pid:
        os._exit(1)
    arm_child_death(Settings().job_parent_watch_interval_sec)
    dest = session_job_dir(Path(data_dir), job_id, session_id)
    dest.mkdir(parents=True, exist_ok=True)
    handle = (dest / RUN_LOCK).open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        # The horse can die while this call is blocked on the previous child.
        if os.getppid() != horse_pid:
            os._exit(1)
        run_queued_job(data_dir, job_id, generation, session_id)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def hold_horse_lock(dest: Path):
    """Hold ``.horse.lock`` until ``execute_job`` returns.

    The API and the worker do not share a PID namespace. The lock is on the
    data volume both of them mount. A killed horse drops it with the process.
    """
    dest.mkdir(parents=True, exist_ok=True)
    handle = (dest / HORSE_LOCK).open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def release_horse_record(dest: Path) -> None:
    """Remove the generation record before a voluntary retry return.

    The caller still holds ``.horse.lock``. A kill before this unlink leaves
    the record, so a free lock means the horse died. A clean admission retry
    removes the record so the gap before RQ leaves ``started`` is not lost.
    """
    try:
        (dest / HORSE_FILE).unlink()
    except FileNotFoundError:
        return


def write_horse_owner(dest: Path, generation: int) -> None:
    """Record this generation while ``.horse.lock`` is already held."""
    dest.mkdir(parents=True, exist_ok=True)
    write_json(dest / HORSE_FILE, {"pid": os.getpid(), "generation": generation})


def horse_lock_held(dest: Path) -> bool | None:
    """True when a horse holds ``.horse.lock``.

    False when the file is missing or the lock is free. None when the file
    cannot be inspected. An access error is not a dead horse.
    """
    path = dest / HORSE_LOCK
    try:
        exists = path.is_file()
    except OSError:
        return None
    if not exists:
        return False
    try:
        handle = path.open("rb")
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:
            return None
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False
    finally:
        handle.close()


def horse_owner_alive(dest: Path, generation: int) -> bool | None:
    """True when this generation's horse holds ``.horse.lock``.

    False when the record exists and the lock is free: the horse exited.
    None when this generation has no record yet, or the lock file cannot be
    inspected. That is not a dead worker.
    """
    path = dest / HORSE_FILE
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict) or loaded.get("generation") != generation:
        return None
    return horse_lock_held(dest)


def run_lock_busy(dest: Path) -> bool:
    """True when the spawn child holds ``.run.lock``.

    Does not create the file. A missing lock is free.
    """
    path = dest / RUN_LOCK
    if not path.is_file():
        return False
    try:
        handle = path.open("rb")
    except OSError:
        return False
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def spawn_child(
    target: object,
    args: tuple[object, ...],
    *,
    malloc_arena_max: int | None,
    name: str,
) -> multiprocessing.Process:
    """Start a spawn-context child with ``MALLOC_ARENA_MAX`` set for the exec.

    glibc reads the variable at exec. It is set on this process only for the
    ``start()`` call, then restored. The class lock keeps two overlapping
    starts from publishing each other's value.
    """
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=target, args=args, name=name)
    with _ARENA_LOCK:
        previous = os.environ.get("MALLOC_ARENA_MAX")
        if malloc_arena_max:
            os.environ["MALLOC_ARENA_MAX"] = str(malloc_arena_max)
        try:
            proc.start()
        finally:
            if previous is None:
                os.environ.pop("MALLOC_ARENA_MAX", None)
            else:
                os.environ["MALLOC_ARENA_MAX"] = previous
    return proc


def stop_child(
    proc: multiprocessing.Process | None,
    *,
    terminate_grace_sec: float,
    kill_grace_sec: float,
) -> None:
    """SIGTERM, then SIGKILL if the child is still alive after the first pause."""
    if proc is None:
        return
    if not proc.is_alive():
        proc.join(terminate_grace_sec)
        return
    proc.terminate()
    proc.join(terminate_grace_sec)
    if proc.is_alive():
        proc.kill()
        proc.join(kill_grace_sec)


def watch_child(
    proc: multiprocessing.Process,
    *,
    timeout_sec: float,
    budget_bytes: int | None,
    watch_interval_sec: float,
    terminate_grace_sec: float,
    kill_grace_sec: float,
) -> ChildResult:
    """Block until the child exits, times out, crosses the RSS cap, or is OOM-killed.

    Timeout comes from the sentinel, not ``is_alive()``. A waiter can reap the
    child first; ``waitpid`` then raises ``ECHILD`` and ``poll()`` still reports
    the process as alive.
    """
    oom_before = cgroup_oom_kill_count()
    deadline = None
    if timeout_sec > 0:
        deadline = time.monotonic() + timeout_sec
    while True:
        if deadline is None:
            slice_s = watch_interval_sec
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stop_child(
                    proc,
                    terminate_grace_sec=terminate_grace_sec,
                    kill_grace_sec=kill_grace_sec,
                )
                return _with_oom(
                    ChildResult(timed_out=True),
                    oom_before,
                    proc.exitcode,
                )
            slice_s = min(watch_interval_sec, remaining)
        if wait_for_exit([proc.sentinel], slice_s):
            proc.join()
            return _with_oom(ChildResult(), oom_before, proc.exitcode)
        # The oom_kill counter is the whole cgroup. Another slot dying must
        # not abandon a child that is still running.
        if not proc.is_alive():
            proc.join()
            return _with_oom(ChildResult(), oom_before, proc.exitcode)
        if budget_bytes is None:
            continue
        rss = process_rss_bytes(proc.pid)
        if rss is not None and rss > budget_bytes:
            proc.kill()
            proc.join(kill_grace_sec)
            # Our SIGKILL is not a cgroup oom_kill. A counter that moved anyway
            # means the kernel got there too; that result wins.
            if _oom_increased(oom_before):
                return ChildResult(kernel_oom=True, rss_bytes=rss)
            return ChildResult(over_budget=True, rss_bytes=rss)


def _with_oom(
    result: ChildResult,
    oom_before: int | None,
    exitcode: int | None,
) -> ChildResult:
    """Count a cgroup oom only when this child itself died by SIGKILL.

    ``memory.events`` is the whole cgroup. Another slot can move the counter
    while this child exits on its own. A clean exit stays a clean exit.
    """
    if exitcode == SHARED_CACHE_BUSY_EXIT:
        return ChildResult(shared_cache_busy=True)
    if result.over_budget or not _oom_increased(oom_before):
        return result
    if exitcode != -signal.SIGKILL:
        return result
    return ChildResult(kernel_oom=True, rss_bytes=result.rss_bytes)


def _oom_increased(before: int | None) -> bool:
    if before is None:
        return False
    after = cgroup_oom_kill_count()
    return after is not None and after > before


def read_meta(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
