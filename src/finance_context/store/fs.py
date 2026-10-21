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
    if os.environ.get("PROTO_JSON"):
        stream_json(path, data)
        return
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    atomic_write_bytes(path, payload.encode("utf-8"))


def stream_json(path: Path, data: Any, *, sort_keys: bool = False) -> None:
    """PROTOTYPE: json.dump writes iterencode chunks; no whole-document str/bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=sort_keys)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


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


RAW_CELL_TYPES = (
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
)


def write_columns_parquet(path: Path, columns, data) -> None:
    """PROTOTYPE: write column lists; each list is freed right after conversion."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    arrays = []
    for name, dtype in columns:
        arrays.append(pa.array(data[name], type=_ARROW_TYPES[dtype]))
        data[name] = None
    table = pa.Table.from_arrays(arrays, names=[name for name, _dtype in columns])
    del arrays
    pq.write_table(table, tmp)
    del table
    _fsync(tmp)
    tmp.replace(path)


def write_parquet_objects(path: Path, columns, objs, getters, batch: int | None = None) -> None:
    """PROTOTYPE: column at a time straight from objects; no row tuples.
    Rows go out in row groups of PROTO_PARQUET_BATCH rows so Arrow buffers stay bounded."""
    if batch is None:
        batch = int(os.environ.get("PROTO_PARQUET_BATCH", "0")) or (1 << 20)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    names = [name for name, _dtype in columns]
    schema = pa.schema([(name, _ARROW_TYPES[dtype]) for name, dtype in columns])
    n = len(objs)
    with pq.ParquetWriter(tmp, schema) as writer:
        for lo in range(0, max(n, 1), batch):
            part = objs[lo : lo + batch]
            arrays = [
                pa.array([get(obj) for obj in part], type=_ARROW_TYPES[dtype])
                for get, (_name, dtype) in zip(getters, columns, strict=True)
            ]
            writer.write_table(pa.Table.from_arrays(arrays, names=names))
            del arrays, part
    _fsync(tmp)
    tmp.replace(path)


class ObjectBatchWriter:
    """PROTOTYPE: one ParquetWriter, one row group per batch, schema fixed up front."""

    def __init__(self, path: Path, columns) -> None:
        self.path = path
        self.names = [name for name, _dtype in columns]
        self.types = [_ARROW_TYPES[dtype] for _name, dtype in columns]
        path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp = path.with_name(path.name + ".tmp")
        schema = pa.schema(list(zip(self.names, self.types, strict=True)))
        self._writer = pq.ParquetWriter(self.tmp, schema)

    def write_objects(self, objs, getters) -> None:
        if not objs:
            return
        arrays = [
            pa.array([get(obj) for obj in objs], type=typ)
            for get, typ in zip(getters, self.types, strict=True)
        ]
        self._writer.write_table(pa.Table.from_arrays(arrays, names=self.names))
        del arrays

    def write_lists(self, columns_data) -> None:
        arrays = [
            pa.array(values, type=typ)
            for values, typ in zip(columns_data, self.types, strict=True)
        ]
        self._writer.write_table(pa.Table.from_arrays(arrays, names=self.names))
        del arrays

    def close(self) -> None:
        self._writer.close()
        _fsync(self.tmp)
        self.tmp.replace(self.path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


class _RowView:
    __slots__ = ("_cols", "_i")

    def __init__(self, cols, i):
        self._cols = cols
        self._i = i

    def get(self, key, default=None):
        col = self._cols.get(key)
        if col is None:
            return default
        return col[self._i]

    def __getitem__(self, key):
        return self._cols[key][self._i]


class ColumnRows:
    """PROTOTYPE: list-of-dicts look-alike over per-column Python lists.
    Repeated strings (sheet, number_format) are deduplicated."""

    def __init__(self, path: Path, dedup=("sheet", "number_format")) -> None:
        table = pq.read_table(path)
        self._cols = {}
        for name in table.column_names:
            values = table.column(name).to_pylist()
            if name in dedup:
                pool: dict = {}
                values = [v if v is None else pool.setdefault(v, v) for v in values]
            self._cols[name] = values
        self._n = table.num_rows
        del table

    def __len__(self):
        return self._n

    def __getitem__(self, i):
        return _RowView(self._cols, i)

    def __iter__(self):
        cols = self._cols
        for i in range(self._n):
            yield _RowView(cols, i)
