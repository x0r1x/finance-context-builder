"""File-order period warnings, without a dict per cell, match the shape check."""

from __future__ import annotations

from pathlib import Path

from finance_context.context.build import _period_warnings
from finance_context.context.timeline import _period_warning_text, _unparsed_period_warning
from finance_context.layout.periods import unparsed_period_shape
from finance_context.store.fs import write_parquet

_COLUMNS = (
    ("sheet", "VARCHAR"),
    ("addr", "VARCHAR"),
    ("cached_value", "VARCHAR"),
    ("number_format", "VARCHAR"),
)


def test_plain_numbers_match_the_full_shape_check() -> None:
    samples = [str(n) for n in range(5000)]
    samples += [f"{n}.5" for n in range(2000)]
    samples += [f"{n}.{year}" for n in range(20) for year in (1899, 1999, 2020, 2024)]
    samples += [
        "20202020",
        "2020.0",
        "2020.00",
        "13.2025",
        "12.2025",
        "1.2025",
        "-1.2025",
        "1.2",
        "-5",
        "Q5 2025",
        "2020 2021",
        "9М 2025",
        "FY2025/26",
        "",
    ]
    for text in samples:
        assert _unparsed_period_warning(text) == (
            bool(text) and unparsed_period_shape(text)
        ), text


def test_period_warnings_follow_file_order(tmp_path: Path) -> None:
    rows = [
        ("S", "A1", "Q5 2025", None),
        ("S", "A2", "Q5 2025", None),
        ("T", "A1", "Q5 2025", None),
        ("S", "A3", "100", None),
        ("S", "A4", "1.2025", None),
        ("S", "A5", "1.2072", "0.0"),
        ("S", "A6", None, None),
        ("S", "A7", "20202020", None),
        ("S", "A8", "13.2025", None),
        ("S", "A9", "12.2025", None),
        ("S", "B1", "9М 2025", None),
        ("S", "B2", "   ", None),
        ("S", "B3", "44927", "DD.MM.YYYY"),
    ]
    path = tmp_path / "cells.parquet"
    write_parquet(path, _COLUMNS, rows, row_group_rows=0)
    assert _period_warnings(path, False) == [
        "Unparsed period header S!A1: «Q5 2025»",
        "Unparsed period header T!A1: «Q5 2025»",
        "Unparsed period header S!A4: «1.2025»",
        "Unparsed period header S!A7: «20202020»",
        "Unparsed period header S!A8: «13.2025»",
    ]
    # The dict form of the same cells produces the same lines.
    expected: list[str] = []
    seen: set[tuple[str, str]] = set()
    for sheet, addr, value, fmt in rows:
        cell = {"sheet": sheet, "addr": addr, "cached_value": value, "number_format": fmt}
        text = _period_warning_text(cell, False)
        if not text or not unparsed_period_shape(text):
            continue
        key = (sheet, text)
        if key in seen:
            continue
        seen.add(key)
        expected.append(f"Unparsed period header {sheet}!{addr}: «{text}»")
    assert _period_warnings(path, False) == expected
