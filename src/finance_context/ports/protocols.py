from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel

SlotKind = Literal["llm", "embed", "run"]
BudgetKind = Literal["llm", "embed"]


class EmbedPort(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class ChatPort(Protocol):
    def complete_json(self, schema: type[BaseModel], messages: list) -> BaseModel: ...


class SlotGate(Protocol):
    def acquire(self, kind: SlotKind, timeout_sec: float = 0) -> bool: ...

    def release(self, kind: SlotKind) -> None: ...

    def charge(self, kind: BudgetKind) -> bool: ...


class ArtifactStore(Protocol):
    def dest_dir(self, job_id: str) -> Path: ...
