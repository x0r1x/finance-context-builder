"""DuckDB spill stays inside a 4 GiB pod: 256MB, one thread, at most 2 GiB each.

The free-space raise is shared across the configured concurrent jobs. One job
on a large disk may still reach 2 GiB. Four jobs on a 4 GiB volume stay at the floor.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

from finance_context.store import duckdb_io
from finance_context.store.duckdb_io import (
    _spill_limit_bytes,
    _spill_limit_setting,
    limited_connection,
)

_GIB = 1 << 30
_FIRST_EDGE_SQL = """
    SELECT evidence FROM (
        SELECT *, row_number() OVER (
            PARTITION BY source, target, kind ORDER BY seq
        ) AS rn
        FROM read_parquet(?)
    )
    WHERE rn = 1
    ORDER BY seq
"""


def _parse_size(value: object) -> float:
    number, unit = str(value).split()
    scale = {"b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3}
    return float(number) * scale[unit.lower()]


@pytest.mark.parametrize(
    ("free", "slots", "expected"),
    [
        (100 * _GIB, 4, 2 * _GIB),
        (4 * _GIB, 4, _GIB),
        (4 * _GIB, 1, 2 * _GIB),
        (int(2.5 * _GIB), 4, _GIB),
        (int(2.5 * _GIB), 1, int(1.5 * _GIB)),
        (int(1.5 * _GIB), 4, _GIB),
        (_GIB // 2, 4, _GIB),
    ],
)
def test_spill_cap_fits_a_four_gigabyte_pod(
    tmp_path: Path, monkeypatch, free: int, slots: int, expected: int
):
    monkeypatch.setattr(duckdb_io.shutil, "disk_usage", lambda _path: SimpleNamespace(free=free))
    assert _spill_limit_bytes(tmp_path, _GIB, slots) == expected


def test_spill_share_follows_the_configured_job_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(
        duckdb_io.shutil, "disk_usage", lambda _path: SimpleNamespace(free=4 * _GIB)
    )
    monkeypatch.setattr(duckdb_io, "_spill_job_slots", lambda: 4)
    assert _spill_limit_bytes(tmp_path, _GIB) == _GIB
    monkeypatch.setattr(duckdb_io, "_spill_job_slots", lambda: 1)
    assert _spill_limit_bytes(tmp_path, _GIB) == 2 * _GIB


def test_spill_cap_stays_one_gibibyte_when_the_volume_cannot_be_read(tmp_path, monkeypatch):
    def offline(_path: Path) -> SimpleNamespace:
        raise OSError("offline")

    monkeypatch.setattr(duckdb_io.shutil, "disk_usage", offline)
    assert _spill_limit_bytes(tmp_path, _GIB) == _GIB
    assert _spill_limit_setting(tmp_path, "1GB", _GIB) == "1GB"


def test_rounded_spill_under_the_floor_keeps_the_setting(tmp_path, monkeypatch):
    floor = 1_000_000_000
    monkeypatch.setattr(
        duckdb_io.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(free=floor + _GIB + 1),
    )
    assert _spill_limit_bytes(tmp_path, floor, slots=1) > floor
    assert _spill_limit_setting(tmp_path, "1GB", floor, slots=1) == "1GB"


def test_two_gibibyte_cap_is_reported_as_gigabytes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        duckdb_io.shutil, "disk_usage", lambda _path: SimpleNamespace(free=100 * _GIB)
    )
    assert _spill_limit_setting(tmp_path, "1GB", _GIB) == "2GiB"


def test_scan_uses_one_thread_and_does_not_take_the_pod(tmp_path, monkeypatch):
    monkeypatch.setattr(
        duckdb_io.shutil, "disk_usage", lambda _path: SimpleNamespace(free=100 * _GIB)
    )
    anchor = tmp_path / "ir" / "cells.parquet"
    anchor.parent.mkdir()
    anchor.write_bytes(b"")
    with limited_connection(anchor) as con:
        threads = con.execute("SELECT current_setting('threads')").fetchone()[0]
        memory = _parse_size(con.execute("SELECT current_setting('memory_limit')").fetchone()[0])
        temp_cap = _parse_size(
            con.execute("SELECT current_setting('max_temp_directory_size')").fetchone()[0]
        )
        temp_dir = Path(str(con.execute("SELECT current_setting('temp_directory')").fetchone()[0]))
        held = temp_dir
        assert held.is_dir()
    assert threads == 1
    assert memory < 300 * 1024 * 1024
    assert _GIB < temp_cap < 3 * _GIB
    assert held.parent == anchor.parent
    assert not held.exists()


def test_full_temp_directory_is_reported_as_out_of_memory(tmp_path, monkeypatch):
    """A sort dies when the buffer is 48MB and the spill cap cannot take it.

    Production scans stay at 256MB. DuckDB may name the temp cap or the
    allocation; either way the scan does not finish.
    """
    path = tmp_path / "edges.parquet"
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
          SELECT
            'S' || (i // 4)::VARCHAR AS source,
            'T' || (i // 4)::VARCHAR AS target,
            'ref' AS kind,
            false AS unresolved,
            false AS truncated,
            false AS dangling,
            NULL::BIGINT AS col_offset,
            NULL::VARCHAR AS period_lag,
            NULL::VARCHAR AS dangling_reason,
            NULL::VARCHAR AS status,
            NULL::VARCHAR AS reason,
            repeat('z', 80) || i::VARCHAR AS evidence,
            NULL::VARCHAR AS range_ref,
            NULL::BOOLEAN AS abs_col,
            NULL::BOOLEAN AS abs_row,
            NULL::BOOLEAN AS abs_col_end,
            NULL::BOOLEAN AS abs_row_end,
            false AS named,
            i::BIGINT AS seq
          FROM range(400000) t(i)
        ) TO '{path.as_posix()}' (FORMAT parquet, COMPRESSION snappy)
        """
    )
    con.close()
    real = duckdb_io._set_duckdb_limits

    def tight(connection, temp_directory, *, anchor=None):
        real(connection, temp_directory, anchor=anchor)
        connection.execute("SET memory_limit='48MB'")
        connection.execute("SET max_temp_directory_size='256KB'")

    monkeypatch.setattr(duckdb_io, "_set_duckdb_limits", tight)

    with pytest.raises(duckdb.OutOfMemoryException):
        with limited_connection(path) as con:
            con.execute(_FIRST_EDGE_SQL, [str(path)]).fetchall()


