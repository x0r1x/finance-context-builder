from __future__ import annotations

import re

_PARENS = re.compile(r"\([^)]*\)|（[^）]*）")


def normalize_label(label: str | None) -> str:
    if not label:
        return ""
    text = _PARENS.sub("", str(label))
    return re.sub(r"\s+", " ", text).strip().casefold()
