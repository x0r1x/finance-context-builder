from __future__ import annotations

import json
import multiprocessing
import threading
from pathlib import Path

from finance_context.adapters.memory_bus import MemoryJobBus
from finance_context.app.pipeline import mark_job_failed, run_job_process

_TERMINAL = {"succeeded", "degraded", "needs_input", "failed"}


class JobProcesses:
    """One spawned interpreter per accepted job. The parent only watches the process."""

    def __init__(self, *, data_dir: Path, bus: MemoryJobBus, timeout_sec: float) -> None:
        self._data_dir = str(data_dir)
        self._bus = bus
        self._timeout = timeout_sec
        self._procs: dict[str, multiprocessing.Process] = {}
        self._generations: dict[str, int] = {}
        self._closed: set[tuple[str, int]] = set()
        self._lock = threading.Lock()

    def launch(self, job_id: str) -> None:
        generation = self._bus.current_generation(job_id)
        ctx = multiprocessing.get_context("spawn")
        proc = ctx.Process(
            target=run_job_process,
            args=(self._data_dir, job_id),
            name=f"finance-job-{job_id[:8]}",
        )
        proc.start()
        with self._lock:
            previous = self._procs.get(job_id)
            self._procs[job_id] = proc
            self._generations[job_id] = generation
        if previous is not None and previous.is_alive():
            previous.terminate()
        threading.Thread(
            target=self._watch,
            args=(proc, job_id, generation),
            name=f"finance-job-watch-{job_id[:8]}",
            daemon=True,
        ).start()

    def is_alive(self, job_id: str) -> bool:
        with self._lock:
            proc = self._procs.get(job_id)
        return proc is not None and proc.is_alive()

    def stop(self, job_id: str) -> None:
        with self._lock:
            proc = self._procs.get(job_id)
            generation = self._generations.get(job_id, 0)
            self._closed.add((job_id, generation))
        if proc is not None and proc.is_alive():
            proc.terminate()
            proc.join(5)
        if generation:
            self._bus.expire(job_id, generation)

    def stop_all(self) -> None:
        with self._lock:
            job_ids = list(self._procs)
        for job_id in job_ids:
            self.stop(job_id)

    def _watch(self, proc: multiprocessing.Process, job_id: str, generation: int) -> None:
        timeout = self._timeout
        if timeout is None or timeout <= 0:
            proc.join()
            timed_out = False
        else:
            proc.join(timeout)
            timed_out = proc.is_alive()
        if timed_out:
            proc.terminate()
            proc.join(5)
        if self._is_closed(job_id, generation) or not self._bus.owns(job_id, generation):
            return
        dest = Path(self._data_dir) / "jobs" / job_id
        if timed_out:
            mark_job_failed(dest, job_id, "TimeoutError")
            self._bus.expire(job_id, generation)
            return
        self._finish_from_disk(dest, job_id, generation)

    def _is_closed(self, job_id: str, generation: int) -> bool:
        with self._lock:
            return (job_id, generation) in self._closed

    def _finish_from_disk(self, dest: Path, job_id: str, generation: int) -> None:
        meta = _read_meta(dest / "meta.json")
        status = meta.get("status")
        if status not in _TERMINAL:
            mark_job_failed(dest, job_id, str(meta.get("error") or "pipeline_failed"))
            status = "failed"
            meta = _read_meta(dest / "meta.json")
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
