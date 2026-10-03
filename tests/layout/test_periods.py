from __future__ import annotations

import pytest

from finance_context.layout.periods import (
    apply_grain,
    classify_atom,
    classify_header,
    compose_period,
    infer_grain,
)


@pytest.mark.parametrize(
    ("text", "role", "period_key"),
    [
        ("2023", "historical", "2023"),
        ("2025E", "forecast", "2025"),
        ("янв.25", "historical", "2025-01"),
        ("2025-01", "historical", "2025-01"),
        ("01.07.2022", "historical", "2022-07-01"),
    ],
)
def test_header_carries_role_and_period_key(text: str, role: str, period_key: str) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert hit.role == role
    assert hit.period_key == period_key


def test_year_fact_plan_share_calendar_key() -> None:
    fact = classify_header("2024 факт")
    plan = classify_header("2024 план")
    assert fact is not None and plan is not None
    assert fact.period_key == plan.period_key == "2024"
    assert fact.role == "historical"
    assert plan.role == "forecast"


def test_month_fact_plan_share_calendar_key() -> None:
    fact = classify_header("янв.25 факт")
    plan = classify_header("янв.25 план")
    assert fact is not None and plan is not None
    assert fact.period_key == plan.period_key == "2025-01"
    assert fact.role == "historical"
    assert plan.role == "forecast"


@pytest.mark.parametrize(
    ("text", "period_key"),
    [
        ("1 кв. 2025", "2025Q1"),
        ("янв-25", "2025-01"),
        ("Jan-25", "2025-01"),
        ("2 023", "2023"),
        ("2022-07-01", "2022-07-01"),
    ],
)
def test_header_period_key(text: str, period_key: str) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert hit.period_key == period_key


def test_fact_plan_keywords() -> None:
    assert classify_header("факт").role == "historical"  # type: ignore[union-attr]
    assert classify_header("план").role == "forecast"  # type: ignore[union-attr]


def test_scenario_and_total_and_stub() -> None:
    assert classify_header("Upside").role == "scenario"  # type: ignore[union-attr]
    assert classify_header("Итого").role == "total"  # type: ignore[union-attr]
    assert classify_header("stub").role == "stub"  # type: ignore[union-attr]


def test_line_item_is_not_a_period() -> None:
    assert classify_header("Revenue") is None
    assert classify_header("Выручка") is None
    assert classify_header("GMV") is None


def test_december_maps_to_12() -> None:
    assert classify_header("дек.25").period_key == "2025-12"  # type: ignore[union-attr]
    assert classify_header("Dec-25").period_key == "2025-12"  # type: ignore[union-attr]


def test_quarter_token_without_year() -> None:
    atom = classify_atom("1 кв.")
    assert atom is not None
    assert atom.kind == "quarter_token"
    assert atom.quarter == 1
    assert classify_header("1 кв.") is None


def test_compose_year_and_quarter_token() -> None:
    year = classify_atom("2026")
    q = classify_atom("2 кв.")
    assert year is not None and q is not None
    hit = compose_period([year, q])
    assert hit is not None
    assert hit.period_key == "2026Q2"


def test_compose_uses_year_hint() -> None:
    q = classify_atom("3 кв.")
    assert q is not None
    hit = compose_period([q], year_hint="2025")
    assert hit is not None
    assert hit.period_key == "2025Q3"


def test_time_factor_is_noise() -> None:
    atom = classify_atom("0.25")
    assert atom is not None
    assert atom.kind == "noise"
    assert classify_header("0.25") is None


def test_quarter_cadence_from_dates() -> None:
    grain = infer_grain(["2022-07-01", "2022-10-01", "2023-01-01", "2023-04-01"])
    assert grain == "quarter"


def test_weekly_cadence_from_dates() -> None:
    grain = infer_grain(["2026-01-11", "2026-01-18", "2026-01-25", "2026-02-01"])
    assert grain == "week"


def test_apply_grain_week_keeps_day_keys() -> None:
    assert apply_grain("2026-01-11", "week") == "2026-01-11"
    assert apply_grain("2026-01-11", None) == "2026-01-11"
    assert apply_grain("2026-01-11", "month") == "2026-01"


def test_model_year_keys_infer_relative_grain() -> None:
    assert infer_grain(["Y1", "Y2", "Y3"]) == "model_year"
    assert infer_grain(["Q1", "Q2", "Q3"]) == "model_quarter"
    assert infer_grain(["M1", "M2", "M3"]) == "model_month"
    assert infer_grain(["P0", "P1", "P2"]) == "model_period"
    assert apply_grain("Y1", "model_year") == "Y1"
