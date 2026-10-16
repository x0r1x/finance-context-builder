"""RU/EN words the layout reads: param headers, phases, period roles and units.

The lists are data in ``lexicon.json`` next to this module, so a new spelling is a
data edit. The publisher hashes that file into the layout stage.
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


def stems(*path: str) -> str:
    """Regex alternation of word stems, longest first: `квартал` wins over `кв`."""
    return "|".join(re.escape(item) for item in sorted(words(*path), key=len, reverse=True))
