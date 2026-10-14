from __future__ import annotations

from typing import Protocol

from finance_context.layout.normalize import normalize_label

_NOISE_EXACT = {
    "helper",
    "helpers",
    "trend charts",
    "liquidity bridge",
    "cover",
    "dashboard",
    "assumptions",
    "notes",
    "chart",
    "charts",
    "bridge",
    "spare",
    "none",
}
_NOISE_TAILS = (" charts", " chart", " bridge")
_TECHNICAL = (
    "from mf",
    "circular",
    "helper",
)


class _Excludable(Protocol):
    kind: str
    article_role: str
    label: str


def is_noise_label(label: str | None) -> bool:
    n = normalize_label(label)
    if not n:
        return True
    if n in _NOISE_EXACT:
        return True
    return any(n.endswith(tail) for tail in _NOISE_TAILS)


def exclusion_reason(ctx: _Excludable) -> str | None:
    if ctx.kind == "flag":
        return "flag"
    if ctx.article_role == "check" or ctx.kind == "helper":
        return "check" if ctx.article_role == "check" else "helper"
    if is_noise_label(ctx.label):
        return "noise"
    blob = (ctx.label or "").casefold()
    if any(token in blob for token in _TECHNICAL) and "pre-revolver" not in blob:
        return "technical_bridge"
    return None
