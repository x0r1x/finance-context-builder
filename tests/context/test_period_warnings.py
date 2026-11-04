"""File-order period warnings, without a dict per cell, match the shape check."""

from __future__ import annotations

from pathlib import Path

from finance_context.context.build import _period_warnings
from finance_context.context.timeline import _period_warning_text, _period_warning_value
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
        shown = _period_warning_value(text, None, False) if text else None
        reached = bool(shown) and unparsed_period_shape(shown)
        assert reached == (bool(text) and unparsed_period_shape(text)), text


def _fullwidth_digits(text: str) -> str:
    return text.translate(str.maketrans("0123456789", "０１２３４５６７８９"))


def test_unicode_digit_headers_match_the_full_shape_check(tmp_path: Path) -> None:
    """Fullwidth digits fold in ``normalize_header``. The amount gate must not drop them."""
    rows = [
        ("S", "A1", _fullwidth_digits("1.2024"), None),
        ("S", "A2", _fullwidth_digits("13.2025"), None),
        ("S", "A3", _fullwidth_digits("12.2025"), None),
        ("S", "A4", _fullwidth_digits("20202020"), None),
        ("S", "A5", _fullwidth_digits("2024.2025"), None),
        ("S", "A6", "１．２０２４", None),
        ("S", "A7", "١٢.٢٠٢٥", None),
        ("S", "A8", "٢٠٢٠٢٠٢٠", None),
    ]
    for _sheet, _addr, text, _fmt in rows:
        shown = _period_warning_value(text, None, False)
        assert (bool(shown) and unparsed_period_shape(shown)) == unparsed_period_shape(text), text
    path = tmp_path / "cells.parquet"
    write_parquet(path, _COLUMNS, rows, row_group_rows=0)
    assert _period_warnings(path, False) == [
        "Unparsed period header S!A1: «１.２０２４»",
        "Unparsed period header S!A2: «１３.２０２５»",
        "Unparsed period header S!A4: «２０２０２０２０»",
        "Unparsed period header S!A5: «２０２４.２０２５»",
        "Unparsed period header S!A6: «１．２０２４»",
    ]


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
