from __future__ import annotations

from finance_context.ports.protocols import BudgetKind, SlotGate, SlotKind


def acquire_slot(slots: SlotGate | None, kind: SlotKind, timeout_sec: float) -> bool:
    if slots is None:
        return True
    return slots.acquire(kind, timeout_sec)


def release_slot(slots: SlotGate | None, kind: SlotKind) -> None:
    if slots is None:
        return
    slots.release(kind)


def charge_slot(slots: SlotGate | None, kind: BudgetKind) -> bool:
    if slots is None:
        return True
    charge = getattr(slots, "charge", None)
    if charge is None:
        return True
    return bool(charge(kind))
