from __future__ import annotations

import pytest

from finance_context.layout.periods import (
    _marker_word,
    apply_grain,
    classify_atom,
    classify_header,
    compose_period,
    infer_grain,
    marker_token,
)

# Casefolded suffix letter to the structural marker key.
_SUFFIX_MARKER = {
    "п": "plan",
    "p": "plan",
    "р": "plan",
    "e": "plan",
    "е": "plan",
    "ф": "actual",
    "a": "actual",
    "а": "actual",
}


@pytest.mark.parametrize(
    ("text", "period_key", "variant"),
    [
        ("2023", "2023", None),
        ("2025E", "2025", "E"),
        ("янв.25", "2025-01", None),
        ("2025-01", "2025-01", None),
        ("01.07.2022", "2022-07-01", None),
    ],
)
def test_header_carries_kind_and_period_key(
    text: str, period_key: str, variant: str | None
) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert hit.kind == "period"
    assert hit.period_key == period_key
    assert hit.variant == variant
    assert hit.explicit_role is (variant is not None)


def test_year_fact_plan_share_calendar_key() -> None:
    """The calendar key stays shared. An axis disambiguates it when both appear."""
    fact = classify_header("2024 факт")
    plan = classify_header("2024 план")
    assert fact is not None and plan is not None
    assert fact.period_key == plan.period_key == "2024"
    assert fact.kind == plan.kind == "period"
    assert marker_token(fact.variant) == "факт"
    assert marker_token(plan.variant) == "план"
    assert fact.explicit_role is True
    assert plan.explicit_role is True


def test_month_fact_plan_share_calendar_key() -> None:
    fact = classify_header("янв.25 факт")
    plan = classify_header("янв.25 план")
    assert fact is not None and plan is not None
    assert fact.period_key == plan.period_key == "2025-01"
    assert fact.kind == plan.kind == "period"
    assert marker_token(fact.variant) == "факт"
    assert marker_token(plan.variant) == "план"


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
    fact = classify_header("факт")
    plan = classify_header("план")
    assert fact is not None and plan is not None
    assert fact.kind == plan.kind == "period"
    assert fact.period_key == "actual"
    assert plan.period_key == "plan"
    assert marker_token(fact.variant) == "факт"
    assert marker_token(plan.variant) == "план"


def test_scenario_and_total_and_stub() -> None:
    assert classify_header("Upside").kind == "scenario"  # type: ignore[union-attr]
    assert classify_header("Итого").kind == "total"  # type: ignore[union-attr]
    assert classify_header("stub").kind == "stub"  # type: ignore[union-attr]


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


@pytest.mark.parametrize(
    ("text", "period_key"),
    [
        ("I кв. 2025", "2025Q1"),
        ("IV кв. 2025", "2025Q4"),
        ("1 квартал 2025", "2025Q1"),
        ("2-й квартал 2025", "2025Q2"),
        ("III квартал 2025 г.", "2025Q3"),
        ("Кв.1 2025", "2025Q1"),
        ("кв. 3 2026", "2026Q3"),
        ("1Q25", "2025Q1"),
        ("Q2'25", "2025Q2"),
        ("2025г.", "2025"),
        ("2025 г.", "2025"),
        ("2025 год", "2025"),
        ("01.2025", "2025-01"),
        ("12/2025", "2025-12"),
        ("H1 2025", "2025H1"),
        ("2H25", "2025H2"),
        ("1 полугодие 2025", "2025H1"),
        ("II полугодие 2025 г.", "2025H2"),
    ],
)
def test_russian_and_short_period_headers(text: str, period_key: str) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert hit.kind == "period"
    assert hit.period_key == period_key
    assert hit.variant is None


@pytest.mark.parametrize(
    ("text", "marker", "period_key"),
    [
        ("Факт 2025", "actual", "2025"),
        ("План 2026", "plan", "2026"),
        ("Прогноз 2027", "plan", "2027"),
        ("Budget 2026", "plan", "2026"),
        ("2025 (факт)", "actual", "2025"),
        ("Факт I кв. 2025", "actual", "2025Q1"),
    ],
)
def test_marker_word_before_or_around_the_period(text: str, marker: str, period_key: str) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert (hit.period_key, hit.kind, hit.explicit_role) == (period_key, "period", True)
    assert hit.variant
    resolved = _marker_word(hit.variant)
    assert resolved is not None and resolved[0] == marker


@pytest.mark.parametrize("text", ["13.2025", "V кв. 2025", "3 полугодие 2025", "1.2025"])
def test_not_a_period_header(text: str) -> None:
    assert classify_header(text) is None


