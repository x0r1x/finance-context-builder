"""Row models share one fields-set object until an instance actually changes it."""

from __future__ import annotations

from finance_context.layout.models import LayoutRow, RowCell
from finance_context.models.context import (
    BlockRow,
    NumericSummary,
    RoleCell,
    RowHints,
    RowSeries,
)


def _pair(cls: type):
    kwargs: dict[str, object] = {}
    if cls is BlockRow:
        kwargs = {"row_key": "s|1|b", "sheet": "S", "row": 1, "label": "Revenue", "kind": "fact"}
    elif cls is RowSeries:
        kwargs = {"axis_id": "years"}
    elif cls is RoleCell:
        kwargs = {"addr": "A1", "col": 1, "role": "value"}
    left = cls(**kwargs)
    right = cls(**kwargs)
    return left, right


def test_context_rows_share_one_fields_set() -> None:
    for cls in (BlockRow, RowSeries, NumericSummary, RowHints, RoleCell):
        left, right = _pair(cls)
        assert left.__pydantic_fields_set__ is right.__pydantic_fields_set__
        assert left.model_fields_set == right.model_fields_set


def test_assignment_does_not_mutate_the_shared_set() -> None:
    untouched = RowHints()
    shared = untouched.__pydantic_fields_set__
    snapshot = set(shared)
    changed = RowHints()
    changed.unit = "rate"
    assert shared == snapshot
    assert untouched.__pydantic_fields_set__ is shared
    assert untouched.model_fields_set == snapshot
    assert changed.model_fields_set == snapshot | {"unit"}
    assert changed.__pydantic_fields_set__ is not shared
    again = RowHints(unit="count")
    assert again.model_fields_set == {"unit"}
    assert again.__pydantic_fields_set__ is changed.__pydantic_fields_set__


def test_replacing_a_set_field_keeps_the_shared_set() -> None:
    original = RowHints(unit="rate", scale="k")
    other = RowHints(unit="count", scale="m")
    shared = original.__pydantic_fields_set__
    snapshot = set(shared)
    original.unit = "years"
    assert original.__pydantic_fields_set__ is shared
    assert other.__pydantic_fields_set__ is shared
    assert shared == snapshot
    assert original.unit == "years"
    assert other.unit == "count"
    copied = original.model_copy(update={"scale": "unit", "currency": None})
    assert "currency" in copied.model_fields_set
    assert shared == snapshot
    assert other.model_fields_set == snapshot


def test_model_copy_does_not_mutate_the_shared_set() -> None:
    original = RowSeries(axis_id="years")
    other = RowSeries(axis_id="years")
    shared = original.__pydantic_fields_set__
    snapshot = set(shared)
    copied = original.model_copy(update={"formula": "=A1"})
    assert shared == snapshot
    assert other.__pydantic_fields_set__ is shared
    assert original.model_fields_set == snapshot
    assert copied.model_fields_set == snapshot | {"formula"}
    assert copied.formula == "=A1"
    assert original.formula is None
    again = other.model_copy(update={"formula": "=B1"})
    assert again.__pydantic_fields_set__ is copied.__pydantic_fields_set__
    validated = RowSeries.model_validate({"axis_id": "years", "formula": "=C1"})
    assert validated.model_fields_set == {"axis_id", "formula"}
    assert validated.__pydantic_fields_set__ is copied.__pydantic_fields_set__


def test_layout_assignment_does_not_mutate_the_shared_set() -> None:
    first = LayoutRow(row=1, label="Revenue")
    second = LayoutRow(row=2, label="Cost")
    shared = first.__pydantic_fields_set__
    snapshot = set(shared)
    assert second.__pydantic_fields_set__ is shared
    first.parent_row = 1
    first.kind = "helper"
    assert shared == snapshot
    assert second.__pydantic_fields_set__ is shared
    assert second.model_fields_set == snapshot
    assert first.model_fields_set == snapshot | {"parent_row", "kind"}
    assert second.parent_row is None
    assert second.kind == "fact"
    cell = RowCell(col=3, role="value")
    other = RowCell(col=4, role="value")
    cell_set = cell.__pydantic_fields_set__
    cell_snapshot = set(cell_set)
    copied = cell.model_copy(update={"header": "2024"})
    cell.header = "2024"
    assert cell_set == cell_snapshot
    assert other.__pydantic_fields_set__ is cell_set
    assert other.header is None
    assert other.model_fields_set == cell_snapshot
    assert cell.model_fields_set == cell_snapshot | {"header"}
    assert copied.model_fields_set == cell.model_fields_set
    assert copied.__pydantic_fields_set__ is cell.__pydantic_fields_set__
    assert copied.header == "2024"
    loaded = LayoutRow.model_validate(first.model_dump())
    again = LayoutRow.model_validate(first.model_dump())
    assert loaded.model_fields_set == again.model_fields_set
    assert loaded.__pydantic_fields_set__ is again.__pydantic_fields_set__
    assert loaded.parent_row == 1
    assert loaded.kind == "helper"


def test_many_block_rows_keep_one_fields_set() -> None:
    rows = [
        BlockRow(row_key=f"s|{index}|b", sheet="S", row=index, label="Revenue", kind="fact")
        for index in range(500)
    ]
    shared = {id(row.__pydantic_fields_set__) for row in rows}
    assert shared == {id(rows[0].__pydantic_fields_set__)}
    rows[0].check_row = True
    assert rows[1].model_fields_set == rows[2].model_fields_set
    assert "check_row" not in rows[1].model_fields_set
    assert rows[0].model_fields_set == rows[1].model_fields_set | {"check_row"}
