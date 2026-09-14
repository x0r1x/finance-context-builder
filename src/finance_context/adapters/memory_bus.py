from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from finance_context.models.context import JobStatus


@dataclass
class JobRecord:
    job_id: str
    status: JobStatus = "queued"
    stage: str = "queued"
    error: str | None = None


@dataclass
class MemoryJobBus:
    """In-process queue. Replace with Redis for multi-replica deploys."""

    _queue: asyncio.Queue[str] = field(default_factory=asyncio.Queue)
    _live: dict[str, JobRecord] = field(default_factory=dict)

    async def enqueue(self, job_id: str) -> None:
        rec = self._live.get(job_id)
        if rec is None or rec.status not in {"queued", "running"}:
            self._live[job_id] = JobRecord(job_id=job_id)
            await self._queue.put(job_id)

    async def claim(self) -> str | None:
        job_id = await self._queue.get()
        rec = self._live.get(job_id)
        if rec is not None:
            rec.status = "running"
            rec.stage = "parse"
        return job_id

    def get(self, job_id: str) -> JobRecord | None:
        return self._live.get(job_id)

    def set_progress(self, job_id: str, stage: str) -> None:
        rec = self._live.setdefault(job_id, JobRecord(job_id=job_id, status="running"))
        rec.stage = stage
        rec.status = "running"

    def set_terminal(
        self,
        job_id: str,
        status: JobStatus,
        *,
        stage: str = "done",
        error: str | None = None,
    ) -> None:
        rec = self._live.setdefault(job_id, JobRecord(job_id=job_id))
        rec.status = status
        rec.stage = stage
        rec.error = error
