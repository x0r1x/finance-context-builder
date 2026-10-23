"""Notice when the worker or the horse has died.

RQ's horse calls ``os.setpgrp()`` and can outlive a SIGKILLed worker (rq#2495).
The spawn child's parent is that horse, so ``getppid()`` inside the child does
not see the worker. The horse watches its own parent. On Linux,
``PR_SET_PDEATHSIG`` is the fast path; the thread covers macOS.

The horse watch runs only when ``FINANCE_CONTEXT_RQ_HORSE`` is ``1``. A
``SimpleWorker`` test, which runs the job in-process, does not set it and must
not ``os._exit`` the pytest process.
"""

from __future__ import annotations

import ctypes
import os
import signal
import sys
import threading
import time

HORSE_ENV = "FINANCE_CONTEXT_RQ_HORSE"

_PR_SET_PDEATHSIG = 1
_LIBC = ctypes.CDLL("libc.so.6", use_errno=True) if sys.platform == "linux" else None
if _LIBC is not None:
    _LIBC.prctl.argtypes = (
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    )
    _LIBC.prctl.restype = ctypes.c_int


def arm_horse_death(interval_sec: float) -> None:
    """Watch the worker. No-op unless this process is an RQ horse."""
    if os.environ.get(HORSE_ENV) != "1":
        return
    _arm_pdeathsig(signal.SIGTERM)
    _start_parent_watch(interval_sec)


def arm_child_death(interval_sec: float) -> None:
    """Watch the horse. The spawn child calls this before the pipeline."""
    _arm_pdeathsig(signal.SIGTERM)
    _start_parent_watch(interval_sec)


def arm_embedded_parent(interval_sec: float) -> None:
    """Ask the embedded pool to exit when ``finance-context serve`` dies.

    ``PR_SET_PDEATHSIG`` is cleared on fork, so only this pool process is
    armed. SIGTERM lets the pool stop its workers. ``os._exit`` would leave
    those workers behind.
    """
    _arm_pdeathsig(signal.SIGTERM)
    parent = os.getppid()
    if parent <= 1:
        return
    interval = interval_sec if interval_sec > 0 else 0.05

    def _watch() -> None:
        while True:
            time.sleep(interval)
            if os.getppid() != parent:
                os.kill(os.getpid(), signal.SIGTERM)
                return

    threading.Thread(target=_watch, name="finance-embedded-parent", daemon=True).start()


def _arm_pdeathsig(sig: int) -> None:
    """Ask Linux to signal this process when its parent dies.

    SIGTERM, not SIGKILL: RQ's own death penalty already uses SIGALRM.
    A parent that is already PID 1 (the container entrypoint) is not dead.
    """
    if _LIBC is None:
        return
    parent = os.getppid()
    if parent <= 1:
        return
    if _LIBC.prctl(_PR_SET_PDEATHSIG, sig, 0, 0, 0) != 0:
        return
    if os.getppid() != parent:
        os.kill(os.getpid(), sig)


def _start_parent_watch(interval_sec: float) -> None:
    parent = os.getppid()
    interval = interval_sec if interval_sec > 0 else 0.05

    def _watch() -> None:
        while True:
            time.sleep(interval)
            if os.getppid() != parent:
                os._exit(1)

    threading.Thread(target=_watch, name="finance-parent-watch", daemon=True).start()
