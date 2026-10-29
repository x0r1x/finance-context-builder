from __future__ import annotations

from pathlib import Path

from finance_context.excel.models import WorkbookMeta
from finance_context.excel.ooxml import parse_ooxml, parse_ooxml_columns
from finance_context.excel.zip_guard import open_xlsx_zip
from finance_context.settings import Settings
from finance_context.store.duckdb_io import DuckDBBatch
from finance_context.store.fs import (
    RAW_CELL_TYPES,
    write_cells_parquet,
    write_json,
    write_parquet,
)

PRESENCE_COLUMNS = (
    ("sheet", "VARCHAR"),
    ("addr", "VARCHAR"),
    ("presence", "VARCHAR"),
)


def parse_workbook(
    source: Path, dest_dir: Path, settings: Settings | None = None
) -> WorkbookMeta:
    chosen = settings if isinstance(settings, Settings) else Settings()
    zf = open_xlsx_zip(source, streaming=chosen.parse_streaming)
    if chosen.parse_streaming:
        cells_path = dest_dir / "raw" / "cells.parquet"
        presence_path = dest_dir / "raw" / "cell_presence.parquet"
        cells = DuckDBBatch(Path(str(cells_path) + ".build.duckdb"), RAW_CELL_TYPES)
        presence = DuckDBBatch(Path(str(presence_path) + ".build.duckdb"), PRESENCE_COLUMNS)
        try:
            try:
                meta = parse_ooxml_columns(zf, cells, presence)
            finally:
                zf.close()
            # One row group is held whole in DuckDB memory (256MB) while it is
            # written, so a large book is split like the IR parquet.
            cells.finish(cells_path, chosen.parquet_row_group_rows)
            presence.finish(presence_path, chosen.parquet_row_group_rows)
        except Exception:
            cells.abort()
            presence.abort()
            raise
        write_json(
            dest_dir / "raw" / "workbook.json",
            meta.model_dump(mode="json"),
            stream=chosen.json_stream,
        )
        return meta
    try:
        cells, meta, presence_rows = parse_ooxml(zf)
    finally:
        zf.close()
    write_cells_parquet(
        dest_dir / "raw" / "cells.parquet",
        [cell.model_dump() for cell in cells],
    )
    write_parquet(
        dest_dir / "raw" / "cell_presence.parquet",
        PRESENCE_COLUMNS,
        [(row.sheet, row.addr, row.presence) for row in presence_rows],
        row_group_rows=0,
    )
    write_json(
        dest_dir / "raw" / "workbook.json",
        meta.model_dump(mode="json"),
        stream=chosen.json_stream,
    )
    return meta
