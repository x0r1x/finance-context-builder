from __future__ import annotations

from pathlib import Path

from finance_context.excel.models import WorkbookMeta
from finance_context.excel.ooxml import parse_ooxml, parse_ooxml_columns
from finance_context.excel.zip_guard import open_xlsx_zip
from finance_context.settings import Settings
from finance_context.store.fs import (
    RAW_CELL_TYPES,
    write_cells_parquet,
    write_columns_parquet,
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
        try:
            columns, meta, presence = parse_ooxml_columns(zf)
        finally:
            zf.close()
        write_columns_parquet(dest_dir / "raw" / "cells.parquet", RAW_CELL_TYPES, columns)
        del columns
        sheet, addr, kind = presence
        write_columns_parquet(
            dest_dir / "raw" / "cell_presence.parquet",
            PRESENCE_COLUMNS,
            {"sheet": sheet, "addr": addr, "presence": kind},
        )
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
