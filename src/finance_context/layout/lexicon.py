"""RU/EN words the layout reads: param headers, phases, period roles and units.

The lists are data in ``lexicon.json`` next to this module, so a new spelling is a
data edit. The publisher hashes that file into the layout stage.

A word matches whole. A trailing ``*`` keeps any ending (``факт*`` is ``фактический``,
``строител*`` is ``строительная``); without it ``кв`` is not ``кВт`` and ``г`` is not
``график``. A word with a space is a phrase (``end of``).
"""

from __future__ import annotations

import json
import re
from functools import cache
from importlib import resources
from typing import Any


@cache
def _data() -> dict[str, Any]:
    text = resources.files(__package__).joinpath("lexicon.json").read_text(encoding="utf-8")
    return json.loads(text)


def words(*path: str) -> tuple[str, ...]:
    node: Any = _data()
    for key in path:
        node = node[key]
    return tuple(node)


@cache
def pattern(*path: str) -> str:
    """Regex for the words at `path`, longest first, not followed by another letter."""
    items = sorted(words(*path), key=len, reverse=True)
    alts = [re.escape(w[:-1]) + r"\w*" if w.endswith("*") else re.escape(w) for w in items]
    return rf"(?:{'|'.join(alts)})(?![^\W\d_])"


@cache
def searcher(*path: str) -> re.Pattern[str]:
    """Compiled scan of one normalized label. Built once per word list."""
    return re.compile(rf"(?<!\w){pattern(*path)}")