@pytest.mark.parametrize(
    ("text", "kind", "quarter", "half"),
    [
        ("I кв.", "quarter_token", 1, None),
        ("Кв.4", "quarter_token", 4, None),
        ("2 квартал", "quarter_token", 2, None),
        ("H2", "half_token", None, 2),
        ("1 полугодие", "half_token", None, 1),
    ],
)
def test_quarter_and_half_tokens_without_a_year(
    text: str, kind: str, quarter: int | None, half: int | None
) -> None:
    atom = classify_atom(text)
    assert atom is not None
    assert (atom.kind, atom.quarter, atom.half) == (kind, quarter, half)


def test_year_row_over_half_tokens_composes_half_keys() -> None:
    year = classify_atom("2025")
    half = classify_atom("II полугодие")
    assert year is not None and half is not None
    hit = compose_period([year, half])
    assert hit is not None and hit.period_key == "2025H2"


def test_half_year_grain_keeps_both_halves() -> None:
    keys = ["2025H1", "2025H2", "2026H1", "2026H2"]
    assert infer_grain(keys) == "half_year"
    assert [apply_grain(key, "half_year") for key in keys] == keys
    assert apply_grain("2025-08", "half_year") == "2025H2"
    assert apply_grain("2025Q2", "half_year") == "2025H1"


@pytest.mark.parametrize(
    ("text", "quarter", "half", "marker"),
    [
        ("План I кв.", 1, None, "plan"),
        ("Факт H2", None, 2, "actual"),
        ("I кв. план", 1, None, "plan"),
        ("H2 (факт)", None, 2, "actual"),
    ],
)
def test_marker_word_on_a_part_without_a_year(
    text: str, quarter: int | None, half: int | None, marker: str
) -> None:
    atom = classify_atom(text)
    assert atom is not None
    assert (atom.quarter, atom.half, atom.column_kind, atom.explicit_role) == (
        quarter,
        half,
        "period",
        True,
    )
    assert atom.variant
    resolved = _marker_word(atom.variant)
    assert resolved is not None and resolved[0] == marker


def test_year_row_over_marker_tagged_quarter_keeps_the_marker() -> None:
    year = classify_atom("2025")
    part = classify_atom("План I кв.")
    assert year is not None and part is not None
    hit = compose_period([year, part])
    assert hit is not None
    assert hit.period_key == "2025Q1"
    assert hit.kind == "period"
    assert marker_token(hit.variant) == "план"


def test_sparse_quarter_and_half_keys_keep_their_identity() -> None:
    quarters = ["2025Q1", "2025Q3", "2026Q1"]
    grain = infer_grain(quarters)
    assert [apply_grain(key, grain) for key in quarters] == quarters
    halves = ["2025H1", "2026H1"]
    assert infer_grain(halves) == "half_year"
    assert [apply_grain(key, "half_year") for key in halves] == halves


def test_semiannual_month_ends_become_half_years() -> None:
    months = ["2025-06", "2025-12", "2026-06"]
    assert infer_grain(months) == "half_year"
    assert [apply_grain(key, "half_year") for key in months] == ["2025H1", "2025H2", "2026H1"]


@pytest.mark.parametrize("text", ["1 кВт", "2 квота", "3 квм", "4 квартира"])
def test_words_starting_like_a_quarter_are_not_quarters(text: str) -> None:
    assert classify_atom(text) is None


@pytest.mark.parametrize("text", ["2025 график", "2025 группа", "1 кв. 2025 график"])
def test_words_starting_like_a_year_word_are_not_periods(text: str) -> None:
    assert classify_header(text) is None


def test_inflected_year_word_is_still_the_year() -> None:
    hit = classify_header("2025 года")
    assert hit is not None and hit.period_key == "2025"


@pytest.mark.parametrize("text", ["History", "Histogram", "Actually", "Budgets", "Budgeting"])
def test_words_starting_like_a_marker_are_not_markers(text: str) -> None:
    assert classify_atom(text) is None


@pytest.mark.parametrize("text", ["Actuals", "Historical", "Факт", "фактический", "Плановый"])
def test_marker_words_and_russian_inflections_are_markers(text: str) -> None:
    atom = classify_atom(text)
    assert atom is not None and atom.kind == "marker"


@pytest.mark.parametrize(
    ("text", "marker", "period_key"),
    [
        ("Бюджет 2026", "plan", "2026"),
        ("2026 бюджет", "plan", "2026"),
        ("Бюджетный 2026", "plan", "2026"),
        ("Plan 2026", "plan", "2026"),
        ("2026 Plan", "plan", "2026"),
        ("Оценка 2025", "plan", "2025"),
        ("2025 (оценка)", "plan", "2025"),
        ("Ожидание 2025", "plan", "2025"),
        ("Ожид. 2025", "plan", "2025"),
    ],
)
def test_budget_plan_and_estimate_are_variant_markers(
    text: str, marker: str, period_key: str
) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert (hit.period_key, hit.kind, hit.explicit_role) == (period_key, "period", True)
    assert hit.variant
    resolved = _marker_word(hit.variant)
    assert resolved is not None and resolved[0] == marker


