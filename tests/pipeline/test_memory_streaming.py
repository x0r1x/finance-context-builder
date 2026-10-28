"""Streaming switches keep JSON bytes and parquet values. Raw bytes may differ."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.app.pipeline import Pipeline
from finance_context.excel.stage import parse_workbook
from finance_context.memory import trim_process_heap
from finance_context.settings import Settings

_JSON = (
    "context/context.json",
    "context/context.md",
    "graph/graph.json",
    "graph/graph.md",
    "layout/layout.json",
    "raw/workbook.json",
)
_PARQUET = (
    "raw/cells.parquet",
    "raw/cell_presence.parquet",
    "ir/cells.parquet",
    "ir/edges.parquet",
    "ir/cell_edges.parquet",
    "graph/edges.parquet",
    "graph/index.parquet",
)


def _model(path: Path) -> None:
    build_xlsx(
        path,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="100"),
                    CellSpec(addr="C2", value="110", formula="=B2+10"),
                    CellSpec(addr="A3", value="Cost", type="s"),
                    CellSpec(addr="B3", value="40"),
                    CellSpec(addr="C3", value="44", formula="=B3*1.1"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "2024", "Revenue", "Cost"],
    )


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(data_dir=tmp_path / "data", _env_file=None, **overrides)  # type: ignore[arg-type]


def _run(tmp_path: Path, name: str, **overrides: object) -> Path:
    dest = tmp_path / name
    dest.mkdir()
    source = dest / "source" / "source.xlsx"
    _model(source)
    Pipeline(_settings(tmp_path, **overrides)).run(
        dest, job_id="job", source_filename="model.xlsx"
    )
    return dest


def _same_table(left: Path, right: Path) -> bool:
    """Compare parquet values. DuckDB marks filled columns not-null; Arrow does not."""
    return _nullable(pq.read_table(left)).equals(_nullable(pq.read_table(right)))


def _nullable(table: pa.Table) -> pa.Table:
    schema = table.schema
    fields = [field.with_nullable(True) for field in schema]
    return table.cast(pa.schema(fields))


def test_defaults_match_every_switch_off(tmp_path: Path) -> None:
    on = _run(tmp_path, "on")
    off = _run(
        tmp_path,
        "off",
        parse_streaming=False,
        json_stream=False,
        parquet_row_group_rows=0,
        compile_string_pool=False,
        compile_ast_zlib=False,
        graph_stream=False,
        layout_columnar_facts=False,
        job_malloc_trim=False,
        job_malloc_arena_max=0,
    )
    for name in _JSON:
        assert (on / name).read_bytes() == (off / name).read_bytes(), name
    value_only = {"raw/cells.parquet", "raw/cell_presence.parquet", "ir/cells.parquet"}
    for name in _PARQUET:
        assert _same_table(on / name, off / name), name
        if name not in value_only:
            assert (on / name).read_bytes() == (off / name).read_bytes(), name


def test_streaming_parse_matches_dom_bytes(tmp_path: Path) -> None:
    source = tmp_path / "model.xlsx"
    _model(source)
    streamed = tmp_path / "stream"
    dom = tmp_path / "dom"
    streamed.mkdir()
    dom.mkdir()
    parse_workbook(source, streamed, settings=_settings(tmp_path, parse_streaming=True))
    parse_workbook(source, dom, settings=_settings(tmp_path, parse_streaming=False))
    for name in ("raw/cells.parquet", "raw/cell_presence.parquet"):
        assert _same_table(streamed / name, dom / name), name
    assert (streamed / "raw" / "workbook.json").read_bytes() == (
        dom / "raw" / "workbook.json"
    ).read_bytes()


def test_streaming_raw_parquet_is_split_into_row_groups(tmp_path: Path) -> None:
    """DuckDB holds one row group whole under its 256MB limit while it writes it."""
    source = tmp_path / "long.xlsx"
    cells = [CellSpec(addr=f"A{row}", value=str(row)) for row in range(1, 5001)]
    build_xlsx(source, sheets=[SheetSpec(name="Data", cells=cells)])
    split = tmp_path / "split"
    whole = tmp_path / "whole"
    split.mkdir()
    whole.mkdir()
    parse_workbook(source, split, settings=_settings(tmp_path, parquet_row_group_rows=2048))
    parse_workbook(source, whole, settings=_settings(tmp_path, parquet_row_group_rows=0))
    raw = "raw/cells.parquet"
    assert pq.ParquetFile(split / raw).num_row_groups > 1
    assert pq.ParquetFile(whole / raw).num_row_groups == 1
    assert _same_table(split / raw, whole / raw)


def test_row_group_of_one_keeps_values(tmp_path: Path) -> None:
    wide = _run(tmp_path, "wide", parquet_row_group_rows=0)
    split = _run(tmp_path, "split", parquet_row_group_rows=1)
    for name in _JSON:
        assert (wide / name).read_bytes() == (split / name).read_bytes(), name
    for name in ("raw/cells.parquet", "raw/cell_presence.parquet"):
        assert (wide / name).read_bytes() == (split / name).read_bytes(), name
    assert _same_table(wide / "ir" / "cells.parquet", split / "ir" / "cells.parquet")
    edges = split / "ir" / "cell_edges.parquet"
    assert pq.ParquetFile(edges).num_row_groups > 1
    assert _same_table(wide / "ir" / "cell_edges.parquet", edges)
    assert (wide / "ir" / "cell_edges.parquet").read_bytes() != edges.read_bytes()
    materialized = _run(
        tmp_path, "materialized", parquet_row_group_rows=1, graph_stream=False
    )
    for name in ("graph/edges.parquet", "graph/index.parquet"):
        assert _same_table(split / name, materialized / name), name


def test_memory_switches_reject_non_bools_and_non_finite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARSE_STREAMING", raising=False)
    monkeypatch.setenv("PARSE_STREAMING", "inf")
    with pytest.raises(ValidationError, match="PARSE_STREAMING must be a boolean"):
        Settings(_env_file=None)
    monkeypatch.delenv("PARSE_STREAMING", raising=False)
    monkeypatch.setenv("PARQUET_ROW_GROUP_ROWS", "nan")
    with pytest.raises(ValidationError, match="PARQUET_ROW_GROUP_ROWS"):
        Settings(_env_file=None)
    monkeypatch.setenv("PARQUET_ROW_GROUP_ROWS", "true")
    with pytest.raises(ValidationError, match="PARQUET_ROW_GROUP_ROWS"):
        Settings(_env_file=None)
    monkeypatch.setenv("PARQUET_ROW_GROUP_ROWS", "-1")
    with pytest.raises(ValidationError, match="PARQUET_ROW_GROUP_ROWS"):
        Settings(_env_file=None)
    monkeypatch.setenv("PARQUET_ROW_GROUP_ROWS", "65536")
    monkeypatch.setenv("JOB_MALLOC_ARENA_MAX", "inf")
    with pytest.raises(ValidationError, match="JOB_MALLOC_ARENA_MAX"):
        Settings(_env_file=None)
    monkeypatch.setenv("JOB_MALLOC_ARENA_MAX", "0")
    assert Settings(_env_file=None).job_malloc_arena_max is None
    monkeypatch.setenv("JSON_STREAM", "false")
    assert Settings(_env_file=None).json_stream is False


def test_trim_is_safe() -> None:
    assert trim_process_heap() in {True, False}


def test_streamed_json_matches_dumps(tmp_path: Path) -> None:
    from finance_context.store.fs import write_json

    payload = {"b": 1, "a": "й", "nested": {"z": None}}
    streamed = tmp_path / "stream.json"
    dumped = tmp_path / "dump.json"
    write_json(streamed, payload, sort_keys=True, stream=True)
    write_json(dumped, payload, sort_keys=True, stream=False)
    assert streamed.read_bytes() == dumped.read_bytes()
    assert json.loads(streamed.read_text(encoding="utf-8"))["a"] == "й"
