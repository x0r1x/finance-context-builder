"""Leaf literals shared by the context model and series helpers."""

from __future__ import annotations

from typing import Literal

ValueStatus = Literal["cached", "empty", "zero_explicit", "not_applicable"]
PeriodPosition = Literal["beginning", "during_period", "end", "average", "instant"]
SeriesAggregation = Literal["sum", "average", "last", "first", "min", "max", "none"]
FormulaClass = Literal[
    "same_period",
    "cross_period",
    "aggregation",
    "rollforward",
    "conditional",
    "hardcoded",
]
