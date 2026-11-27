from __future__ import annotations

import json
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx
from tests.synthetic.support import run_book

from finance_context.excel.a1 import format_addr


def test_published_block_kinds_keep_their_column_maps(tmp_path: Path) -> None:
    note = "Источник: бизнес-план компании на 2025 год, утверждён советом"
    tables = {
        "Cash Flow": [("Item", "2023", "2024", "2025"), ("Revenue", 10, 20, 30)],
        "Inputs": [
            ("Параметр", "Значение", "Ед. изм.", "Примечание"),
            ("Цена", 12, "тыс. руб.", note),
            ("Доля расходов", 0.4, "%", note),
            ("Ставка налога", 0.2, "%", note),
        ],
        "Uses": [("Name", "Amount", "Multiple"), ("Rent", 10, 2), ("Tax", 5, 1)],
        "Sensitivity": [("Case", "Low", "Base", "High"), ("Rent", 10, 20, 30), ("Tax", 1, 2, 3)],
    }
    sheets = [
        SheetSpec(
            name=sheet,
            cells=[
                CellSpec(
                    format_addr(col, row),
                    value=str(value),
                    type="inlineStr" if isinstance(value, str) else None,
                )
                for row, values in enumerate(table, start=1)
                for col, value in enumerate(values, start=1)
            ],
        )
        for sheet, table in tables.items()
    ]
    xlsx = build_xlsx(tmp_path / "mixed.xlsx", sheets=sheets)
    dest = tmp_path / "job"
    run_book(xlsx, dest, filename=xlsx.name, job_id="mixed")
    document = json.loads((dest / "context/context.json").read_text(encoding="utf-8"))

    blocks = {block["kind"]: block for block in document["blocks"]}
    assert [block["kind"] for block in document["blocks"]] == [
        "timeline",
        "params",
        "key_value",
        "matrix",
    ]
    (axis,) = document["axes"]
    timeline = blocks["timeline"]
    assert timeline["axis_ids"] == [axis["id"]]
    assert timeline["periods"] == []
    assert timeline["rows"][0]["series"][0]["axis_id"] == axis["id"]
    assert [period["period_key"] for period in axis["periods"]] == ["2023", "2024", "2025"]

    params = blocks["params"]
    assert params["periods"] == [
        {"col": 2, "text": "Значение", "period_key": "value", "role": "value"}
    ]
    assert {(c["addr"], c["role"]) for c in params["rows"][0]["cells"]} >= {
        ("C2", "unit"),
        ("D2", "note"),
    }
    assert blocks["key_value"]["sheet"] == "Uses"
    assert blocks["matrix"]["sheet"] == "Sensitivity"
    for kind, captions, values in (
        ("params", ["Значение"], ["12"]),
        ("key_value", ["Amount", "Multiple"], ["10", "2"]),
        ("matrix", ["Low", "Base", "High"], ["10", "20", "30"]),
    ):
        block = blocks[kind]
        assert block["axis_ids"] == []
        periods = block["periods"]
        assert [(p["col"], p["text"]) for p in periods] == list(enumerate(captions, start=2))
        if kind != "params":
            assert [p["period_key"] for p in periods] == [f"c{p['col']}" for p in periods]
        row = block["rows"][0]
        (series,) = row["series"]
        assert series["axis_id"] == block["block_id"]
        assert row["values"] == series["values"] == values
        assert row["value_statuses"] == series["value_statuses"] == ["cached"] * len(periods)
        assert row["normalized_values"] == series["normalized_values"]
        assert len(series["normalized_values"]) == len(periods)

    assert document["schema_version"] == document["meta"]["schema_version"] == "1.15.0"
