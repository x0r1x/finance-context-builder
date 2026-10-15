from __future__ import annotations

import re
from typing import Literal

from finance_context.layout.models import LayoutRow
from finance_context.layout.normalize import normalize_label

ArticleRole = Literal[
    "assumption",
    "calculation",
    "database_like",
    "check",
    "output",
    "actual_adjustment",
]

_OUTPUT = re.compile(r"итого|total|всего")
_ADJUST = re.compile(r"корректир|adjustment|plug")
_ASSUME = re.compile(r"ставк|assumption|допущен|\brate\b")


def article_role(
    row: LayoutRow,
    cell_templates: list[str | None],
    *,
    block_kind: str = "timeline",
) -> ArticleRole:
    if row.check_row:
        return "check"
    if block_kind == "params" and row.kind == "fact":
        return "assumption"
    label = normalize_label(row.label)
    if _OUTPUT.search(label):
        return "output"
    if _ADJUST.search(label):
        return "actual_adjustment"
    if _ASSUME.search(label):
        return "assumption"
    if not any(cell_templates):
        return "database_like"
    return "calculation"
