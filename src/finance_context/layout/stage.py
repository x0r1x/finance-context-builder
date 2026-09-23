from __future__ import annotations

import json
from pathlib import Path

from finance_context.layout.detect import detect_layout
from finance_context.layout.models import Layout
from finance_context.store.fs import read_parquet, write_json


def layout_workbook(
    dest_dir: Path,
    *,
    cells: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> Layout:
    if cells is None:
        cells = read_parquet(dest_dir / "ir" / "cells.parquet")
    if edges is None:
        edges = _read_edges(dest_dir)
    date1904 = False
    meta_path = dest_dir / "raw" / "workbook.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        date1904 = bool(meta.get("date1904"))
    layout = detect_layout(cells, date1904=date1904, edges=edges)
    write_json(dest_dir / "layout.json", layout.model_dump(mode="json"))
    return layout


def _read_edges(dest_dir: Path) -> list[dict]:
    path = dest_dir / "ir" / "edges.parquet"
    if not path.is_file():
        return []
    return read_parquet(path)
