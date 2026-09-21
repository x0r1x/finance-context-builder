from __future__ import annotations

from finance_context.layout.params import parse_display_unit, unit_kind_from_text


def test_parse_display_unit_money_scale_and_flow() -> None:
    pound = parse_display_unit("£")
    assert pound is not None
    assert pound.raw == "£"
    assert pound.dimension == "money"
    assert pound.currency == "GBP"
    assert pound.scale == 1
    assert pound.per is None

    kilo = parse_display_unit("k£")
    assert kilo is not None
    assert kilo.dimension == "money"
    assert kilo.currency == "GBP"
    assert kilo.scale == 1000
    assert kilo.per is None

    flow = parse_display_unit("£/year")
    assert flow is not None
    assert flow.dimension == "money"
    assert flow.currency == "GBP"
    assert flow.scale == 1
    assert flow.per == "year"


def test_parse_display_unit_count_rate_duration() -> None:
    traffic = parse_display_unit("veh/year")
    assert traffic is not None
    assert traffic.dimension == "count"
    assert traffic.currency is None
    assert traffic.scale is None
    assert traffic.per == "year"

    rate = parse_display_unit("%")
    assert rate is not None
    assert rate.dimension == "rate"
    assert rate.per is None

    per_year = parse_display_unit("per year")
    assert per_year is not None
    assert per_year.dimension == "rate"
    assert per_year.per == "year"

    duration = parse_display_unit("years")
    assert duration is not None
    assert duration.dimension == "count"
    assert duration.per is None
    assert unit_kind_from_text("years") == "count"


def test_parse_display_unit_empty() -> None:
    assert parse_display_unit(None) is None
    assert parse_display_unit("  ") is None
