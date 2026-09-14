from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb


class IrCatalog:
    def __init__(self, connection: duckdb.DuckDBPyConnection) -> None:
        self._con = connection

    @classmethod
    def open(cls, audit_dir: Path) -> IrCatalog:
        con = duckdb.connect(":memory:")
        cells = _sql_path(audit_dir / "ir" / "cells.parquet")
        edges = _sql_path(audit_dir / "ir" / "edges.parquet")
        con.execute(f"CREATE VIEW cells AS SELECT * FROM read_parquet('{cells}')")
        con.execute(f"CREATE VIEW edges AS SELECT * FROM read_parquet('{edges}')")
        return cls(con)

    def sql(self, query: str) -> Any:
        return self._con.execute(query)

    def upsert_layout(
        self,
        *,
        axis_headers: list[dict[str, Any]],
        layout_rows: list[dict[str, Any]],
    ) -> None:
        self._con.execute("DROP TABLE IF EXISTS axis_headers")
        self._con.execute("DROP TABLE IF EXISTS layout_rows")
        self._con.execute(
            """
            CREATE TABLE axis_headers (
                sheet VARCHAR,
                block_id VARCHAR,
                col INTEGER,
                role VARCHAR,
                header_text VARCHAR,
                period_key VARCHAR
            )
            """
        )
        self._con.execute(
            """
            CREATE TABLE layout_rows (
                sheet VARCHAR,
                block_id VARCHAR,
                row INTEGER,
                label VARCHAR,
                parent_row INTEGER,
                check_row BOOLEAN,
                label_col INTEGER,
                indent INTEGER
            )
            """
        )
        if axis_headers:
            self._con.executemany(
                "INSERT INTO axis_headers VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        h["sheet"],
                        h["block_id"],
                        h["col"],
                        h["role"],
                        h["header_text"],
                        h["period_key"],
                    )
                    for h in axis_headers
                ],
            )
        if layout_rows:
            self._con.executemany(
                "INSERT INTO layout_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r["sheet"],
                        r["block_id"],
                        r["row"],
                        r["label"],
                        r["parent_row"],
                        r["check_row"],
                        r["label_col"],
                        r["indent"],
                    )
                    for r in layout_rows
                ],
            )

    def upsert_series_outliers(self, rows: list[dict[str, Any]]) -> None:
        self._con.execute("DROP VIEW IF EXISTS series_outliers")
        self._con.execute("DROP TABLE IF EXISTS series_outlier_rows")
        self._con.execute(
            """
            CREATE TABLE series_outlier_rows (
                sheet VARCHAR,
                block_id VARCHAR,
                row INTEGER,
                col INTEGER,
                addr VARCHAR,
                cell_ref VARCHAR,
                kind VARCHAR,
                edge_period BOOLEAN,
                majority_template VARCHAR,
                cell_template VARCHAR
            )
            """
        )
        if rows:
            self._con.executemany(
                "INSERT INTO series_outlier_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        r["sheet"],
                        r["block_id"],
                        r["row"],
                        r["col"],
                        r["addr"],
                        r["cell_ref"],
                        r["kind"],
                        r["edge_period"],
                        r.get("majority_template"),
                        r.get("cell_template"),
                    )
                    for r in rows
                ],
            )
        self._con.execute(
            "CREATE VIEW series_outliers AS SELECT * FROM series_outlier_rows"
        )

    def close(self) -> None:
        self._con.close()


def _sql_path(path: Path) -> str:
    return path.as_posix().replace("'", "''")
