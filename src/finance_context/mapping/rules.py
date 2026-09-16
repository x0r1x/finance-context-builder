from __future__ import annotations

from finance_context.mapping.normalize import normalize_label

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
}
_NOISE_TAILS = (" charts", " chart", " bridge")


def is_noise_label(label: str | None) -> bool:
    n = normalize_label(label)
    if not n:
        return True
    if n in _NOISE_EXACT:
        return True
    return any(n.endswith(tail) for tail in _NOISE_TAILS)
