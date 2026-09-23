from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import duckdb

_CELL_COLUMNS = (
    "sheet",
    "row",
    "col",
    "addr",
    "formula_raw",
    "cached_value",
    "hidden",
    "number_format",
    "comment",
)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    _fsync(tmp)
    tmp.replace(path)


def _fsync(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


@contextmanager
def file_lock(path: Path, *, blocking: bool = True) -> Iterator[bool]:
    """Exclusive flock on a sidecar. Non-blocking failure yields False."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    locked = False
    try:
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
            locked = True
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        if locked:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def update_json(path: Path, mutate: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Re-read JSON under a sidecar lock, then write the mutated document."""
    lock_path = path.with_name(path.name + ".lock")
    with file_lock(lock_path):
        current: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict):
                current = loaded
        write_json(path, mutate(current))


def write_json(path: Path, data: dict[str, Any]) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write_bytes(path, payload.encode("utf-8"))


def write_parquet(
    path: Path,
    columns: Sequence[tuple[str, str]],
    rows: Sequence[Sequence[Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    col_sql = ", ".join(f"{name} {dtype}" for name, dtype in columns)
    placeholders = ", ".join("?" for _ in columns)
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"CREATE TABLE cells ({col_sql})")
        if rows:
            con.executemany(f"INSERT INTO cells VALUES ({placeholders})", list(rows))
        dest = tmp.as_posix().replace("'", "''")
        con.execute(f"COPY cells TO '{dest}' (FORMAT PARQUET)")
    finally:
        con.close()
    _fsync(tmp)
    tmp.replace(path)


def read_parquet(path: Path) -> list[dict[str, Any]]:
    con = duckdb.connect(":memory:")
    try:
        quoted = path.as_posix().replace("'", "''")
        rel = con.execute(f"SELECT * FROM read_parquet('{quoted}')")
        cols = [d[0] for d in rel.description]
        return [dict(zip(cols, row, strict=True)) for row in rel.fetchall()]
    finally:
        con.close()


def write_cells_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    typed = [
        ("sheet", "VARCHAR"),
        ("row", "INTEGER"),
        ("col", "INTEGER"),
        ("addr", "VARCHAR"),
        ("formula_raw", "VARCHAR"),
        ("cached_value", "VARCHAR"),
        ("hidden", "BOOLEAN"),
        ("number_format", "VARCHAR"),
        ("comment", "VARCHAR"),
    ]
    write_parquet(
        path,
        typed,
        [tuple(row[col] for col in _CELL_COLUMNS) for row in rows],
    )
