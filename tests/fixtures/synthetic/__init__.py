"""Deterministic synthetic workbooks. See ``books`` for the shapes."""

from tests.fixtures.synthetic.books import (
    DENSE_COLUMNS,
    REL_PERIODS,
    SERIES_PERIODS,
    TEXT_STRLEN,
    dense,
    hops,
    hops_tl,
    outline,
    rel,
    series,
    sumif,
    text,
)
from tests.fixtures.synthetic.cache import GENERATOR_REV, cached_workbook

__all__ = [
    "DENSE_COLUMNS",
    "GENERATOR_REV",
    "REL_PERIODS",
    "SERIES_PERIODS",
    "TEXT_STRLEN",
    "cached_workbook",
    "dense",
    "hops",
    "hops_tl",
    "outline",
    "rel",
    "series",
    "sumif",
    "text",
]