def test_raised_cap_keeps_the_first_of_each_edge(tmp_path):
    path = tmp_path / "edges.parquet"
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (
          SELECT
            'S' || (i // 4)::VARCHAR AS source,
            'T' || (i // 4)::VARCHAR AS target,
            'ref' AS kind,
            false AS unresolved,
            false AS truncated,
            false AS dangling,
            NULL::BIGINT AS col_offset,
            NULL::VARCHAR AS period_lag,
            NULL::VARCHAR AS dangling_reason,
            NULL::VARCHAR AS status,
            NULL::VARCHAR AS reason,
            'e' || i::VARCHAR AS evidence,
            NULL::VARCHAR AS range_ref,
            NULL::BOOLEAN AS abs_col,
            NULL::BOOLEAN AS abs_row,
            NULL::BOOLEAN AS abs_col_end,
            NULL::BOOLEAN AS abs_row_end,
            false AS named,
            i::BIGINT AS seq
          FROM range(20000) t(i)
        ) TO '{path.as_posix()}' (FORMAT parquet, COMPRESSION snappy)
        """
    )
    con.close()

    with limited_connection(path) as con:
        rows = con.execute(_FIRST_EDGE_SQL, [str(path)]).fetchall()
    assert len(rows) == 5000
    assert rows[0][0] == "e0"
    assert rows[-1][0] == "e19996"
    assert all(int(row[0][1:]) % 4 == 0 for row in rows)
