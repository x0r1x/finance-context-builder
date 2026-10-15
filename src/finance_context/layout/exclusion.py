from __future__ import annotations

from typing import Protocol

from finance_context.layout.models import Layout
from finance_context.layout.normalize import normalize_label
from finance_context.layout.resolve import period_headers
from finance_context.layout.roles import article_role

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


class _ExclusionView:
    def __init__(self, kind: str, article_role: str, label: str) -> None:
        self.kind = kind
        self.article_role = article_role
        self.label = label


def row_exclusion(layout: Layout, cells: list[dict]) -> dict[tuple[str, int], str]:
    """Structural exclusion by sheet and row. Absent means the row stays in the document."""
    by_addr = {(str(cell["sheet"]), int(cell["row"]), int(cell["col"])): cell for cell in cells}
    excluded: dict[tuple[str, int], str] = {}
    for sheet in layout.sheets:
        for block in sheet.blocks:
            headers = period_headers(sheet, block)
            if getattr(block, "kind", "timeline") == "params":
                headers = [header for header in headers if header.role in {"value", "scenario"}]
            block_kind = getattr(block, "kind", "timeline")
            for layout_row in block.rows:
                templates: list[str | None] = []
                for header in headers:
                    cell = by_addr.get((sheet.name, layout_row.row, header.col))
                    template = None if cell is None else cell.get("formula_template")
                    templates.append(str(template) if template else None)
                role = article_role(layout_row, templates, block_kind=block_kind)
                reason = exclusion_reason(_ExclusionView(layout_row.kind, role, layout_row.label))
                if reason:
                    excluded[(sheet.name, layout_row.row)] = reason
    return excluded


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
