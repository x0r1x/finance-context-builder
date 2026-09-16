from __future__ import annotations

import json
from pathlib import Path

from finance_context.ir.catalog import IrCatalog
from finance_context.layout.detect import detect_layout
from finance_context.layout.models import Layout
from finance_context.store.fs import read_parquet, write_json


def layout_workbook(dest_dir: Path, catalog: IrCatalog | None = None) -> Layout:
    cells = read_parquet(dest_dir / "ir" / "cells.parquet")
    date1904 = False
    meta_path = dest_dir / "raw" / "workbook.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        date1904 = bool(meta.get("date1904"))
    layout = detect_layout(cells, date1904=date1904)
    write_json(dest_dir / "layout.json", layout.model_dump(mode="json"))
    if catalog is not None:
        register_layout(layout, catalog)
    return layout


def register_layout(layout: Layout, catalog: IrCatalog) -> None:
    catalog.upsert_layout(
        axis_headers=_axis_rows(layout),
        layout_rows=_row_rows(layout),
    )


def _axis_rows(layout: Layout) -> list[dict]:
    out: list[dict] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            for header in block.axis.headers:
                out.append(
                    {
                        "sheet": sheet.name,
                        "block_id": block.block_id,
                        "col": header.col,
                        "role": header.role,
                        "header_text": header.text,
                        "period_key": header.period_key,
                    }
                )
    return out


def _row_rows(layout: Layout) -> list[dict]:
    out: list[dict] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            for row in block.rows:
                out.append(
                    {
                        "sheet": sheet.name,
                        "block_id": block.block_id,
                        "row": row.row,
                        "label": row.label,
                        "parent_row": row.parent_row,
                        "check_row": row.check_row,
                        "label_col": (
                            row.label_col if row.label_col is not None else block.label_col
                        ),
                        "indent": row.indent,
                    }
                )
    return out
