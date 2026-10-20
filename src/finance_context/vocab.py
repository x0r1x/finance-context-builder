"""Leaf literals shared by the context model and series helpers."""

from __future__ import annotations

from typing import Literal

ValueStatus = Literal["cached", "empty", "zero_explicit", "not_applicable"]
FormulaClass = Literal[
    "same_period",
    "cross_period",
    "aggregation",
    "rollforward",
    "conditional",
    "hardcoded",
]
