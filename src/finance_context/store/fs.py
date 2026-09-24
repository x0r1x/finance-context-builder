from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

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
    "shared_si",
    "shared_master",
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


_ARROW_TYPES = {
    "VARCHAR": pa.string(),
    "INTEGER": pa.int64(),
    "BOOLEAN": pa.bool_(),
}


def write_parquet(
    path: Path,
    columns: Sequence[tuple[str, str]],
    rows: Sequence[Sequence[Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    materialized = list(rows)
    arrays = [
        pa.array([row[index] for row in materialized], type=_ARROW_TYPES[dtype])
        for index, (_name, dtype) in enumerate(columns)
    ]
    table = pa.Table.from_arrays(arrays, names=[name for name, _dtype in columns])
    pq.write_table(table, tmp)
    _fsync(tmp)
    tmp.replace(path)


def read_parquet(path: Path) -> list[dict[str, Any]]:
    return pq.read_table(path).to_pylist()


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
        ("shared_si", "INTEGER"),
        ("shared_master", "BOOLEAN"),
    ]
    write_parquet(
        path,
        typed,
        [tuple(row[col] for col in _CELL_COLUMNS) for row in rows],
    )
