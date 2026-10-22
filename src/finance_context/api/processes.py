from __future__ import annotations

import json
import logging
import multiprocessing
import os
import threading
import time
from multiprocessing.connection import wait as wait_for_exit
from pathlib import Path

from finance_context.adapters.memory_bus import MemoryJobBus
from finance_context.app.pipeline import mark_job_failed, run_job_process
from finance_context.memory import over_rss_detail, process_rss_bytes
from finance_context.observability import log_event
from finance_context.store.paths import LOCAL_SESSION, session_job_dir

_LOGGER = logging.getLogger("finance_context.processes")

_TERMINAL = {"succeeded", "degraded", "needs_input", "failed"}


def reap_process(proc: object, *, join_sec: float = 5) -> None:
    """Stop a child. SIGTERM first, then SIGKILL if it is still alive."""
    if proc is None:
        return
    alive = getattr(proc, "is_alive", None)
    join = getattr(proc, "join", None)
    if not callable(alive) or not alive():
        if callable(join):
            join(join_sec)
        return
    terminate = getattr(proc, "terminate", None)
    if callable(terminate):
        terminate()
    if callable(join):
        join(join_sec)
    if alive():
        kill = getattr(proc, "kill", None)
        if callable(kill):
            kill()
        if callable(join):
            join(join_sec)


