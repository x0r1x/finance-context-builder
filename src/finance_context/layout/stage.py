from __future__ import annotations

import json
from pathlib import Path

from finance_context.excel.a1 import parse_addr
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
    hidden_rows: dict[str, set[int]] | None = None
    summary_below: dict[str, bool] = {}
    meta: dict | None = None
    meta_path = dest_dir / "raw" / "workbook.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        date1904 = bool(meta.get("date1904"))
        hidden_rows = {}
        for sheet in meta.get("sheets") or []:
            if not isinstance(sheet, dict) or not sheet.get("name"):
                continue
            name = str(sheet["name"])
            if sheet.get("hidden"):
                hidden_sheets.add(name)
            cols = {int(col) for col in sheet.get("hidden_cols") or []}
            if cols:
                hidden_columns[name] = cols
            rows = {int(row) for row in sheet.get("hidden_rows") or []}
            if rows:
                hidden_rows[name] = rows
            if "summary_below" in sheet:
                summary_below[name] = bool(sheet.get("summary_below"))
    cells = _overlay_merges(cells, meta)
    layout = detect_layout(
        cells,
        date1904=date1904,
        edges=edges,
        hidden_sheets=hidden_sheets,
        hidden_columns=hidden_columns,
        hidden_rows=hidden_rows,
        summary_below=summary_below,
    )
    write_json(dest_dir / "layout.json", layout.model_dump(mode="json"))
    return layout


def _overlay_merges(cells: list[dict], meta: dict | None) -> list[dict]:
    """Copy merge spans onto anchor cells. The context schema does not store them."""
    if not meta:
        return cells
    spans: dict[tuple[str, int, int], int] = {}
    for sheet in meta.get("sheets") or []:
        if not isinstance(sheet, dict):
            continue
        name = str(sheet.get("name") or "")
        for ref in sheet.get("merges") or []:
            if not isinstance(ref, str) or ":" not in ref:
                continue
            start, end = ref.split(":", 1)
            try:
                col, row = parse_addr(start)
                end_col, _end_row = parse_addr(end)
            except ValueError:
                continue
            if end_col > col:
                spans[(name, row, col)] = end_col
    if not spans:
        return cells
    updated: list[dict] = []
    for cell in cells:
        key = (str(cell.get("sheet")), int(cell.get("row") or 0), int(cell.get("col") or 0))
        end_col = spans.get(key)
        if end_col is None:
            updated.append(cell)
            continue
        copied = dict(cell)
        copied["merge_end_col"] = end_col
        updated.append(copied)
    return updated


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
