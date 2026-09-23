from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthBody(BaseModel):
    status: Literal["ok"]


class ReadyBody(BaseModel):
    status: Literal["ready"]
    llm: bool
    embeddings: bool
    queue: Literal["in_process"]


class ErrorBody(BaseModel):
    """Stable error code. `detail` is set when the code needs a short extra."""

    error: str
    detail: str | None = None


class JobBody(BaseModel):
    """Job status. Document URLs appear once the job is no longer queued or running."""

    job_id: str
    status: str | None = None
    stage: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    context_json_url: str | None = None
    context_md_url: str | None = None
    graph_json_url: str | None = None
    graph_md_url: str | None = None
