from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from finance_context.store.duckdb_io import (
    copy_arrow_parquet,
    read_parquet_column_tuples,
    read_parquet_rows,
    read_parquet_table,
)
from finance_context.store.duckdb_io import read_parquet_dicts as _read_parquet_dicts

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


def temporary_path(path: Path) -> Path:
    """Unique temp file in ``path``'s directory. The caller deletes it on failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    os.close(fd)
    return Path(name)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Publish ``data`` as ``path``. Concurrent writers use distinct temp names."""
    path.parent.mkdir(parents=True, exist_ok=True)
    src = temporary_path(path)
    try:
        src.write_bytes(data)
        atomic_replace(src, path)
    except BaseException:
        src.unlink(missing_ok=True)
        raise
    src.unlink(missing_ok=True)


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
    src = temporary_path(path)
    try:
        with src.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=sort_keys)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(src, path)
    except BaseException:
        src.unlink(missing_ok=True)
        raise
    src.unlink(missing_ok=True)


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
    tmp = temporary_path(path)
    tmp.unlink()
    try:
        copy_arrow_parquet(table, tmp)
        atomic_replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def read_parquet(path: Path) -> list[dict[str, Any]]:
    return read_parquet_rows(path)


def read_parquet_columns(path: Path, columns: Sequence[str]) -> list[tuple[Any, ...]]:
    """Read named columns only. The rest of the row stays on disk."""
    return read_parquet_column_tuples(path, list(columns))


def read_parquet_dicts(
    path: Path,
    columns: Sequence[str],
    *,
    where: str | None = None,
    params: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Read named columns as dicts. Other columns stay on disk."""
    return _read_parquet_dicts(path, list(columns), where=where, params=params)


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


class RowGroupWriter:
    """One DuckDB parquet file per requested row group.

    DuckDB 2.0 flushes a parquet file on 2048-row vectors, so one ``COPY`` cannot
    honor ``parquet_row_group_rows``. A file that stays in one group is one DuckDB
    ``COPY``. Each requested group is its own DuckDB file. Several files are
    appended with PyArrow so each part stays one row group.
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
        self._parts: list[Path] = []
        self._streaming = False
        self._closed = False

    def write_lists(self, columns_data: Sequence[Sequence[Any]]) -> None:
        if self._closed:
            raise RuntimeError("parquet writer is closed")
        if not columns_data or len(columns_data[0]) == 0:
            return
        count = len(columns_data[0])
        if not self._streaming and (
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
        try:
            self._finish()
        except BaseException:
            self._drop_parts()
            raise

    def _finish(self) -> None:
        if not self._streaming:
            merged: list[list[Any]] = [[] for _ in self.names]
            for chunk in self._chunks:
                for index, values in enumerate(chunk):
                    merged[index].extend(values)
            self._chunks.clear()
            arrays = [
                pa.array(values, type=typ) for values, typ in zip(merged, self.types, strict=True)
            ]
            _write_table_file(self.path, pa.Table.from_arrays(arrays, names=self.names))
            return
        if not self._parts:
            empty = [pa.array([], type=typ) for typ in self.types]
            _write_table_file(self.path, pa.Table.from_arrays(empty, names=self.names))
            return
        if len(self._parts) == 1:
            atomic_replace(self._parts[0], self.path)
            self._parts.clear()
            return
        self._stitch()

    def begin_row_groups(self) -> None:
        """Further ``write_lists`` calls are each one row group."""
        self._start_groups()

    def _start_groups(self) -> None:
        if self._streaming:
            return
        self._streaming = True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self._chunks
        self._chunks = []
        self._buffered = 0
        for chunk in pending:
            self._write_group(chunk)

    def _write_group(self, columns_data: Sequence[Sequence[Any]]) -> None:
        arrays = [
            pa.array(values, type=typ) for values, typ in zip(columns_data, self.types, strict=True)
        ]
        table = pa.Table.from_arrays(arrays, names=self.names)
        part = temporary_path(self.path)
        part.unlink()
        try:
            copy_arrow_parquet(table, part)
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        self._parts.append(part)

    def _stitch(self) -> None:
        tmp = temporary_path(self.path)
        schema = pa.schema(list(zip(self.names, self.types, strict=True)))
        try:
            with pq.ParquetWriter(tmp, schema, compression="snappy") as writer:
                for part in self._parts:
                    writer.write_table(read_parquet_table(part))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        try:
            atomic_replace(tmp, self.path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        self._drop_parts()

    def __enter__(self) -> RowGroupWriter:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        if exc_type is None:
            self.close()
        else:
            self._abort_tmp()
        return False

    def _abort_tmp(self) -> None:
        self._closed = True
        self._chunks.clear()
        self._drop_parts()

    def _drop_parts(self) -> None:
        parts = self._parts
        self._parts = []
        for part in parts:
            part.unlink(missing_ok=True)


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
        table = read_parquet_table(path)
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
