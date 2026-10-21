"""Closed format-word tables. Matching does not stem and does not read a file."""

from finance_context.layout.lexicon import (
    accepts,
    blocked_surface,
    header_role_map,
    is_total_word,
    month_numbers,
    words,
)


def test_quarter_inflection_is_the_listed_ending() -> None:
    assert accepts("period_words", "quarter", surface="кварталу")
    assert accepts("period_words", "quarter", surface="кв")
    assert not accepts("period_words", "quarter", surface="квота")
    assert not accepts("period_words", "year", surface="график")
    assert accepts("period_words", "half", surface="полугодия")
    assert accepts("structural", "quarter_label", surface="квартального")


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
    assert "итого" in words("structural", "total")


def test_false_friends_are_not_period_surfaces() -> None:
    assert blocked_surface("график")
    assert blocked_surface("кВт")
    assert not blocked_surface("год")
    assert accepts("structural", "quarter_label", surface="quarterly")
    assert not accepts("period_words", "quarter", surface="quarterly")


def test_unit_header_words_stay_in_the_role_map() -> None:
    roles = header_role_map()
    assert roles["ед изм"] == "unit"
    assert roles["unit of measure"] == "unit"
    assert roles["значение"] == "value"
