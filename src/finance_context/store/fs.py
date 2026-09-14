from __future__ import annotations

import json
from collections.abc import Sequence
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
    tmp.replace(path)


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
