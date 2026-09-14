from __future__ import annotations

from pathlib import Path

from finance_context.excel.models import WorkbookMeta
from finance_context.excel.ooxml import parse_ooxml
from finance_context.excel.zip_guard import open_xlsx_zip
from finance_context.store.fs import write_cells_parquet, write_json


def parse_workbook(source: Path, dest_dir: Path) -> WorkbookMeta:
    zf = open_xlsx_zip(source)
    try:
        cells, meta = parse_ooxml(zf)
    finally:
        zf.close()
    write_cells_parquet(
        dest_dir / "raw" / "cells.parquet",
        [cell.model_dump() for cell in cells],
    )
    write_json(dest_dir / "raw" / "workbook.json", meta.model_dump(mode="json"))
    return meta
