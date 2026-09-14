from __future__ import annotations

from finance_context.ports.protocols import BudgetKind, SlotKind


class AlwaysGrant:
    def acquire(self, kind: SlotKind, timeout_sec: float = 0) -> bool:
        return True

    def release(self, kind: SlotKind) -> None:
        return None

    def charge(self, kind: BudgetKind) -> bool:
        return True
