from __future__ import annotations

import pytest
from tests.helpers.slice_book import (
    BLANK,
    DISCOUNT,
    MYSTERY,
    OTHER,
    REVENUE,
    TOTAL,
    ZERO,
    sample_context,
    sample_links,
)

from finance_context.context.observations import build_observations
from finance_context.models.context import ContextAxis, ContextPeriod, FinancialBlock
from finance_context.models.observation import ObservationPrecedent


def _dump(context, **kwargs) -> dict:
    document = build_observations(context, sample_links(), **kwargs)
    return document.model_dump(mode="json", by_alias=True)


def test_revenue_observation_matches_the_slice_fields() -> None:
    payload = _dump(sample_context(), row_keys=[REVENUE])
    assert payload["truncated"] is False
    assert payload["observations"] == [
        {
            "row_key": REVENUE,
            "label": "Revenue",
            "concept_id": "pnl.revenue",
            "disposition": "mapped",
            "dimensions": {"segment": "pc"},
            "period_id": "Y5",
            "value": "1234.56",
            "value_status": "cached",
            "normalized_value": "1234560",
            "scale_factor": 1000,
            "period_position": "during_period",
            "aggregation": "sum",
            "unit": {
                "kind": "money",
                "currency": "GBP",
                "scale": "k",
                "sign": "inflow",
            },
            "formula": {
                "text": "=RC[-1]*(1+Growth)",
                "class": "cross_period",
                "precedents": [],
                "precedents_total": 0,
            },
            "source": {"sheet": "Operation", "cell": "H10"},
            "timeline": {
                "axis_id": "Operation!r8",
                "phase": "operation",
                "phase_year": 1,
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
            },
        }
    ]


def test_sum_precedents_include_child_rows() -> None:
    def lookup(origin: str, depth: int) -> list[ObservationPrecedent]:
        assert origin == "Operation!H12"
        assert depth == 1
        return [
            ObservationPrecedent(
                row_key=REVENUE,
                concept_id="pnl.revenue",
                period_id="Y5",
                value="1234.56",
                cell="H10",
                depth=1,
            ),
            ObservationPrecedent(
                row_key=OTHER,
                concept_id="pnl.other",
                period_id="Y5",
                value="765.44",
                cell="H11",
                depth=1,
            ),
        ]

    payload = _dump(
        sample_context(),
        row_keys=[TOTAL],
        precedent_depth=1,
        precedents=lookup,
    )
    precedents = payload["observations"][0]["formula"]["precedents"]
    assert [item["row_key"] for item in precedents] == [REVENUE, OTHER]
    assert [item["label"] for item in precedents] == ["Revenue", "Other income"]
    assert [item["depth"] for item in precedents] == [1, 1]
    assert payload["observations"][0]["formula"]["class"] == "aggregation"
    formula = payload["observations"][0]["formula"]
    assert formula["precedents_total"] == len(precedents) == 2
    assert "precedents_truncated" not in formula
    assert all(item["row_key"] for item in precedents)


def test_depth_zero_does_not_call_trace() -> None:
    def lookup(origin: str, depth: int) -> list[ObservationPrecedent]:
        raise AssertionError(origin)

    payload = _dump(
        sample_context(),
        row_keys=[TOTAL],
        precedent_depth=0,
        precedents=lookup,
    )
    formula = payload["observations"][0]["formula"]
    assert formula["precedents"] == []
    assert formula["precedents_total"] == 0
    assert "precedents_truncated" not in formula


def test_observation_limit_does_not_cut_precedents() -> None:
    def lookup(origin: str, depth: int) -> list[ObservationPrecedent]:
        return [
            ObservationPrecedent(
                row_key=f"child-{index}",
                value="1",
                cell=f"A{index}",
                depth=1,
            )
            for index in range(5)
        ]

    payload = _dump(
        sample_context(),
        row_keys=[TOTAL],
        precedent_depth=1,
        limit=2,
        precedents=lookup,
    )
    formula = payload["observations"][0]["formula"]
    assert payload["truncated"] is False
    assert len(formula["precedents"]) == 5
    assert formula["precedents_total"] == 5


def test_direct_inputs_stay_past_the_old_page_size() -> None:
    def lookup(origin: str, depth: int) -> list[ObservationPrecedent]:
        return [
            ObservationPrecedent(row_key=f"child-{index}", value="1", cell="A1", depth=1)
            for index in range(25)
        ]

    payload = _dump(
        sample_context(),
        row_keys=[TOTAL],
        precedent_depth=1,
        precedents=lookup,
    )
    formula = payload["observations"][0]["formula"]
    assert payload["truncated"] is False
    assert len(formula["precedents"]) == 25
    assert formula["precedents_total"] == 25


