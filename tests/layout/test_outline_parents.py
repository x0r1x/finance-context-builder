"""Outline summaries stay the same row, and finding them stays linear."""

from __future__ import annotations

import random
from copy import deepcopy

from finance_context.layout import rows as row_mod
from finance_context.layout.models import LayoutRow
from finance_context.layout.rows import _apply_outline_parents
from finance_context.layout.units import is_unit_text

_UNIT_LABELS = ("k£", "RURm", "EUR'000")


def _slow_apply(
    rows: list[LayoutRow], levels: list[int], summary_below: bool
) -> None:
    """The previous per-row scan, including ``list.index``."""
    for index, (row, level) in enumerate(zip(rows, levels, strict=True)):
        if level <= 1 or is_unit_text(row.label):
            continue
        span = range(index + 1, len(rows)) if summary_below else range(index - 1, -1, -1)
        parent = None
        for other in span:
            if is_unit_text(rows[other].label):
                continue
            if levels[other] < level:
                parent = rows[other]
                break
        if parent is None:
            continue
        parent_level = levels[rows.index(parent)]
        if parent_level <= 0:
            continue
        if row.parent_row is None:
            row.parent_row = parent.row
            continue
        closer_above = not summary_below and parent.row > row.parent_row
        closer_below = summary_below and parent.row < row.parent_row
        if closer_above or closer_below:
            row.parent_row = parent.row


def _sample(rng: random.Random, n: int) -> tuple[list[LayoutRow], list[int]]:
    rows: list[LayoutRow] = []
    levels: list[int] = []
    for index in range(n):
        if rng.random() < 0.15:
            label = rng.choice(_UNIT_LABELS)
        else:
            label = f"Line {index}"
        parent = None if index == 0 or rng.random() < 0.5 else rows[rng.randrange(index)].row
        rows.append(
            LayoutRow(row=index + 2, label=label, indent=rng.randint(0, 3), parent_row=parent)
        )
        levels.append(rng.randint(0, 4))
    return rows, levels


def test_outline_parents_match_the_row_scan() -> None:
    rng = random.Random(20)
    sizes = (1, 2, 5, 17, 64)
    for n in sizes:
        for summary_below in (True, False):
            for _ in range(8):
                rows, levels = _sample(rng, n)
                expected = deepcopy(rows)
                actual = deepcopy(rows)
                _slow_apply(expected, levels, summary_below)
                _apply_outline_parents(actual, levels, summary_below)
                assert [row.parent_row for row in actual] == [row.parent_row for row in expected]


def test_summary_below_a_flat_group_hangs_off_the_total() -> None:
    rows = [LayoutRow(row=index, label=f"Line {index}", indent=0) for index in range(1, 6)]
    rows[2] = LayoutRow(row=3, label="k£", indent=0)
    rows.append(LayoutRow(row=6, label="Total", indent=0))
    levels = [2, 2, 2, 2, 2, 1]
    _apply_outline_parents(rows, levels, True)
    assert [row.parent_row for row in rows] == [6, 6, None, 6, 6, None]


def test_outline_parent_lookup_is_linear(monkeypatch) -> None:
    """A group whose summary is the last row used to scan the tail each time."""
    calls = 0
    real = row_mod.is_unit_text

    def wrapped(text: str | None) -> bool:
        nonlocal calls
        calls += 1
        return real(text)

    monkeypatch.setattr(row_mod, "is_unit_text", wrapped)

    def once(n: int) -> int:
        nonlocal calls
        calls = 0
        rows = [LayoutRow(row=index, label=f"Line {index}", indent=0) for index in range(n)]
        rows.append(LayoutRow(row=n, label="Total", indent=0))
        _apply_outline_parents(rows, [2] * n + [1], True)
        return calls

    small = once(200)
    large = once(400)
    assert large <= small * 2.6
    assert large <= 4 * 401
