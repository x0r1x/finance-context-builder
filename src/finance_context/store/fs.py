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
    atomic_replace(tmp, path)


def atomic_replace(tmp: Path, path: Path) -> None:
    """Publish ``tmp`` as ``path``.

    The bytes are fsynced, then ``os.replace`` swaps the name in the same
    directory, then the directory is fsynced. A reader of ``path`` sees the
    previous file or the complete new one.
    """
    _fsync(tmp)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _fsync(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_dir(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    fd = os.open(directory, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


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


def update_json(
    path: Path,
    mutate: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    stream: bool | None = None,
) -> None:
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
        write_json(path, updated, stream=stream)


def write_json(
    path: Path,
    data: dict[str, Any],
    *,
    sort_keys: bool = False,
    stream: bool | None = None,
) -> None:
    if stream is None:
        from finance_context.settings import Settings

        stream = Settings().json_stream
    if stream:
        stream_json(path, data, sort_keys=sort_keys)
        return
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=sort_keys) + "\n"
    atomic_write_bytes(path, payload.encode("utf-8"))


def stream_json(path: Path, data: Any, *, sort_keys: bool = False) -> None:
    """json.dump writes the document in chunks. No whole-document str or bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=sort_keys)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    atomic_replace(tmp, path)


_ARROW_TYPES = {
    "VARCHAR": pa.string(),
    "INTEGER": pa.int64(),
    "BOOLEAN": pa.bool_(),
}


def write_parquet(
    path: Path,
    columns: Sequence[tuple[str, str]],
    rows: Sequence[Sequence[Any]],
    *,
    row_group_rows: int | None = None,
) -> None:
    if row_group_rows is None:
        from finance_context.settings import Settings

        row_group_rows = Settings().parquet_row_group_rows
    materialized = list(rows)
    if row_group_rows <= 0 or len(materialized) <= row_group_rows:
        _write_row_table(path, columns, materialized)
        return
    width = len(columns)
    with RowGroupWriter(path, columns, row_group_rows) as writer:
        for start in range(0, len(materialized), row_group_rows):
            part = materialized[start : start + row_group_rows]
            writer.write_lists([[row[index] for row in part] for index in range(width)])


def _write_row_table(
    path: Path,
    columns: Sequence[tuple[str, str]],
    rows: Sequence[Sequence[Any]],
) -> None:
    arrays = [
        pa.array([row[index] for row in rows], type=_ARROW_TYPES[dtype])
        for index, (_name, dtype) in enumerate(columns)
    ]
    table = pa.Table.from_arrays(arrays, names=[name for name, _dtype in columns])
    _write_table_file(path, table)


def _write_table_file(path: Path, table: pa.Table) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp)
    atomic_replace(tmp, path)


def read_parquet(path: Path) -> list[dict[str, Any]]:
    return pq.read_table(path).to_pylist()


def read_parquet_columns(path: Path, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    """Read named columns only. The rest of the row stays on disk."""
    table = pq.read_table(path, columns=list(columns))
    arrays = [table.column(name).to_pylist() for name in columns]
    return list(zip(*arrays, strict=True))


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


def write_cells_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    # Raw cells stay one row group so the file matches the streaming writer.
    write_parquet(
        path,
        RAW_CELL_TYPES,
        [tuple(row[col] for col in _CELL_COLUMNS) for row in rows],
        row_group_rows=0,
    )


def write_columns_parquet(
    path: Path,
    columns: Sequence[tuple[str, str]],
    data: dict[str, list[Any] | None],
) -> None:
    """One row group from column lists. Each list is dropped after Arrow copies it."""
    arrays = []
    for name, dtype in columns:
        arrays.append(pa.array(data[name], type=_ARROW_TYPES[dtype]))
        data[name] = None
    table = pa.Table.from_arrays(arrays, names=[name for name, _dtype in columns])
    del arrays
    _write_table_file(path, table)


class RowGroupWriter:
    """ParquetWriter row groups. A table that fits in one group uses write_table.

    ``write_table`` bytes match the historical single-group files. Bytes differ
    only when a row group is smaller than the table.
    """

    def __init__(
        self,
        path: Path,
        columns: Sequence[tuple[str, str]],
        row_group_rows: int,
    ) -> None:
        self.path = path
        self.names = [name for name, _dtype in columns]
        self.types = [_ARROW_TYPES[dtype] for _name, dtype in columns]
        self.row_group_rows = row_group_rows
        self._buffered = 0
        self._chunks: list[list[list[Any]]] = []
        self._writer: pq.ParquetWriter | None = None
        self._tmp: Path | None = None
        self._closed = False

    def write_lists(self, columns_data: Sequence[Sequence[Any]]) -> None:
        if self._closed:
            raise RuntimeError("parquet writer is closed")
        if not columns_data or len(columns_data[0]) == 0:
            return
        count = len(columns_data[0])
        if self._writer is None and (
            self.row_group_rows <= 0 or self._buffered + count <= self.row_group_rows
        ):
            self._chunks.append([list(column) for column in columns_data])
            self._buffered += count
            return
        self._start_groups()
        self._write_group(columns_data)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._writer is not None:
            self._writer.close()
            assert self._tmp is not None
            atomic_replace(self._tmp, self.path)
            return
        merged: list[list[Any]] = [[] for _ in self.names]
        for chunk in self._chunks:
            for index, values in enumerate(chunk):
                merged[index].extend(values)
        self._chunks.clear()
        arrays = [
            pa.array(values, type=typ) for values, typ in zip(merged, self.types, strict=True)
        ]
        _write_table_file(self.path, pa.Table.from_arrays(arrays, names=self.names))

    def begin_row_groups(self) -> None:
        """Start ParquetWriter groups. Further write_lists calls are not buffered."""
        self._start_groups()

    def _start_groups(self) -> None:
        if self._writer is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tmp = self.path.with_name(self.path.name + ".tmp")
        schema = pa.schema(list(zip(self.names, self.types, strict=True)))
        self._writer = pq.ParquetWriter(self._tmp, schema)
        pending = self._chunks
        self._chunks = []
        for chunk in pending:
            self._write_group(chunk)

    def _write_group(self, columns_data: Sequence[Sequence[Any]]) -> None:
        assert self._writer is not None
        arrays = [
            pa.array(values, type=typ)
            for values, typ in zip(columns_data, self.types, strict=True)
        ]
        self._writer.write_table(pa.Table.from_arrays(arrays, names=self.names))

    def __enter__(self) -> RowGroupWriter:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if exc_type is None:
            self.close()
        elif self._tmp is not None and self._tmp.is_file() and not self.path.is_file():
            self._tmp.unlink(missing_ok=True)
        return False


class _RowView:
    __slots__ = ("_cols", "_i")

    def __init__(self, cols: dict[str, list[Any]], index: int) -> None:
        self._cols = cols
        self._i = index

    def get(self, key: str, default: object = None) -> object:
        column = self._cols.get(key)
        if column is None:
            return default
        return column[self._i]

    def __getitem__(self, key: str) -> object:
        return self._cols[key][self._i]


class ColumnRows:
    """Row views over per-column lists. Repeated strings can share one object."""

    def __init__(self, path: Path, dedup: tuple[str, ...] = ("sheet", "number_format")) -> None:
        table = pq.read_table(path)
        self._cols: dict[str, list[Any]] = {}
        for name in table.column_names:
            values = table.column(name).to_pylist()
            if name in dedup:
                pool: dict[Any, Any] = {}
                values = [
                    value if value is None else pool.setdefault(value, value) for value in values
                ]
            self._cols[name] = values
        self._n = table.num_rows
        del table

    def __len__(self) -> int:
        return self._n

    def __getitem__(self, index: int) -> _RowView:
        return _RowView(self._cols, index)

    def __iter__(self) -> Iterator[_RowView]:
        cols = self._cols
        for index in range(self._n):
            yield _RowView(cols, index)
