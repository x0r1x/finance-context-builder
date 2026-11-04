from __future__ import annotations

import pytest

from finance_context.context.measure import currency_code_from_text, parse_measure
from finance_context.layout.params import unit_kind_from_text


def test_label_k_pound_is_money_gbp_thousands() -> None:
    measure = parse_measure(label="Revenue k£")
    assert measure.unit == "money"
    assert measure.currency == "GBP"
    assert measure.scale == "k"
    assert measure.display() == "k£"


def test_unit_cell_k_pound() -> None:
    measure = parse_measure(unit_text="k£", label="Maintenance")
    assert measure.unit == "money"
    assert measure.currency == "GBP"
    assert measure.scale == "k"


def test_dollar_format_is_money_usd_with_unknown_scale() -> None:
    measure = parse_measure(number_formats=['"$"#,##0'])
    assert measure.unit == "money"
    assert measure.currency == "USD"
    # A number format names the currency, not the multiplier of the stored amount.
    assert measure.scale is None


def test_pound_format_is_gbp() -> None:
    measure = parse_measure(number_formats=["[$£-809]#,##0"])
    assert measure.unit == "money"
    assert measure.currency == "GBP"


def test_percent_is_rate_not_years() -> None:
    measure = parse_measure(unit_text="%", label="Inflation per year")
    assert measure.unit == "rate"
    assert measure.display() == "%"


def test_years_duration() -> None:
    measure = parse_measure(unit_text="years", label="Concession Duration")
    assert measure.unit == "years"


def test_per_year_without_percent_is_rate() -> None:
    measure = parse_measure(label="Inflation per year")
    assert measure.unit == "rate"


def test_cyrillic_rub_aliases_are_rub() -> None:
    for text in ("руб", "РУБ", "руб.", "тыс. руб", "₽"):
        measure = parse_measure(unit_text=text)
        assert measure.unit == "money", text
        assert measure.currency == "RUB", text


def test_gbp_aliases() -> None:
    for text in ("£", "gbp", "GBP", "pound", "фунт", "£/year"):
        measure = parse_measure(unit_text=text)
        assert measure.unit == "money", text
        assert measure.currency == "GBP", text


def test_eur_aliases() -> None:
    for text in ("€", "eur", "EUR", "euro", "евро", "€/year"):
        measure = parse_measure(unit_text=text)
        assert measure.unit == "money", text
        assert measure.currency == "EUR", text


def test_usd_aliases() -> None:
    for text in ("$", "usd", "USD", "dollar", "долл", "доллар", "$/year"):
        measure = parse_measure(unit_text=text)
        assert measure.unit == "money", text
        assert measure.currency == "USD", text


def test_pound_per_year_is_money() -> None:
    measure = parse_measure(unit_text="£/year")
    assert measure.unit == "money"
    assert measure.currency == "GBP"


def test_currency_with_thousands_suffix_is_scaled_money() -> None:
    measure = parse_measure("EUR'000")
    assert (measure.unit, measure.currency, measure.scale) == ("money", "EUR", "k")


def test_scale_token_label_is_not_a_scaled_amount() -> None:
    measure = parse_measure(label="Thousand")
    assert measure.scale is None
    assert parse_measure(label="Million").scale is None
    assert parse_measure(label="Maintenance k£").scale == "k"


def test_currency_per_energy_is_a_price() -> None:
    measure = parse_measure("EUR/MWh")
    assert (measure.unit, measure.currency, measure.per) == ("price", "EUR", "MWh")
    assert measure.display() == "€/MWh"


def test_multiple_and_date_units() -> None:
    assert parse_measure("x").unit == "ratio"
    assert parse_measure("Date").unit == "date"
    assert parse_measure("#").unit == "count"
    assert parse_measure("№").unit == "count"
    assert unit_kind_from_text("#") == "count"
    assert unit_kind_from_text("№") == "count"


