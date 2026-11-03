from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq

from finance_context.store.duckdb_io import read_parquet_dicts
from finance_context.store.fs import write_parquet

_COLUMNS = (
    ("sheet", "VARCHAR"),
    ("row", "INTEGER"),
    ("col", "INTEGER"),
    ("formula_template", "VARCHAR"),
    ("number_format", "VARCHAR"),
    ("cached_value", "VARCHAR"),
)


def _write(path: Path) -> list[tuple]:
    rows = [
        ("Sheet1", 300, 1, "=A1", "0.00", "one"),
        ("Sheet1", 300, 1, "=A1", "0.00", "two"),
        ("Sheet1", 300, 2, "=A1", "0.00", "three"),
        ("Sheet1", 301, 1, "=B1", "0.00", "four"),
        ("Other", 301, 1, "=B1", None, "five"),
        ("Other", 301, 1, None, None, "six"),
    ]
    write_parquet(path, _COLUMNS, rows, row_group_rows=0)
    return rows


def test_streamed_dicts_match_values_and_share_repeats(tmp_path: Path) -> None:
    path = tmp_path / "cells.parquet"
    expected = _write(path)
    names = [name for name, _dtype in _COLUMNS]
    found = read_parquet_dicts(path, names)
    assert [tuple(row[name] for name in names) for row in found] == expected
    arrow = pq.read_table(path).select(names).to_pylist()
    assert found == arrow

    assert found[0]["sheet"] is found[1]["sheet"] is found[2]["sheet"] is found[3]["sheet"]
    assert found[0]["sheet"] is not found[4]["sheet"]
    assert found[4]["sheet"] is found[5]["sheet"]
    assert found[0]["row"] is found[1]["row"] is found[2]["row"]
    assert found[0]["row"] is not found[3]["row"]
    assert found[3]["row"] is found[4]["row"]
    assert found[0]["formula_template"] is found[1]["formula_template"]
    assert found[0]["formula_template"] is not found[3]["formula_template"]
    assert found[3]["formula_template"] is found[4]["formula_template"]
    assert found[5]["formula_template"] is None
    assert found[0]["number_format"] is found[1]["number_format"] is found[3]["number_format"]
    assert found[4]["number_format"] is None
    assert found[0]["cached_value"] == "one"
    assert found[0]["cached_value"] is not found[1]["cached_value"]


def test_where_clause_still_filters(tmp_path: Path) -> None:
    path = tmp_path / "cells.parquet"
    _write(path)
    found = read_parquet_dicts(
        path,
        ["sheet", "row", "cached_value"],
        where="row = ?",
        params=[301],
    )
    assert [row["cached_value"] for row in found] == ["four", "five", "six"]
    assert found[0]["row"] is found[1]["row"] is found[2]["row"]
