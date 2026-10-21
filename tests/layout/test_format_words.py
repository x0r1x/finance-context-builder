"""Closed format-word tables. A word matches whole, or with one listed ending."""

from finance_context.layout.format_words import (
    HALF_WORDS,
    QUARTER_LABEL_WORDS,
    QUARTER_WORDS,
    TOTAL_WORDS,
    YEAR_WORDS,
    accepts,
    blocked_surface,
    header_role_map,
    is_total_word,
    month_numbers,
)


def test_quarter_inflection_is_the_listed_ending() -> None:
    assert accepts(QUARTER_WORDS, "кварталу")
    assert accepts(QUARTER_WORDS, "кв")
    assert not accepts(QUARTER_WORDS, "квота")
    assert not accepts(YEAR_WORDS, "график")
    assert accepts(HALF_WORDS, "полугодия")
    assert accepts(QUARTER_LABEL_WORDS, "квартального")


def test_month_cases_map_to_the_month_number() -> None:
    months = month_numbers()
    assert months["января"] == 1
    assert months["январю"] == 1
    assert months["мае"] == 5
    assert months["августом"] == 8
    assert months["дек"] == 12
    assert "маяк" not in months


def test_total_words_are_a_hint_list() -> None:
    assert is_total_word("Итого")
    assert is_total_word("Total")
    assert not is_total_word("Выручка")
    assert "итого" in TOTAL_WORDS


def test_false_friends_are_not_period_surfaces() -> None:
    assert blocked_surface("график")
    assert blocked_surface("кВт")
    assert not blocked_surface("год")
    assert accepts(QUARTER_LABEL_WORDS, "quarterly")
    assert not accepts(QUARTER_WORDS, "quarterly")


def test_unit_header_words_stay_in_the_role_map() -> None:
    roles = header_role_map()
    assert roles["ед изм"] == "unit"
    assert roles["unit of measure"] == "unit"
    assert roles["значение"] == "value"
