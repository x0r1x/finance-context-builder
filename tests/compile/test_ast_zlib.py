"""Streaming compile does not zlib-compress AST JSON it is about to write."""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.excel.stage import parse_workbook
from finance_context.formulas.stage import compile_workbook
from finance_context.settings import Settings


def _compile(tmp_path: Path, name: str, **flags: object) -> Path:
    dest = tmp_path / name
    dest.mkdir()
    build_xlsx(
        dest / "book.xlsx",
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="1"),
                    CellSpec(addr="B1", value="2", formula="=A1+A1"),
                    CellSpec(addr="C1", value="3", formula="=SUM(A1:A1)+B1"),
                ],
            )
        ],
    )
    settings = Settings(data_dir=tmp_path / "data", _env_file=None, **flags)  # type: ignore[arg-type]
    parse_workbook(dest / "book.xlsx", dest, settings=settings)
    compile_workbook(dest, settings=settings)
    return dest


def test_streaming_skips_zlib_and_cells_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    real = zlib.compress

    def spy(data: bytes, level: int = -1) -> bytes:
        calls["n"] += 1
        return real(data, level)

    monkeypatch.setattr("finance_context.formulas.stage.zlib.compress", spy)
    compressed = _compile(tmp_path, "on", parse_streaming=True, compile_ast_zlib=True)
    assert calls["n"] == 0
    plain = _compile(tmp_path, "off", parse_streaming=True, compile_ast_zlib=False)
    assert calls["n"] == 0
    assert (compressed / "ir" / "cells.parquet").read_bytes() == (
        plain / "ir" / "cells.parquet"
    ).read_bytes()


def test_non_streaming_still_compresses_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}
    real = zlib.compress

    def spy(data: bytes, level: int = -1) -> bytes:
        calls["n"] += 1
        return real(data, level)

    monkeypatch.setattr("finance_context.formulas.stage.zlib.compress", spy)
    compressed = _compile(tmp_path, "on", parse_streaming=False, compile_ast_zlib=False)
    assert calls["n"] == 0
    calls["n"] = 0
    plain = _compile(tmp_path, "off", parse_streaming=False, compile_ast_zlib=True)
    assert calls["n"] > 0
    assert (compressed / "ir" / "cells.parquet").read_bytes() == (
        plain / "ir" / "cells.parquet"
    ).read_bytes()