def test_months_per_year_is_a_count_not_a_rate() -> None:
    assert parse_measure(None, "Months per year").unit == "count"


def test_index_and_per_annum_captions_are_cell_units() -> None:
    from finance_context.layout.params import is_unit_text

    assert is_unit_text("MWh p.a.")
    assert is_unit_text("MWh pa")
    assert is_unit_text("MWh per annum")
    assert is_unit_text("%p.a.")
    assert is_unit_text("Index")
    assert is_unit_text("INDEX")
    assert not is_unit_text("Base Index")
    assert not is_unit_text("p.a.")
    assert parse_measure("MWh p.a.").unit == "count"
    assert parse_measure("EUR p.a.").unit == "money"
    assert parse_measure("% p.a.").unit == "rate"
    assert parse_measure("Index").unit == "ratio"
    assert parse_measure("Base Index").unit is None
    assert parse_measure("MWh p.a.", number_formats=["0.00%"]).unit == "count"
    assert unit_kind_from_text("MWh p.a.") == "count"
    assert unit_kind_from_text("Index") == "ratio"
    assert unit_kind_from_text("years") == "count"
    assert parse_measure("EUR/MWh").unit == "price"
    assert parse_measure("#").unit == "count"


@pytest.mark.parametrize(
    ("unit_text", "scale"),
    [
        ("тыс. руб.", "k"),
        ("тыс.руб.", "k"),
        ("млн руб.", "m"),
        ("млн. руб.", "m"),
        ("млрд руб.", "bn"),
        ("руб.", "unit"),
        ("₽", "unit"),
        ("тыс. ₽", "k"),
        ("млн ₽", "m"),
        ("тыс.р.", "k"),
        ("млн.р.", "m"),
        ("т. руб.", "k"),
        ("т.р.", "k"),
        ("тысячи руб.", "k"),
        ("тысяч рублей", "k"),
        ("р.", "unit"),
    ],
)
def test_rub_unit_cell_is_money_with_its_scale(unit_text: str, scale: str) -> None:
    measure = parse_measure(unit_text=unit_text, label="Выручка")
    assert (measure.unit, measure.currency, measure.scale) == ("money", "RUB", scale)


@pytest.mark.parametrize(
    ("label", "scale"),
    [
        ("Выручка, тыс. руб.", "k"),
        ("Выручка (млн руб.)", "m"),
        ("Выручка, млрд ₽", "bn"),
        ("Выручка, руб.", "unit"),
    ],
)
def test_rub_label_is_money_with_its_scale(label: str, scale: str) -> None:
    measure = parse_measure(label=label)
    assert (measure.unit, measure.currency, measure.scale) == ("money", "RUB", scale)


def test_rub_unit_cell_scale_beats_a_plain_label() -> None:
    measure = parse_measure(unit_text="тыс. руб.", label="Выручка, руб.")
    assert measure.scale == "k"


@pytest.mark.parametrize(
    ("fmt", "code"),
    [
        ("[$₽-419]# ##0", "RUB"),
        ("# ##0,00 [$руб.-419]", "RUB"),
        ("[$RUB] #,##0", "RUB"),
        ("[$USD] #,##0", "USD"),
        ("[$$-409]#,##0", "USD"),
        ("[$£-809]#,##0", "GBP"),
        ("[$€-x-euro2] #,##0", "EUR"),
        ('"$"#,##0', "USD"),
        ('#,##0.00"р."', "RUB"),
        ("[$-419]# ##0", None),
        ("[$-F800]dddd, mmmm dd, yyyy", None),
    ],
)
def test_currency_code_reads_the_symbol_of_a_format_tag(fmt: str, code: str | None) -> None:
    assert currency_code_from_text(fmt) == code


def test_locale_only_format_tag_is_not_money() -> None:
    measure = parse_measure(number_formats=["[$-419]# ##0"])
    assert (measure.unit, measure.currency) == (None, None)


