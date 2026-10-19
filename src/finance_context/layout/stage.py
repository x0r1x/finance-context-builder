from __future__ import annotations

import json
from pathlib import Path

from finance_context.layout.detect import detect_layout
from finance_context.layout.models import Layout
from finance_context.store.fs import read_parquet, write_json

_PARSE_FACTS = ("style_indent", "outline_level", "col_outline_level")


def layout_workbook(
    dest_dir: Path,
    *,
    cells: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> Layout:
    if cells is None:
        cells = read_parquet(dest_dir / "ir" / "cells.parquet")
    cells = _overlay_parse_facts(dest_dir, cells)
    if edges is None:
        edges = _read_edges(dest_dir)
    date1904 = False
    hidden_sheets: set[str] = set()
    hidden_columns: dict[str, set[int]] = {}
    summary_below: dict[str, bool] = {}
    meta_path = dest_dir / "raw" / "workbook.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        date1904 = bool(meta.get("date1904"))
        for sheet in meta.get("sheets") or []:
            if not isinstance(sheet, dict) or not sheet.get("name"):
                continue
            name = str(sheet["name"])
            if sheet.get("hidden"):
                hidden_sheets.add(name)
            cols = {int(col) for col in sheet.get("hidden_cols") or []}
            if cols:
                hidden_columns[name] = cols
            if "summary_below" in sheet:
                summary_below[name] = bool(sheet.get("summary_below"))
    layout = detect_layout(
        cells,
        date1904=date1904,
        edges=edges,
        hidden_sheets=hidden_sheets,
        hidden_columns=hidden_columns,
        summary_below=summary_below,
    )
    write_json(dest_dir / "layout.json", layout.model_dump(mode="json"))
    return layout


def _overlay_parse_facts(dest_dir: Path, cells: list[dict]) -> list[dict]:
    """Copy alignment indent and outline levels from the raw parse onto IR cells.

    Formula IR does not carry those columns. A missing raw file, or a parquet
    written before the columns existed, leaves the IR cells unchanged.
    """
    path = dest_dir / "raw" / "cells.parquet"
    if not cells or not path.is_file():
        return cells
    facts: dict[tuple[object, int, int], dict[str, int]] = {}
    for raw in read_parquet(path):
        picked: dict[str, int] = {}
        for key in _PARSE_FACTS:
            value = raw.get(key)
            if not value:
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                picked[key] = number
        if picked:
            facts[(raw.get("sheet"), int(raw.get("row") or 0), int(raw.get("col") or 0))] = picked
    if not facts:
        return cells
    merged: list[dict] = []
    for cell in cells:
        extra = facts.get((cell.get("sheet"), int(cell.get("row") or 0), int(cell.get("col") or 0)))
        if not extra:
            merged.append(cell)
            continue
        updated = dict(cell)
        updated.update(extra)
        merged.append(updated)
    return merged


def _read_edges(dest_dir: Path) -> list[dict]:
    path = dest_dir / "ir" / "edges.parquet"
    if not path.is_file():
        return []
    return read_parquet(path)