@pytest.mark.parametrize("text", ["Бюджет", "Plan", "Оценка", "Ожид."])
def test_bare_budget_plan_and_estimate_are_markers(text: str) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert hit.kind == "period"
    assert hit.period_key == "plan"
    assert hit.variant
    assert hit.explicit_role is True


@pytest.mark.parametrize("text", ["Plant 2026", "Planning", "Plants", "Histogram"])
def test_words_near_plan_are_not_plan_markers(text: str) -> None:
    assert classify_header(text) is None


@pytest.mark.parametrize(
    ("text", "marker", "period_key"),
    [
        ("2025П", "plan", "2025"),
        ("2025Ф", "actual", "2025"),
        ("2025A", "actual", "2025"),
        ("1 кв. 2025П", "plan", "2025Q1"),
        ("1 кв. 2025Ф", "actual", "2025Q1"),
        ("1 кв. 2025A", "actual", "2025Q1"),
        ("Q2 2025П", "plan", "2025Q2"),
        ("1 полугодие 2025П", "plan", "2025H1"),
        ("1 полугодие 2025Ф", "actual", "2025H1"),
        ("H1 2025A", "actual", "2025H1"),
        ("01.2025П", "plan", "2025-01"),
        ("01.2025A", "actual", "2025-01"),
        ("янв.25П", "plan", "2025-01"),
        ("янв.25A", "actual", "2025-01"),
    ],
)
def test_year_suffix_letters_match_on_parts_and_months(
    text: str, marker: str, period_key: str
) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert (hit.period_key, hit.kind, hit.explicit_role) == (period_key, "period", True)
    assert _SUFFIX_MARKER[marker_token(hit.variant)] == marker


@pytest.mark.parametrize(
    ("text", "period_key"),
    [
        ("1 пол. 2025", "2025H1"),
        ("2 пол. 2025", "2025H2"),
        ("1 п/г 2025", "2025H1"),
        ("2 п/г 2025", "2025H2"),
        ("2П25", "2025H2"),
        ("1П2025", "2025H1"),
        ("Полугодие 1 2025", "2025H1"),
        ("Полугодие 2 2026", "2026H2"),
        ("1–й квартал 2025", "2025Q1"),
        ("1–е полугодие 2025", "2025H1"),
    ],
)
def test_half_year_abbreviations_and_en_dash_ordinals(text: str, period_key: str) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert hit.kind == "period"
    assert hit.period_key == period_key
    assert hit.variant is None


@pytest.mark.parametrize(
    ("text", "half"),
    [
        ("Полугодие 1", 1),
        ("1 пол.", 1),
        ("2 пол.", 2),
        ("1 п/г", 1),
    ],
)
def test_half_year_tokens_without_a_year(text: str, half: int) -> None:
    atom = classify_atom(text)
    assert atom is not None
    assert (atom.kind, atom.half) == ("half_token", half)
    assert classify_header(text) is None


def test_half_word_then_number_composes_with_a_year_row() -> None:
    year = classify_atom("2025")
    half = classify_atom("Полугодие 1")
    assert year is not None and half is not None
    hit = compose_period([year, half])
    assert hit is not None and hit.period_key == "2025H1"


@pytest.mark.parametrize(
    "text",
    [
        "1 кВт",
        "2025 график",
        "History",
        "2П",
        "П25",
        "12П25",
        "1 полка",
        "1 полка 2025",
        "полнота 2025",
        "3 пол. 2025",
        "1 п/год",
        "1 п/гВт",
        "2P25",
        "Plant 2026",
    ],
)
def test_half_year_spellings_do_not_swallow_nearby_words(text: str) -> None:
    assert classify_header(text) is None
    assert classify_atom(text) is None


@pytest.mark.parametrize(
    ("text", "marker", "period_key"),
    [
        ("2025A", "actual", "2025"),
        ("2025А", "actual", "2025"),
        ("2025а", "actual", "2025"),
        ("2025P", "plan", "2025"),
        ("2025Р", "plan", "2025"),
        ("2025р", "plan", "2025"),
        ("2025E", "plan", "2025"),
        ("2025Е", "plan", "2025"),
        ("2025П", "plan", "2025"),
        ("2025Ф", "actual", "2025"),
        ("1 кв. 2025А", "actual", "2025Q1"),
        ("1 кв. 2025Р", "plan", "2025Q1"),
        ("Н1 2025", None, "2025H1"),
        ("н2 2025", None, "2025H2"),
        ("1Н25", None, "2025H1"),
        ("2н2025", None, "2025H2"),
    ],
)
def test_cyrillic_lookalikes_in_period_codes_keep_suffix_markers(
    text: str, marker: str | None, period_key: str
) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert hit.kind == "period"
    assert hit.period_key == period_key
    if marker is None:
        assert hit.variant is None
        assert hit.explicit_role is False
    else:
        assert hit.explicit_role is True
        assert _SUFFIX_MARKER[marker_token(hit.variant)] == marker


