from __future__ import annotations

from datetime import date

from finance_context.excel.dates import is_date_format, serial_to_date


def test_builtin_date_formats_detected() -> None:
    assert is_date_format("mm-dd-yy")
    assert is_date_format("d-mmm-yy")
    assert is_date_format("dd.mm.yyyy")
    assert is_date_format("mmm-yy")
    assert not is_date_format("General")
    assert not is_date_format("0.00")
    assert not is_date_format("@")
    assert not is_date_format("h:mm")
    assert not is_date_format("0%")


def test_serial_1900_system() -> None:
    assert serial_to_date(1) == date(1900, 1, 1)
    assert serial_to_date(59) == date(1900, 2, 28)
    assert serial_to_date(60) is None
    assert serial_to_date(61) == date(1900, 3, 1)
    assert serial_to_date(44743) == date(2022, 7, 1)


def test_serial_1904_system() -> None:
    assert serial_to_date(0, date1904=True) == date(1904, 1, 1)
    delta = (date(2022, 7, 1) - date(1904, 1, 1)).days
    assert serial_to_date(delta, date1904=True) == date(2022, 7, 1)


def test_fractional_serial_uses_the_day() -> None:
    assert serial_to_date(44743.75) == date(2022, 7, 1)
