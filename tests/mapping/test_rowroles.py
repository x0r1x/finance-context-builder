from __future__ import annotations

from finance_context.mapping.models import RowContext
from finance_context.mapping.rowroles import infer_context_role


def test_scenario_chosen_is_selector_not_assumption() -> None:
    ctx = RowContext(
        row_key="Input Assumptions|3|Input Assumptions!r5",
        sheet="Input Assumptions",
        row=3,
        block_id="Input Assumptions!r5",
        label="Scenario Chosen",
        kind="flag",
        article_role="assumption",
    )
    assert infer_context_role(ctx) == "scenario_selector"


def test_assumption_row_is_not_selector() -> None:
    ctx = RowContext(
        row_key="Input Assumptions|8|Input Assumptions!r5",
        sheet="Input Assumptions",
        row=8,
        block_id="Input Assumptions!r5",
        label="Concession Duration",
        kind="fact",
        article_role="assumption",
    )
    assert infer_context_role(ctx) == "assumption"
