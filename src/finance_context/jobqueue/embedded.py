"""RQ pool as a child of ``finance-context serve``.

The child is the same ``finance-context worker`` process. It is not a thread
on the API event loop and not an extra uvicorn worker. ``/readyz`` asks
:func:`embedded_worker_ready` so a dead child cannot leave a silent API.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from finance_context.observability import log_event
from finance_context.settings import Settings, write_redis_env
from finance_context.settings_env import ENV_SPECS

_LOGGER = logging.getLogger("finance_context.jobqueue")
_registry_lock = threading.Lock()


class EmbeddedWorker:
    """Spawn ``finance-context worker`` and start it again after it exits."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._proc: subprocess.Popen[bytes] | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Spawn once, before the caller accepts traffic, then watch the child."""
        self._spawn()
        self._thread = threading.Thread(
            target=self._supervise,
            name="embedded-worker",
            daemon=True,
        )
        self._thread.start()

    def healthy(self) -> bool:
        with self._lock:
            proc = self._proc
        return proc is not None and proc.poll() is None

    def pid(self) -> int | None:
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return None
        return proc.pid

    def stop(self) -> None:
        """SIGTERM the child, wait both grace periods, then SIGKILL the tree.

        A job still running at the end of that window is left to the existing
        lost-job recovery (``queue_lost`` / ``process_lost`` / ``JOB_RETRY_ON_LOST``).
        """
        self._stop.set()
        with self._lock:
            proc = self._proc
            self._proc = None
        if proc is not None:
            _shutdown_proc(proc, self.settings)
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_stop_join_timeout(self.settings))

    def _supervise(self) -> None:
        while not self._stop.is_set():
            proc = self._current()
            if proc is None:
                if self._stop.wait(self.settings.embedded_worker_restart_backoff_sec):
                    return
                self._spawn()
                continue
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                continue
            if self._stop.is_set():
                return
            _kill_group(proc.pid)
            log_event(
                _LOGGER,
                logging.WARNING,
                "embedded_worker_exit",
                "embedded worker exited; restarting after backoff",
                pid=proc.pid,
                returncode=proc.returncode,
                backoff_sec=self.settings.embedded_worker_restart_backoff_sec,
            )
            if self._stop.wait(self.settings.embedded_worker_restart_backoff_sec):
                return
            self._spawn()

    def _current(self) -> subprocess.Popen[bytes] | None:
        with self._lock:
            return self._proc

    def _spawn(self) -> None:
        if self._stop.is_set():
            return
        try:
            proc = subprocess.Popen(
                _worker_argv(),
                env=_child_env(self.settings),
                start_new_session=True,
            )
        except OSError as exc:
            log_event(
                _LOGGER,
                logging.ERROR,
                "embedded_worker_spawn_failed",
                "embedded worker failed to start",
                error=str(exc),
            )
            return
        with self._lock:
            if self._stop.is_set():
                self._proc = None
            else:
                self._proc = proc
                log_event(
                    _LOGGER,
                    logging.INFO,
                    "embedded_worker_start",
                    "embedded worker started",
                    pid=proc.pid,
                )
                return
        _shutdown_proc(proc, self.settings)


_supervisor: EmbeddedWorker | None = None


def start_for_serve(settings: Settings) -> EmbeddedWorker | None:
    """Start the child when ``EMBEDDED_WORKER`` is true. None leaves ``/readyz`` alone."""
    global _supervisor
    if not settings.embedded_worker:
        return None
    worker = EmbeddedWorker(settings)
    with _registry_lock:
        _supervisor = worker
    worker.start()
    return worker


def stop_for_serve(worker: EmbeddedWorker | None) -> None:
    """Stop the child this process started and drop it from ``/readyz``."""
    global _supervisor
    if worker is None:
        return
    worker.stop()
    with _registry_lock:
        if _supervisor is worker:
            _supervisor = None


def embedded_worker_ready() -> bool | None:
    """None when serve is not supervising. False while the child is down."""
    with _registry_lock:
        worker = _supervisor
    if worker is None:
        return None
    return worker.healthy()


def _worker_argv() -> list[str]:
    exe = Path(sys.executable).with_name("finance-context")
    if exe.is_file():
        return [str(exe), "worker"]
    return [sys.executable, "-m", "finance_context.cli", "worker"]


def _child_env(settings: Settings) -> dict[str, str]:
    """The worker process loads ``Settings`` itself, so export this object's fields.

    Redis credentials go through :func:`write_redis_env`. A ``SecretStr`` must
    not be stringified, and an empty username or password must not be exported.
    """
    env = os.environ.copy()
    for spec in ENV_SPECS:
        if spec.field_name in {"redis_url", "redis_username", "redis_password"}:
            continue
        value = getattr(settings, spec.field_name)
        if value is None:
            env.pop(spec.env_name, None)
            continue
        if isinstance(value, bool):
            env[spec.env_name] = "true" if value else "false"
        else:
            env[spec.env_name] = str(value)
    write_redis_env(env, settings)
    # The pool arms PR_SET_PDEATHSIG so it exits with this server.
    env["FINANCE_CONTEXT_EMBEDDED_WORKER"] = "1"
    return env


def _stop_join_timeout(settings: Settings) -> float:
    return (
        settings.job_terminate_grace_sec
        + settings.job_kill_grace_sec
        + settings.embedded_worker_restart_backoff_sec
        + 5
    )


def _shutdown_proc(proc: subprocess.Popen[bytes], settings: Settings) -> None:
    """SIGTERM the pool, keep a live job for both grace periods, then SIGKILL it.

    Horses call ``os.setpgrp`` and survive the worker's process group. The
    descendant list is taken first so those processes are still killed after
    the grace window. An idle pool that exits on SIGTERM does not wait it out.
    """
    if proc.poll() is not None:
        _kill_tree(proc.pid)
        return
    descendants = _descendants(proc.pid)
    _signal(proc.pid, signal.SIGTERM)
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    grace = settings.job_terminate_grace_sec + settings.job_kill_grace_sec
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if proc.poll() is not None and not any(_alive(pid) for pid in descendants):
            break
        time.sleep(0.05)
    _kill_tree(proc.pid)
    for pid in descendants:
        _signal(pid, signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log_event(
            _LOGGER,
            logging.ERROR,
            "embedded_worker_stop_timeout",
            "embedded worker did not exit after SIGKILL",
            pid=proc.pid,
        )


def _kill_tree(pid: int) -> None:
    descendants = _descendants(pid)
    _kill_group(pid)
    for member in [pid, *descendants]:
        _signal(member, signal.SIGKILL)


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal(pid: int, sig: int) -> None:
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _descendants(root: int) -> list[int]:
    """pid's children, from ``ps``. Horses call ``os.setpgrp`` and leave the group."""
    try:
        listed = subprocess.check_output(["ps", "-axo", "pid=,ppid="], text=True)
    except (OSError, subprocess.CalledProcessError):
        return []
    children: dict[int, list[int]] = {}
    for line in listed.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
    found: list[int] = []
    stack = list(children.get(root, []))
    me = os.getpid()
    while stack:
        current = stack.pop()
        if current in {root, me}:
            continue
        found.append(current)
        stack.extend(children.get(current, []))
    return found
