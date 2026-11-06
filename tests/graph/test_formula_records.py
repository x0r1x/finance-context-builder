from pathlib import Path

from finance_context.formulas.models import Edge
from finance_context.graph.stage import (
    _attach_formula_text,
    _bind_refs,
    _formulas_from_cells,
    _links_from_formulas,
    _load_formulas,
    _RangeView,
)
from finance_context.store.fs import write_parquet

_COLUMNS = (
    ("sheet", "VARCHAR"),
    ("row", "INTEGER"),
    ("col", "INTEGER"),
    ("addr", "VARCHAR"),
    ("formula_raw", "VARCHAR"),
)


def test_one_record_is_the_link() -> None:
    cells = [
        {"sheet": "S", "row": 1, "col": 1, "addr": "A1", "formula_raw": "=B1"},
        {"sheet": "S", "row": 2, "col": 1, "addr": "A2", "formula_raw": None},
        {"sheet": "S", "row": 3, "col": 2, "addr": "B3", "formula_raw": "=A1"},
    ]
    row_meta = {("S", 1): "S|1|b", ("S", 3): "S|3|b"}
    period = {("S", 1): "2024", ("S", 2): "2025"}
    formulas = _formulas_from_cells(cells, row_meta, period)
    assert set(formulas) == {"S!A1", "S!B3"}
    assert formulas["S!A1"].row_key == "S|1|b"
    assert formulas["S!A1"].period_id == "2024"
    assert formulas["S!B3"].period_id == "2025"
    view = _RangeView(Path("."), formulas, period)
    hops = view.hops()
    edges = [
        Edge(kind="ref", source="S!A1", target="S!B1"),
        Edge(kind="ref", source="S!A1", target="S!B1"),
        Edge(kind="ref", source="S!A1", target=None),
        Edge(kind="ref", source="S!A1", target="S!C1"),
        Edge(kind="ref", source="S!Z9", target="S!A1"),
        Edge(kind="ref", source="", target="S!A1"),
    ]
    _bind_refs(formulas, edges)
    assert formulas["S!A1"].refs == ["S!B1", "S!C1"]
    assert formulas["S!Z9"].formula is None
    assert formulas["S!Z9"].refs == ["S!A1"]
    assert view.hops() is hops
    assert ("S", 26) not in hops.rows
    dumped = [link.model_dump() for link in _links_from_formulas(formulas, None)]
    assert [item["cell"] for item in dumped] == ["S!A1", "S!B3", "S!Z9"]
    assert dumped[0]["formula"] == "=B1"
    assert dumped[0]["refs"] == ["S!B1", "S!C1"]
    assert dumped[0]["row_key"] == "S|1|b"
    assert dumped[2]["formula"] is None
    assert dumped[2]["row_key"] is None


def test_load_formulas_skips_values(tmp_path: Path) -> None:
    book = tmp_path / "book"
    (book / "ir").mkdir(parents=True)
    write_parquet(
        book / "ir" / "cells.parquet",
        _COLUMNS,
        [
            ("S", 1, 1, "A1", "=B1"),
            ("S", 2, 1, "A2", None),
            ("S", 3, 2, "B3", ""),
        ],
    )
    found = _load_formulas(book, {("S", 1): "S|1|b"}, {("S", 1): "2024"})
    assert list(found) == ["S!A1"]
    assert found["S!A1"].formula is None
    assert found["S!A1"].formula_cell is True
    assert found["S!A1"].row_key == "S|1|b"
    assert found["S!A1"].period_id == "2024"
    view = _RangeView(book, found, {("S", 1): "2024"})
    assert list(view.hops().rows[("S", 1)]) == [1]
    _attach_formula_text(book, found)
    assert found["S!A1"].formula == "=B1"
