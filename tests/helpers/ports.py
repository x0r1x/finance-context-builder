from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from finance_context.ports.protocols import BudgetKind


class FakeEmbed:
    def __init__(self, table: dict[str, list[float]] | None = None) -> None:
        self.table = table or {}
        self.calls = 0
        self.texts: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        self.texts.append(list(texts))
        return [self._vec(text) for text in texts]

    def _vec(self, text: str) -> list[float]:
        key = text.casefold().strip()
        if key in self.table:
            return self.table[key]
        for label, vec in self.table.items():
            if label.casefold() == key:
                return vec
        return [0.0, 0.0, 0.0]


class FakeChat:
    def __init__(
        self,
        concept_id: str = "unknown",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.concept_id = concept_id
        self.payload = payload
        self.calls = 0
        self.messages_seen: list[list[dict[str, Any]]] = []

    def complete_json(self, schema: type[BaseModel], messages: list) -> BaseModel:
        self.calls += 1
        self.messages_seen.append(list(messages))
        if self.payload is not None:
            return schema.model_validate(self.payload)
        fields = schema.model_fields
        if "concept_id" in fields:
            return schema.model_validate({"concept_id": self.concept_id})
        return schema.model_validate({})


class GrantSlots:
    def acquire(self, kind: Literal["llm", "embed", "run"], timeout_sec: float = 0) -> bool:
        return True

    def release(self, kind: Literal["llm", "embed", "run"]) -> None:
        return None

    def charge(self, kind: BudgetKind) -> bool:
        return True


class DenySlots:
    def acquire(self, kind: Literal["llm", "embed", "run"], timeout_sec: float = 0) -> bool:
        return False

    def release(self, kind: Literal["llm", "embed", "run"]) -> None:
        return None

    def charge(self, kind: BudgetKind) -> bool:
        return True


class CapBudget(GrantSlots):
    def __init__(self, remaining: dict[str, int] | None = None) -> None:
        self.remaining = remaining or {}
        self.charges: list[str] = []

    def charge(self, kind: BudgetKind) -> bool:
        self.charges.append(kind)
        left = self.remaining.get(kind, 0)
        if left <= 0:
            return False
        self.remaining[kind] = left - 1
        return True
