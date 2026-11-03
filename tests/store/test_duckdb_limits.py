from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from pydantic import ValidationError

from finance_context.settings import Settings
from finance_context.store.duckdb_io import DuckDBBatch, copy_arrow_parquet, limited_connection

_LIMIT_NAMES = ("memory_limit", "max_temp_directory_size")


def _current(con: Any, name: str) -> object:
    rows = con.execute(f"SELECT current_setting('{name}')").fetchall()
    assert rows
    return rows[0][0]


def _applied(name: str, value: str) -> object:
    import duckdb

    con = duckdb.connect()
    try:
        quoted = "'" + value.replace("'", "''") + "'"
        con.execute(f"SET {name}={quoted}")
        return _current(con, name)
    finally:
        con.close()


def _assert_both(tmp_path: Path, memory: str, temp: str) -> None:
    expected = {
        name: _applied(name, value)
        for name, value in zip(_LIMIT_NAMES, (memory, temp), strict=True)
    }
    batch = DuckDBBatch(tmp_path / "batch.duckdb", (("addr", "VARCHAR"),))
    try:
        assert _current(batch._con, "memory_limit") == expected["memory_limit"]
        assert (
            _current(batch._con, "max_temp_directory_size") == (expected["max_temp_directory_size"])
        )
        assert _current(batch._con, "threads") == 1
    finally:
        batch.abort()
    with limited_connection() as con:
        assert _current(con, "memory_limit") == expected["memory_limit"]
        assert _current(con, "max_temp_directory_size") == expected["max_temp_directory_size"]
        assert _current(con, "threads") == 1


def test_defaults_reach_both_connections(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DUCKDB_MEMORY_LIMIT", raising=False)
    monkeypatch.delenv("DUCKDB_MAX_TEMP_DIRECTORY_SIZE", raising=False)
    settings = Settings()
    assert settings.duckdb_memory_limit == "256MB"
    assert settings.duckdb_max_temp_directory_size == "1GB"
    _assert_both(tmp_path, "256MB", "1GB")


def test_copy_arrow_parquet_reports_configured_caps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "512MB")
    monkeypatch.setenv("DUCKDB_MAX_TEMP_DIRECTORY_SIZE", "2GB")
    seen: dict[str, object] = {}
    real = limited_connection

    @contextmanager
    def _record() -> Iterator[Any]:
        with real() as con:
            seen["memory_limit"] = _current(con, "memory_limit")
            seen["max_temp_directory_size"] = _current(con, "max_temp_directory_size")
            seen["threads"] = _current(con, "threads")
            yield con

    monkeypatch.setattr("finance_context.store.duckdb_io.limited_connection", _record)
    dest = tmp_path / "out.parquet"
    copy_arrow_parquet(pa.table({"addr": ["A1"], "n": [1]}), dest)
    assert dest.is_file()
    assert seen["memory_limit"] == _applied("memory_limit", "512MB")
    assert seen["max_temp_directory_size"] == _applied("max_temp_directory_size", "2GB")
    assert seen["threads"] == 1


def test_env_override_reaches_both_connections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DUCKDB_MEMORY_LIMIT", "512MB")
    monkeypatch.setenv("DUCKDB_MAX_TEMP_DIRECTORY_SIZE", "2GB")
    settings = Settings()
    assert settings.duckdb_memory_limit == "512MB"
    assert settings.duckdb_max_temp_directory_size == "2GB"
    _assert_both(tmp_path, "512MB", "2GB")


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0MB",
        "0",
        "-1",
        "none",
        "256",
        "100%",
        "256MB'; SET threads=4--",
        "1e3MB",
    ],
)
def test_invalid_duckdb_size_is_rejected_at_load(value: str) -> None:
    with pytest.raises(ValidationError, match="DUCKDB_MEMORY_LIMIT"):
        Settings(duckdb_memory_limit=value, _env_file=None)
    with pytest.raises(ValidationError, match="DUCKDB_MAX_TEMP_DIRECTORY_SIZE"):
        Settings(duckdb_max_temp_directory_size=value, _env_file=None)


@pytest.mark.parametrize("value", ["512MB", "1GB", "1GiB", "1.5 GB"])
def test_duckdb_size_examples_load(value: str) -> None:
    settings = Settings(
        duckdb_memory_limit=value,
        duckdb_max_temp_directory_size=value,
        _env_file=None,
    )
    assert settings.duckdb_memory_limit == value.strip()
    assert settings.duckdb_max_temp_directory_size == value.strip()


def test_duckdb_memory_limit_warns_when_two_connections_do_not_fit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from finance_context import settings as settings_mod

    settings_mod._duckdb_limit_warned = False
    caplog.set_level(logging.WARNING, logger="finance_context.settings")
    # 256MB is 256_000_000 bytes. Two connections need 512_000_000.
    Settings(
        duckdb_memory_limit="256MB",
        job_memory_budget_bytes=512_000_000,
        _env_file=None,
    )
    assert any(getattr(record, "event", None) == "duckdb_memory_limit" for record in caplog.records)
    assert "not clearly below" in caplog.text
    assert "compile reader" in caplog.text
    caplog.clear()
    Settings(
        duckdb_memory_limit="256MB",
        job_memory_budget_bytes=512_000_000,
        _env_file=None,
    )
    assert caplog.records == []

    settings_mod._duckdb_limit_warned = False
    Settings(
        duckdb_memory_limit="256MB",
        job_memory_budget_bytes=512_000_001,
        _env_file=None,
    )
    assert caplog.records == []
