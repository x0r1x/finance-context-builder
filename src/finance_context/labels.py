"""Match a search needle against a row's own label."""

from __future__ import annotations


def own_label_contains(label: str, needle: str) -> bool:
    """True when the stripped needle is a casefold substring of the row label."""
    folded = needle.strip().casefold()
    if not folded:
        return False
    return folded in label.casefold()