class JobProcesses:
    """One spawned interpreter per accepted job. The parent only watches the process."""

    def __init__(
        self,
        *,
        data_dir: Path,
        bus: MemoryJobBus,
        timeout_sec: float,
        max_concurrent_jobs: int = 2,
        session_id: str = LOCAL_SESSION,
        memory_budget_bytes: int | None = None,
        unusable_detail: str | None = None,
        watch_interval_sec: float = 0.2,
        malloc_arena_max: int | None = None,
    ) -> None:
        self._data_dir = str(data_dir)
        self._session_id = session_id or LOCAL_SESSION
        self._bus = bus
        self._timeout = timeout_sec
        self._max = max_concurrent_jobs if max_concurrent_jobs > 0 else 2
        self._budget = (
            memory_budget_bytes if memory_budget_bytes and memory_budget_bytes > 0 else None
        )
        self._unusable_detail = unusable_detail
        self._watch_interval = watch_interval_sec
        self._malloc_arena_max = malloc_arena_max if malloc_arena_max else None
        self._procs: dict[str, multiprocessing.Process] = {}
        self._generations: dict[str, int] = {}
        self._closed: set[tuple[str, int]] = set()
        self._reserved: set[str] = set()
        self._lock = threading.Lock()

    def alive_count(self) -> int:
        with self._lock:
            return sum(1 for proc in self._procs.values() if proc.is_alive())

    def try_acquire(self, job_id: str) -> bool:
        """Reserve a process slot. A live or already reserved job keeps its slot."""
        with self._lock:
            if job_id in self._reserved or self._process_alive(job_id):
                return True
            if self._occupied() >= self._max:
                return False
            self._reserved.add(job_id)
            return True

    def release_reservation(self, job_id: str) -> None:
        with self._lock:
            self._reserved.discard(job_id)

    def launch(self, job_id: str) -> bool:
        """Start the child. False when this call would pass the process cap."""
        generation = self._bus.current_generation(job_id)
        if self._unusable_detail:
            dest = session_job_dir(Path(self._data_dir), job_id, self._session_id)
            mark_job_failed(
                dest,
                job_id,
                "workbook_too_large",
                generation,
                detail=self._unusable_detail,
            )
            self._bus.abandon(job_id, generation, "workbook_too_large")
            self.release_reservation(job_id)
            return True
        with self._lock:
            previous = self._procs.get(job_id)
            reserved = job_id in self._reserved
            replacing = previous is not None and previous.is_alive()
            if not reserved and not replacing and self._occupied() >= self._max:
                return False
        if previous is not None:
            reap_process(previous)
        ctx = multiprocessing.get_context("spawn")
        proc = ctx.Process(
            target=run_job_process,
            args=(self._data_dir, job_id, generation, self._session_id),
            name=f"finance-job-{job_id[:8]}",
        )
        started = False
        try:
            with self._lock:
                previous_arena = os.environ.get("MALLOC_ARENA_MAX")
                if self._malloc_arena_max:
                    os.environ["MALLOC_ARENA_MAX"] = str(self._malloc_arena_max)
                else:
                    os.environ.pop("MALLOC_ARENA_MAX", None)
                try:
                    proc.start()
                    started = True
                finally:
                    if previous_arena is None:
                        os.environ.pop("MALLOC_ARENA_MAX", None)
                    else:
                        os.environ["MALLOC_ARENA_MAX"] = previous_arena
        finally:
            if not started:
                self.release_reservation(job_id)
        with self._lock:
            self._procs[job_id] = proc
            self._generations[job_id] = generation
            self._reserved.discard(job_id)
        threading.Thread(
            target=self._watch,
            args=(proc, job_id, generation),
            name=f"finance-job-watch-{job_id[:8]}",
            daemon=True,
        ).start()
        return True

    def _process_alive(self, job_id: str) -> bool:
        proc = self._procs.get(job_id)
        return proc is not None and proc.is_alive()

    def _occupied(self) -> int:
        alive = {job_id for job_id, proc in self._procs.items() if proc.is_alive()}
        return len(alive | self._reserved)

    def is_alive(self, job_id: str) -> bool:
        with self._lock:
            proc = self._procs.get(job_id)
        return proc is not None and proc.is_alive()

    def stop(self, job_id: str) -> None:
        with self._lock:
            proc = self._procs.get(job_id)
            generation = self._generations.get(job_id, 0)
            self._closed.add((job_id, generation))
        if proc is not None:
            reap_process(proc)
        with self._lock:
            if self._procs.get(job_id) is proc:
                self._procs.pop(job_id, None)
            self._reserved.discard(job_id)
        if generation:
            self._bus.expire(job_id, generation)

    def stop_all(self) -> None:
        with self._lock:
            job_ids = list(self._procs)
        for job_id in job_ids:
            self.stop(job_id)

    def _watch(self, proc: multiprocessing.Process, job_id: str, generation: int) -> None:
        try:
            timed_out, over_budget, rss_seen = self._wait_for_child(proc)
            if timed_out:
                reap_process(proc)
            if self._is_closed(job_id, generation) or not self._bus.owns(job_id, generation):
                return
            dest = session_job_dir(Path(self._data_dir), job_id, self._session_id)
            if over_budget:
                detail = over_rss_detail(rss_seen, int(self._budget or 0))
                mark_job_failed(
                    dest, job_id, "workbook_too_large", generation, detail=detail
                )
                log_event(
                    _LOGGER,
                    logging.WARNING,
                    "job_failed",
                    f"workbook_too_large: {detail}",
                    error_code="workbook_too_large",
                    reason=detail,
                    budget_bytes=self._budget,
                )
                self._bus.abandon(job_id, generation, "workbook_too_large")
                return
            if timed_out:
                mark_job_failed(dest, job_id, "TimeoutError", generation)
                self._bus.expire(job_id, generation)
                return
            self._finish_from_disk(dest, job_id, generation)
        finally:
            self._drop_process(job_id, generation)

    def _wait_for_child(self, proc: multiprocessing.Process) -> tuple[bool, bool, int]:
        """Block until the child exits, times out, or crosses the RSS budget.

        Timeout comes from the sentinel, not is_alive(). A request thread can
        reap the child first; waitpid then raises ECHILD and poll() still
        reports the process as alive.
        """
        deadline = None
        if self._timeout is not None and self._timeout > 0:
            deadline = time.monotonic() + self._timeout
        while True:
            if deadline is None:
                slice_s = self._watch_interval
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return True, False, 0
                slice_s = min(self._watch_interval, remaining)
            if wait_for_exit([proc.sentinel], slice_s):
                proc.join()
                return False, False, 0
            if self._budget is None or not proc.is_alive():
                continue
            rss = process_rss_bytes(proc.pid)
            if rss is not None and rss > self._budget:
                proc.kill()
                proc.join(5)
                return False, True, rss

    def _drop_process(self, job_id: str, generation: int) -> None:
        with self._lock:
            if self._generations.get(job_id) == generation:
                self._procs.pop(job_id, None)

    def _is_closed(self, job_id: str, generation: int) -> bool:
        with self._lock:
            return (job_id, generation) in self._closed

    def _finish_from_disk(self, dest: Path, job_id: str, generation: int) -> None:
        meta = _read_meta(dest / "meta.json")
        status = meta.get("status")
        if status not in _TERMINAL:
            mark_job_failed(
                dest,
                job_id,
                str(meta.get("error") or "pipeline_failed"),
                generation,
            )
            meta = _read_meta(dest / "meta.json")
            status = meta.get("status")
            if status not in _TERMINAL:
                status = "failed"
        stage = "failed" if status == "failed" else str(meta.get("stage") or "done")
        error = meta.get("error") if status == "failed" else None
        if status == "failed" and not error:
            error = "pipeline_failed"
        self._bus.set_terminal(
            job_id,
            status,  # type: ignore[arg-type]
            stage=stage,
            error=str(error) if error else None,
            generation=generation,
        )


def _read_meta(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
