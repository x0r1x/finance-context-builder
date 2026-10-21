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
    "style_indent",
    "outline_level",
    "col_outline_level",
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
    """Re-read JSON under a sidecar lock, then write the mutated document.

    Returning the same object leaves a document that was loaded from disk untouched.
    A no-op read-modify-write must not replace a write that landed after the read.
    """
    lock_path = path.with_name(path.name + ".lock")
    with file_lock(lock_path):
        current: dict[str, Any] = {}
        loaded_document = False
        if path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict):
                current = loaded
                loaded_document = True
        updated = mutate(current)
        if updated is current and loaded_document:
            return
        write_json(path, updated)


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


def read_parquet_columns(path: Path, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    """Read named columns only. The rest of the row stays on disk."""
    table = pq.read_table(path, columns=list(columns))
    arrays = [table.column(name).to_pylist() for name in columns]
    return list(zip(*arrays, strict=True))


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
        ("style_indent", "INTEGER"),
        ("outline_level", "INTEGER"),
        ("col_outline_level", "INTEGER"),
    ]
    write_parquet(
        path,
        typed,
        [tuple(row[col] for col in _CELL_COLUMNS) for row in rows],
    )
