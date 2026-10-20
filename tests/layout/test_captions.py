"""Real captions and the outcome the layout should give them."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finance_context.context.timeline import _phase_kind
from finance_context.layout.params import _header_role
from finance_context.layout.periods import classify_atom, classify_header, unparsed_period_shape

_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "captions.json").read_text(encoding="utf-8")
)["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[case["text"] for case in _CASES])
def test_caption_outcome(case: dict) -> None:
    text = case["text"]
    if "period" in case or "period_key" in case:
        hit = classify_header(text)
        if case.get("period", "present") is None and "period_key" not in case:
            assert hit is None
        else:
            assert hit is not None
            assert hit.period_key == case["period_key"]
            assert hit.kind == "period"
            assert hit.explicit_role is case["explicit_role"]
            assert bool(hit.variant) is case["explicit_role"]
    if "header_role" in case:
        assert _header_role(text) == case["header_role"]
    if "phase" in case:
        assert _phase_kind(text) == case["phase"]
    if case.get("quarter") is False:
        atom = classify_atom(text)
        assert atom is None or atom.quarter is None
    if "unparsed" in case:
        assert unparsed_period_shape(text) is case["unparsed"]
