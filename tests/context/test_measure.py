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


def test_sign_stock_for_opening_balance() -> None:
    measure = parse_measure(
        label="Opening cash",
        nature="balance",
        time_semantics="bop",
    )
    assert measure.sign == "stock"


def test_sign_inflow_from_revenue_label() -> None:
    measure = parse_measure(label="Gross revenue")
    assert measure.sign == "inflow"


def test_sign_outflow_from_cost_label() -> None:
    measure = parse_measure(label="Operating costs")
    assert measure.sign == "outflow"


def test_sign_outflow_for_principal_repayment() -> None:
    measure = parse_measure(label="Principal Repayment")
    assert measure.sign == "outflow"


def test_sign_pre_tax_income_is_not_outflow() -> None:
    measure = parse_measure(label="EBT (Taxable Profit)")
    assert measure.sign != "outflow"


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
        ("[$-419]# ##0", None),
        ("[$-F800]dddd, mmmm dd, yyyy", None),
    ],
)
def test_currency_code_reads_the_symbol_of_a_format_tag(fmt: str, code: str | None) -> None:
    assert currency_code_from_text(fmt) == code


def test_locale_only_format_tag_is_not_money() -> None:
    measure = parse_measure(number_formats=["[$-419]# ##0"])
    assert (measure.unit, measure.currency) == (None, None)