def test_cyrillic_en_without_a_year_is_a_half_token() -> None:
    atom = classify_atom("Н1")
    assert atom is not None
    assert (atom.kind, atom.half) == ("half_token", 1)
    assert classify_header("Н1") is None


@pytest.mark.parametrize(
    "text",
    ["2025Н", "Начало", "Нет", "1Нет", "Факт", "План", "2025 график", "History"],
)
def test_lookalike_letters_do_not_rewrite_russian_words(text: str) -> None:
    hit = classify_header(text)
    if text in {"Факт", "План"}:
        assert hit is not None and hit.period_key in {"actual", "plan"}
    else:
        assert hit is None


@pytest.mark.parametrize(
    ("text", "period_key"),
    [
        ("2025 Q1", "2025Q1"),
        ("2025Q1", "2025Q1"),
        ("2025-Q1", "2025Q1"),
        ("2025/Q1", "2025Q1"),
        ("Q1-2025", "2025Q1"),
        ("Q1/2025", "2025Q1"),
        ("2025 Q4", "2025Q4"),
        ("2025 H1", "2025H1"),
        ("2025H2", "2025H2"),
        ("2025-H1", "2025H1"),
        ("2025 Н1", "2025H1"),
        ("2025Н2", "2025H2"),
        ("FY2025", "2025"),
        ("FY 2025", "2025"),
        ("FY25", "2025"),
        ("FY 25", "2025"),
    ],
)
def test_year_first_quarters_halves_and_short_fiscal_years(text: str, period_key: str) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert hit.period_key == period_key
    assert hit.kind == "period"
    assert hit.variant is None


def test_fiscal_year_suffix_keeps_its_marker() -> None:
    hit = classify_header("FY25E")
    assert hit is not None
    assert (hit.period_key, hit.kind, hit.explicit_role) == ("2025", "period", True)
    assert marker_token(hit.variant) == "e"


@pytest.mark.parametrize(
    "text",
    [
        "Q5-2025",
        "Q1/2",
        "Q1-2",
        "2025 Q",
        "2025Q",
        "2025H",
        "H5 2025",
        "FY",
        "FY2",
        "FYI",
        "FY250",
        "20251",
        "1 кВт",
        "History",
    ],
)
def test_quarter_separators_and_fiscal_years_reject_nearby_tokens(text: str) -> None:
    assert classify_header(text) is None


@pytest.mark.parametrize(
    ("text", "period_key"),
    [
        ("янв.25", "2025-01"),
        ("январь 2025", "2025-01"),
        ("января 25", "2025-01"),
        ("мая 25", "2025-05"),
        ("мае 25", "2025-05"),
        ("март 2025", "2025-03"),
        ("марта 2025", "2025-03"),
        ("марту 25", "2025-03"),
        ("мартом 2025", "2025-03"),
        ("августа 2025", "2025-08"),
        ("августу 25", "2025-08"),
        ("августом 2025", "2025-08"),
        ("январём 2025", "2025-01"),
        ("сентября 2025", "2025-09"),
        ("сентябрь 2025", "2025-09"),
        ("Dec-25", "2025-12"),
        ("September 2025", "2025-09"),
        ("Jan-25", "2025-01"),
    ],
)
def test_real_month_names_and_inflections(text: str, period_key: str) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert hit.period_key == period_key


@pytest.mark.parametrize(
    "text",
    ["маяк 25", "марка 25", "маркер 25", "июльский 25", "marching 25", "maybe 25", "январский 25"],
)
def test_words_that_start_like_a_month_are_not_months(text: str) -> None:
    assert classify_header(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Upside",
        "Base case",
        "сценарий",
        "сценарий базовый",
        "Сценарий: базовый",
        "Сценарий:базовый",
        "Базовый сценарий",
        "Пессимистичный сценарий",
        "Оптимистичный сценарий",
        "Негативный",
        "Стрессовый сценарий",
    ],
)
def test_scenario_headers_accept_either_word_order(text: str) -> None:
    hit = classify_header(text)
    assert hit is not None
    assert hit.kind == "scenario"
    assert hit.explicit_role is True


@pytest.mark.parametrize(
    "text",
    ["базовый", "Выручка", "мой сценарий", "сцена", "сценарист", "1 кВт", "History"],
)
def test_scenario_words_do_not_match_loose_phrases(text: str) -> None:
    assert classify_header(text) is None
