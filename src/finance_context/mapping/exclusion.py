from __future__ import annotations

from finance_context.mapping.models import RowContext
from finance_context.mapping.rules import is_noise_label

_TECHNICAL = (
    "from mf",
    "circular",
    "helper",
)


def exclusion_reason(ctx: RowContext) -> str | None:
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