@pytest.mark.parametrize("label", ["Тысяча", "Миллион", "Миллиард"])
def test_russian_multiplier_label_is_not_a_scaled_amount(label: str) -> None:
    assert parse_measure(label=label).scale is None


@pytest.mark.parametrize("label", ["тысячная доля", "миллиардный", "миллионер", "Стоимость труб"])
def test_word_built_on_a_multiplier_is_not_a_scale(label: str) -> None:
    assert parse_measure(label=label).scale is None


def test_rub_label_in_thousands_phrase_is_scaled() -> None:
    measure = parse_measure(label="Выручка, в тысячах рублей")
    assert (measure.unit, measure.currency, measure.scale) == ("money", "RUB", "k")


@pytest.mark.parametrize(
    ("text", "currency", "scale"),
    [
        ("RURm", "RUB", "m"),
        ("rurm", "RUB", "m"),
        ("RUR", "RUB", "unit"),
        ("RUBm", "RUB", "m"),
        ("mRUB", "RUB", "m"),
        ("RUB m", "RUB", "m"),
        ("RUB mn", "RUB", "m"),
        ("RUB'000", "RUB", "k"),
        ("USDm", "USD", "m"),
        ("USD bn", "USD", "bn"),
        ("mUSD", "USD", "m"),
        ("GBPm", "GBP", "m"),
        ("EURk", "EUR", "k"),
        ("EUR k", "EUR", "k"),
        ("CHF", "CHF", "unit"),
        ("TRYm", "TRY", "m"),
        ("mTRY", "TRY", "m"),
    ],
)
def test_iso_currency_with_scale_suffix_or_prefix(text: str, currency: str, scale: str) -> None:
    measure = parse_measure(unit_text=text)
    assert (measure.unit, measure.currency, measure.scale) == ("money", currency, scale)


@pytest.mark.parametrize(
    "text",
    [
        "from",
        "summary",
        "margin",
        "% Margin",
        "try again",
        "CAPEX",
        "Value upside",
        "all",
        "pen",
        "cup",
        "top",
        "mad",
        "gel",
        "sos",
        "cop",
        "Cash Flow Summary",
        "CF from Operations",
        "CF from Investing Activities",
    ],
)
def test_ordinary_words_are_not_iso_currencies(text: str) -> None:
    measure = parse_measure(label=text)
    assert measure.currency is None
    assert measure.scale is None


def test_margin_after_a_currency_code_is_not_a_millions_suffix() -> None:
    measure = parse_measure(label="RUB margin")
    assert (measure.currency, measure.scale) == ("RUB", "unit")


def test_a_number_glued_to_a_currency_code_is_not_a_thousands_scale() -> None:
    measure = parse_measure(unit_text="1000 USD")
    assert (measure.currency, measure.scale) == ("USD", "unit")


def test_currency_is_read_once_for_a_label(monkeypatch: pytest.MonkeyPatch) -> None:
    import finance_context.context.measure as measure

    calls: list[str | None] = []
    original = measure.currency_code_from_text

    def spy(text: str | None) -> str | None:
        calls.append(text)
        return original(text)

    monkeypatch.setattr(measure, "currency_code_from_text", spy)
    plain = parse_measure(label="Maintenance cost line")
    assert plain.unit is None
    assert plain.currency is None
    scaled = parse_measure(label="Total EUR mn")
    assert (scaled.unit, scaled.currency, scaled.scale) == ("money", "EUR", "m")
    assert calls == ["Maintenance cost line", "Total EUR mn"]


def test_iso_unit_caption_is_unit_text() -> None:
    from finance_context.layout.units import is_unit_text, unit_kind_from_text

    assert is_unit_text("RURm")
    assert is_unit_text("RUR")
    assert is_unit_text("USD bn")
    assert is_unit_text("mRUB")
    assert unit_kind_from_text("RURm") == "money"
    assert not is_unit_text("Cash Flow Summary")
    assert not is_unit_text("% Margin")
    assert not is_unit_text("try again")