def test_a_wide_formula_keeps_every_direct_input() -> None:
    def lookup(origin: str, depth: int) -> list[ObservationPrecedent]:
        direct = [
            ObservationPrecedent(row_key=f"in-{index}", value="1", cell="A1", depth=1)
            for index in range(130)
        ]
        deeper = [ObservationPrecedent(row_key="deep-0", value="1", cell="B1", depth=2)]
        return [*direct, *deeper]

    payload = _dump(
        sample_context(),
        row_keys=[TOTAL],
        precedent_depth=2,
        precedents=lookup,
    )
    formula = payload["observations"][0]["formula"]
    assert payload["truncated"] is False
    assert formula["precedents_total"] == 131
    assert [item["row_key"] for item in formula["precedents"]] == [
        *[f"in-{index}" for index in range(130)],
        "deep-0",
    ]


def test_deeper_precedents_fill_the_page_budget() -> None:
    def lookup(origin: str, depth: int) -> list[ObservationPrecedent]:
        direct = [
            ObservationPrecedent(row_key=f"in-{index}", value="1", cell="A1", depth=1)
            for index in range(10)
        ]
        deeper = [
            ObservationPrecedent(row_key=f"deep-{index}", value="1", cell="B1", depth=2)
            for index in range(130)
        ]
        return [*direct, *deeper]

    payload = _dump(
        sample_context(),
        row_keys=[TOTAL],
        precedent_depth=2,
        precedents=lookup,
    )
    formula = payload["observations"][0]["formula"]
    embedded = formula["precedents"]
    assert payload["truncated"] is False
    assert formula["precedents_total"] == 140
    assert len(embedded) == 140
    assert [item["row_key"] for item in embedded[:10]] == [f"in-{index}" for index in range(10)]
    assert [item["row_key"] for item in embedded[10:]] == [f"deep-{index}" for index in range(130)]


def test_precedent_outside_the_layout_keeps_an_empty_label() -> None:
    def lookup(origin: str, depth: int) -> list[ObservationPrecedent]:
        return [ObservationPrecedent(value="1", cell="Z9", depth=2)]

    payload = _dump(
        sample_context(),
        row_keys=[TOTAL],
        precedent_depth=1,
        precedents=lookup,
    )
    precedent = payload["observations"][0]["formula"]["precedents"][0]
    assert precedent["row_key"] is None
    assert precedent["label"] is None
    assert precedent["depth"] == 2


def test_abstained_is_found_by_label_and_not_by_a_foreign_concept() -> None:
    found = _dump(sample_context(), q="mystery")
    assert len(found["observations"]) == 1
    assert found["observations"][0]["row_key"] == MYSTERY
    assert found["observations"][0]["concept_id"] is None
    missed = _dump(sample_context(), concept_ids=["pnl.nope"])
    assert missed["observations"] == []
    assert missed["truncated"] is False


def test_params_scenario_comes_from_the_value_header() -> None:
    payload = _dump(sample_context(), row_keys=[DISCOUNT])
    observation = payload["observations"][0]
    assert observation["scenario"] == "Live"
    assert observation["period_id"] == "live"
    assert observation["value"] == "0.05"
    assert observation["source"] == {"sheet": "Inputs", "cell": "B20"}
    assert "timeline" not in observation
    assert observation["period_position"] == "instant"
    assert observation["aggregation"] == "none"


def test_empty_cell_stays_null_and_explicit_zero_stays_text() -> None:
    blank = _dump(sample_context(), row_keys=[BLANK])["observations"][0]
    assert blank["value"] is None
    assert blank["value_status"] == "empty"
    assert blank["value"] != "0"
    zero = _dump(sample_context(), row_keys=[ZERO])["observations"][0]
    assert zero["value"] == "0"
    assert zero["value_status"] == "zero_explicit"


def test_limit_sets_truncated() -> None:
    payload = _dump(sample_context(), row_keys=[REVENUE, OTHER, TOTAL], limit=1)
    assert payload["truncated"] is True
    assert len(payload["observations"]) == 1
    assert payload["observations"][0]["row_key"] == REVENUE


def test_selector_is_required() -> None:
    with pytest.raises(ValueError, match="selector_required"):
        build_observations(sample_context(), sample_links())


def test_second_axis_without_a_series_is_not_filled_from_row_values() -> None:
    context = sample_context()
    context.axes.append(
        ContextAxis(
            id="Operation!r99",
            sheet="Operation",
            grain="year",
            header_row=99,
            periods=[ContextPeriod(col=9, period_key="Y6", index=6)],
        )
    )
    block = context.blocks[0]
    assert isinstance(block, FinancialBlock)
    block.axis_ids = ["Operation!r8", "Operation!r99"]
    payload = _dump(context, row_keys=[REVENUE])
    assert [item["timeline"]["axis_id"] for item in payload["observations"]] == ["Operation!r8"]
