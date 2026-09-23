from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field

from finance_context.models.context import JobStatus


@dataclass
class JobRecord:
    job_id: str
    status: JobStatus = "queued"
    stage: str = "queued"
    error: str | None = None
    cancel: bool = False
    generation: int = 0


@dataclass
class MemoryJobBus:
    """In-process queue. Replace with Redis for multi-replica deploys."""

    _queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    _live: dict[str, JobRecord] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    async def enqueue(self, job_id: str) -> bool:
        """Register a new run. False when this job is already queued or running."""
        with self._lock:
            rec = self._live.get(job_id)
            if rec is not None and rec.status in {"queued", "running"}:
                return False
            generation = 1 if rec is None else rec.generation + 1
            self._live[job_id] = JobRecord(job_id=job_id, generation=generation)
        await self._queue.put(job_id)
        return True

    async def claim(self) -> str | None:
        job_id = await self._queue.get()
        with self._lock:
            rec = self._live.get(job_id)
            if rec is not None and not rec.cancel and rec.status in {"queued", "running"}:
                rec.status = "running"
                rec.stage = "parse"
        return job_id

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            rec = self._live.get(job_id)
            if rec is None:
                return None
            return JobRecord(
                job_id=rec.job_id,
                status=rec.status,
                stage=rec.stage,
                error=rec.error,
                cancel=rec.cancel,
                generation=rec.generation,
            )

    def current_generation(self, job_id: str) -> int:
        with self._lock:
            rec = self._live.get(job_id)
            return rec.generation if rec is not None else 0

    def owns(self, job_id: str, generation: int) -> bool:
        with self._lock:
            rec = self._live.get(job_id)
            return rec is not None and rec.generation == generation

    def is_cancelled(self, job_id: str, generation: int) -> bool:
        with self._lock:
            rec = self._live.get(job_id)
            if rec is None or rec.generation != generation:
                return True
            return rec.cancel

    def set_progress(self, job_id: str, stage: str, *, generation: int | None = None) -> None:
        with self._lock:
            rec = self._live.get(job_id)
            if rec is None:
                rec = JobRecord(job_id=job_id, status="running", generation=generation or 0)
                self._live[job_id] = rec
            if generation is not None and rec.generation != generation:
                return
            if rec.cancel or rec.status not in {"queued", "running"}:
                return
            rec.stage = stage
            rec.status = "running"

    def expire(self, job_id: str, generation: int) -> bool:
        with self._lock:
            rec = self._live.get(job_id)
            if rec is None or rec.generation != generation:
                return False
            if rec.status not in {"queued", "running"}:
                return False
            rec.cancel = True
            rec.status = "failed"
            rec.stage = "failed"
            rec.error = "TimeoutError"
            return True

    def set_terminal(
        self,
        job_id: str,
        status: JobStatus,
        *,
        stage: str = "done",
        error: str | None = None,
        generation: int | None = None,
    ) -> None:
        with self._lock:
            rec = self._live.get(job_id)
            if rec is None:
                rec = JobRecord(job_id=job_id, generation=generation or 0)
                self._live[job_id] = rec
            if generation is not None and rec.generation != generation:
                return
            if rec.status not in {"queued", "running"}:
                return
            rec.status = status
            rec.stage = stage
            rec.error = error


class JobProgress:
    """Per-job progress handle. Pipeline calls ``progress`` and ``cancelled``."""

    def __init__(self, bus: MemoryJobBus, job_id: str, generation: int) -> None:
        self._bus = bus
        self._job_id = job_id
        self._generation = generation

    def progress(self, stage: str) -> None:
        self._bus.set_progress(self._job_id, stage, generation=self._generation)

    def cancelled(self) -> bool:
        return self._bus.is_cancelled(self._job_id, self._generation)
