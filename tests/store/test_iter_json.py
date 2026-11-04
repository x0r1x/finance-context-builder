"""iter_json matches json.dumps, including lazy arrays and both writers."""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any

from finance_context.app.pipeline import _context_json
from finance_context.models.context import (
    ArtifactMeta,
    BlockRow,
    ContextDocument,
    ContextRange,
    FinancialBlock,
    RoleCell,
    WorkbookRaw,
)
from finance_context.store.fs import JsonItems, iter_json, write_json


def _materialize(value: Any) -> Any:
    if isinstance(value, JsonItems):
        return [_materialize(item) for item in value.items()]
    if isinstance(value, dict):
        return {key: _materialize(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_materialize(item) for item in value)
    if isinstance(value, list):
        return [_materialize(item) for item in value]
    return value


def _dumps(value: Any, *, sort_keys: bool) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=sort_keys)


def _assert_identical(value: Any, *, sort_keys: bool) -> None:
    got = "".join(iter_json(value, sort_keys=sort_keys))
    expected = _dumps(_materialize(value), sort_keys=sort_keys)
    assert got == expected


def test_lazy_json_matches_dumps_on_the_stated_edges() -> None:
    rows = JsonItems(lambda: iter([{"v": -0.0}, {"v": 1e20}, {"v": 1e-20}, {"v": 1.0}]))
    cases = [
        {},
        [],
        {"a": {}, "b": [], "c": JsonItems(lambda: iter([]))},
        {"ключ": "значение", "emoji": "€", "quoted": "a\"b\\c\n\t\u0001"},
        {"rows": rows},
        {
            "blocks": JsonItems(
                lambda: iter(
                    [
                        {"rows": JsonItems(lambda: iter([{"a": 1}, {"a": None}]))},
                        {"rows": JsonItems(lambda: iter([]))},
                        {"plain": [1, {"b": True}], "rows": JsonItems(lambda: iter([False]))},
                    ]
                )
            )
        },
        {"nested": [{"rows": JsonItems(lambda: iter([{"u": "й"}]))}]},
        {"pair": ({"rows": JsonItems(lambda: iter([1, 2]))},)},
        {1: "n", 2: JsonItems(lambda: iter([{"ok": True}]))},
        {"nan": [float("nan"), float("inf"), float("-inf"), -0.0]},
    ]
    for sort_keys in (False, True):
        for value in cases:
            try:
                _dumps(_materialize(value), sort_keys=sort_keys)
            except TypeError:
                continue
            _assert_identical(value, sort_keys=sort_keys)


def test_fuzz_lazy_json_is_byte_identical_to_dumps() -> None:
    rnd = random.Random(20260304)
    leaves = [
        None,
        True,
        False,
        0,
        -2,
        -0.0,
        0.0,
        1.0,
        1e20,
        1e-20,
        1e16,
        math.pi,
        "",
        "a",
        "й",
        "ё",
        "€",
        'a"b',
        "x\ny",
        "\\",
        [],
        {},
    ]

    def leaf() -> Any:
        return rnd.choice(leaves)

    def tree(depth: int) -> Any:
        if depth <= 0 or rnd.random() < 0.45:
            if rnd.random() < 0.3:
                items = [leaf() for _ in range(rnd.randint(0, 3))]
                return JsonItems(lambda items=items: iter(items))
            return leaf()
        if rnd.random() < 0.5:
            return [tree(depth - 1) for _ in range(rnd.randint(0, 3))]
        keys = ["a", "b", "ключ", "z", ""]
        obj = {rnd.choice(keys): tree(depth - 1) for _ in range(rnd.randint(0, 3))}
        return obj

    for _ in range(80):
        value = tree(4)
        for sort_keys in (False, True):
            _assert_identical(value, sort_keys=sort_keys)


def test_both_writers_match_dumps_for_plain_documents(tmp_path: Path) -> None:
    """Shared cache, stages, and the API pass ordinary dicts through write_json."""
    documents = [
        {"content_sha256": "abc", "source_filename": "book.xlsx"},
        {"publisher": "stamp", "stages": {"compile": "h1", "layout": "h2"}},
        {"b": 1, "a": "й", "nested": {"z": None, "nums": [-0.0, 1e20, 1.0]}},
        {1: "year", 10: "later"},
    ]
    for index, payload in enumerate(documents):
        for sort_keys in (False, True):
            for stream in (False, True):
                path = tmp_path / f"{index}-{sort_keys}-{stream}.json"
                write_json(path, payload, sort_keys=sort_keys, stream=stream)
                expected = _dumps(payload, sort_keys=sort_keys) + "\n"
                assert path.read_text(encoding="utf-8") == expected


def test_json_items_factory_runs_once_per_write(tmp_path: Path) -> None:
    calls = 0

    def factory() -> Any:
        nonlocal calls
        calls += 1
        yield {"a": 1}

    payload = {"rows": JsonItems(factory)}
    write_json(tmp_path / "once.json", payload, stream=True)
    write_json(tmp_path / "twice.json", payload, stream=False)
    assert calls == 2


def _document() -> ContextDocument:
    return ContextDocument(
        meta=ArtifactMeta(job_id="job1", status="succeeded", stage="done"),
        workbook=WorkbookRaw(sheets=["P&L"], sheet_count=1, cell_count=4, formula_count=1),
        blocks=[
            FinancialBlock(
                block_id="P&L!r1",
                sheet="P&L",
                label_col=1,
                periods=[{"col": 2, "text": "2024", "period_key": "2024"}],
                rows=[
                    BlockRow(
                        row_key="P&L|2|P&L!r1",
                        sheet="P&L",
                        row=2,
                        kind="fact",
                        label="Выручка",
                        values=["10", None],
                        value_statuses=["cached", "empty"],
                        cells=[RoleCell(addr="A2", col=1, role="label", header="Item")],
                    ),
                    BlockRow(
                        row_key="P&L|3|P&L!r1",
                        sheet="P&L",
                        row=3,
                        kind="fact",
                        label="Cost",
                        neighbor_ranges=["P&L!R1"],
                        values=["4"],
                    ),
                ],
            ),
            FinancialBlock(
                block_id="P&L!r10",
                sheet="P&L",
                label_col=1,
                rows=[],
            ),
        ],
        ranges=[
            ContextRange(
                id="P&L!R1",
                sheet="P&L",
                row_start=2,
                row_end=3,
                cells=2,
                readers=["P&L!3"],
            )
        ],
        warnings=["проверка"],
    )


def test_context_json_matches_model_dump_on_both_writers(tmp_path: Path) -> None:
    doc = _document()
    bare = ContextDocument(
        meta=ArtifactMeta(job_id="job1", status="succeeded", stage="done"),
        workbook=WorkbookRaw(),
    )
    for document in (doc, bare):
        payload = _context_json(document)
        expected = _dumps(document.model_dump(mode="json"), sort_keys=True) + "\n"
        assert "".join(iter_json(payload, sort_keys=True)) + "\n" == expected
        for stream in (False, True):
            path = tmp_path / f"{stream}-{document.meta.job_id}-{len(document.blocks)}.json"
            write_json(path, payload, sort_keys=True, stream=stream)
            assert path.read_text(encoding="utf-8") == expected
