from __future__ import annotations

from finance_context.layout.models import LayoutRow
from finance_context.mapping.exclusion import exclusion_reason
from finance_context.mapping.models import RowContext
from finance_context.mapping.roles import article_role


def test_check_rows_are_excluded() -> None:
    row = LayoutRow(row=3, label="Tie-out", check_row=True)
    ctx = RowContext(
        row_key="S|3|S!r1",
        sheet="S",
        row=3,
        block_id="S!r1",
        label="Tie-out",
        article_role=article_role(row, ["=A1"]),
    )
    assert exclusion_reason(ctx) == "check"
