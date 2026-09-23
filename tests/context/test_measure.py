from __future__ import annotations

from finance_context.context.measure import parse_measure


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


def test_dollar_format_is_money_usd() -> None:
    measure = parse_measure(number_formats=['"$"#,##0'])
    assert measure.unit == "money"
    assert measure.currency == "USD"
    assert measure.scale == "unit"


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


def test_sign_stock_for_balance_sheet() -> None:
    measure = parse_measure(
        label="Opening cash",
        concept_id="bs.cash",
        statement="bs",
        nature="balance",
        time_semantics="bop",
    )
    assert measure.sign == "stock"


def test_sign_inflow_from_revenue() -> None:
    measure = parse_measure(label="Gross revenues", concept_id="pnl.revenue", statement="pnl")
    assert measure.sign == "inflow"


def test_sign_outflow_from_opex() -> None:
    measure = parse_measure(label="Operating costs", concept_id="pnl.opex", statement="pnl")
    assert measure.sign == "outflow"


def test_sign_outflow_for_principal_repayment() -> None:
    measure = parse_measure(
        label="Principal Repayment",
        concept_id="cf.repayment",
        statement="cf",
    )
    assert measure.sign == "outflow"
    directed = parse_measure(
        label="Principal Repayment",
        concept_id="cf.repayment",
        statement="cf",
        direction="outflow",
    )
    assert directed.sign == "outflow"


def test_sign_pre_tax_income_is_not_outflow() -> None:
    measure = parse_measure(
        label="EBT (Taxable Profit)",
        concept_id="pnl.pre_tax_income",
        statement="pnl",
    )
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


def test_months_per_year_is_a_count_not_a_rate() -> None:
    assert parse_measure(None, "Months per year").unit == "count"
