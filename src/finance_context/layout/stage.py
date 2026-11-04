from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

from finance_context.excel.a1 import parse_addr
from finance_context.formulas.models import as_edge
from finance_context.layout.cells import LayoutCell
from finance_context.layout.detect import _sheet_layout, detect_layout
from finance_context.layout.models import Layout, SheetLayout
from finance_context.layout.relations import relation_period_cols
from finance_context.layout.timelines import link_workbook_timelines
from finance_context.observability import log_event
from finance_context.settings import Settings
from finance_context.store.duckdb_io import _iter_parquet_rows
from finance_context.store.fs import read_parquet, read_parquet_dicts, write_json

_PARSE_FACTS = ("style_indent", "outline_level", "col_outline_level")
_PARQUET_BATCH = 65536
# Columns detect reads. Comment and AST stay in the parquet file.
_LAYOUT_COLUMNS = (
    "sheet",
    "row",
    "col",
    "addr",
    "formula_raw",
    "formula_template",
    "cached_value",
    "hidden",
    "number_format",
)
_FLAG_COLUMNS = ("sheet", "row", "col", "cached_value", "number_format")
_RELATION_COLUMNS = (
    "sheet",
    "row",
    "col",
    "addr",
    "formula_raw",
    "formula_template",
    "ast_json",
)
_LOGGER = logging.getLogger("finance_context.pipeline")


def _timed_step(event: str, msg: str, fn):
    """Log one layout step with `duration_ms`, like pipeline `_stage`.

    Job id and the current stage are left for JsonFormatter to attach.
    """
    started = time.monotonic()
    try:
        return fn()
    finally:
        log_event(
            _LOGGER,
            logging.INFO,
            event,
            msg,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


def layout_workbook(
    dest_dir: Path,
    *,
    cells: list[dict] | None = None,
    edges: list[dict] | None = None,
    settings: Settings | None = None,
) -> Layout:
    chosen = settings if isinstance(settings, Settings) else Settings()
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
    defined_names = None if meta is None else meta.get("defined_names")
    if cells is None:
        names, groups, facts = _timed_step(
            "layout_cells_read",
            "layout cells read done",
            lambda: _load_layout_cells(dest_dir),
        )
        layout = _timed_step(
            "layout_detect",
            "detect_layout done",
            lambda: _detect_from_groups(
                dest_dir,
                names,
                groups,
                facts,
                date1904=date1904,
                edges=edges,
                hidden_sheets=hidden_sheets,
                hidden_columns=hidden_columns,
                hidden_rows=hidden_rows,
                summary_below=summary_below,
                defined_names=defined_names,
                meta=meta,
            ),
        )
    else:
        cells = _overlay_parse_facts(dest_dir, cells, columnar=chosen.layout_columnar_facts)
        cells = _overlay_merges(cells, meta)
        layout = _timed_step(
            "layout_detect",
            "detect_layout done",
            lambda: detect_layout(
                cells,
                date1904=date1904,
                edges=edges,
                hidden_sheets=hidden_sheets,
                hidden_columns=hidden_columns,
                hidden_rows=hidden_rows,
                summary_below=summary_below,
                defined_names=defined_names,
            ),
        )
    _timed_step(
        "layout_write",
        "layout.json write done",
        lambda: write_json(
            dest_dir / "layout" / "layout.json",
            layout.model_dump(mode="json"),
            stream=chosen.json_stream,
        ),
    )
    return layout


def _load_layout_cells(
    dest_dir: Path,
) -> tuple[list[str], _SheetSpill, dict[str, dict[tuple[int, int], dict[str, int]]]]:
    """One pass over IR cells and one pass over raw indent and outline facts."""
    names, sheets = _group_layout_cells(dest_dir / "ir" / "cells.parquet")
    try:
        facts = _group_raw_facts(dest_dir / "raw" / "cells.parquet")
    except Exception:
        sheets.close()
        raise
    return names, sheets, facts


def _load_layout_sheet_cells(dest_dir: Path) -> tuple[list[str], dict[str, list]]:
    """Cells ``_sheet_layout`` receives. The quality check hashes this."""
    meta = None
    meta_path = dest_dir / "raw" / "workbook.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    names, sheets, facts = _load_layout_cells(dest_dir)
    try:
        ready = {
            name: _overlay_merges(_apply_sheet_facts(sheets.pop(name), facts.get(name)), meta)
            for name in names
        }
    finally:
        sheets.close()
    return names, ready


def _detect_from_groups(
    dest_dir: Path,
    names: list[str],
    groups: _SheetSpill,
    facts: dict[str, dict[tuple[int, int], dict[str, int]]],
    *,
    date1904: bool,
    edges: list[dict] | None,
    hidden_sheets: set[str],
    hidden_columns: dict[str, set[int]],
    hidden_rows: dict[str, set[int]] | None,
    summary_below: dict[str, bool],
    defined_names: list | None,
    meta: dict | None,
) -> Layout:
    """Lay out one sheet, then drop its cells. Shared timelines read flag rows only."""
    prepared = None if edges is None else [as_edge(edge) for edge in edges]
    sheets = []
    try:
        for name in names:
            sheet_cells = _apply_sheet_facts(groups.pop(name), facts.pop(name, None))
            if not sheet_cells:
                continue
            sheet_cells = _overlay_merges(sheet_cells, meta)
            sheets.append(
                _sheet_layout(
                    name,
                    sheet_cells,
                    date1904=date1904,
                    edges=prepared,
                    hidden_sheets=hidden_sheets,
                    hidden_columns=hidden_columns,
                    hidden_rows=hidden_rows,
                    summary_below=summary_below,
                    defined_names=defined_names,
                )
            )
            del sheet_cells
        facts.clear()
        layout = Layout(sheets=sheets)
        link_workbook_timelines(layout, _read_flag_cells(dest_dir, layout), date1904)
        return layout
    finally:
        groups.close()


class _SheetSpill:
    """One parquet scan. Each batch is flushed to disk, one sheet loaded on ``pop``."""

    def __init__(self, anchor: Path | None = None) -> None:
        self.names: list[str] = []
        directory = None
        if anchor is not None:
            parent = anchor if anchor.is_dir() else anchor.parent
            if parent.is_dir():
                directory = parent
        # Beside the workbook, not on a memory-backed temp disk.
        self._dir = Path(tempfile.mkdtemp(prefix=".layout-sheets-", dir=directory))
        self._seen: set[str] = set()
        self._parts: dict[str, list[Path]] = {}
        self._seq = 0

    def write_batch(self, batch) -> None:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        sheet = batch.column("sheet")
        order: list[tuple[object, str]] = []
        known: set[object] = set()
        for value in sheet.to_pylist():
            if value is None or value in known:
                continue
            known.add(value)
            order.append((value, str(value)))
        for value, name in order:
            part = batch.filter(pc.equal(sheet, value))
            if part.num_rows == 0:
                continue
            if name not in self._seen:
                self._seen.add(name)
                self.names.append(name)
                self._parts[name] = []
            path = self._dir / f"{self._seq}.parquet"
            self._seq += 1
            pq.write_table(pa.Table.from_batches([part]), path)
            self._parts[name].append(path)

    def pop(self, name: str) -> list[LayoutCell]:
        paths = self._parts.pop(name, [])
        cells: list[LayoutCell] = []
        try:
            for path in paths:
                cells.extend(_layout_cells_from_parquet(path))
        finally:
            for path in paths:
                path.unlink(missing_ok=True)
        return cells

    def close(self) -> None:
        self._parts.clear()
        shutil.rmtree(self._dir, ignore_errors=True)


def _layout_cells_from_parquet(path: Path) -> list[LayoutCell]:
    import pyarrow.parquet as pq

    table = pq.read_table(path, columns=list(_LAYOUT_COLUMNS))
    columns = {name: table.column(name).to_pylist() for name in _LAYOUT_COLUMNS}
    count = table.num_rows
    del table
    return [
        LayoutCell(
            columns["sheet"][index],
            columns["row"][index],
            columns["col"][index],
            columns["addr"][index],
            columns["formula_raw"][index],
            columns["formula_template"][index],
            columns["cached_value"][index],
            columns["hidden"][index],
            columns["number_format"][index],
        )
        for index in range(count)
    ]


def _group_layout_cells(path: Path) -> tuple[list[str], _SheetSpill]:
    """Layout columns in one scan. Sheet order is first-seen file order.

    Cells of a sheet stay in file order when a later row group returns to it.
    Each sheet is a temp file until ``pop``. Comment and AST columns stay unread.
    """
    spill = _SheetSpill(path.parent)
    if not path.is_file():
        return [], spill
    import pyarrow.parquet as pq

    try:
        columns = list(_LAYOUT_COLUMNS)
        for batch in pq.ParquetFile(path).iter_batches(columns=columns, batch_size=_PARQUET_BATCH):
            spill.write_batch(batch)
    except Exception:
        spill.close()
        raise
    return spill.names, spill


def load_relation_cells(dest_dir: Path, layout: Layout) -> _RelationCells:
    """Period-column formula cells. Other cells, and their AST text, stay on disk.

    The scan is one pass. A row's strings live only until ``formula_relations``
    reduces that row. Value cells are not read: the pattern never looks at them.
    """
    return _RelationCells(dest_dir, layout)


class _RelationCells:
    """One ordered pass over the cells ``formula_relations`` looks up."""

    def __init__(self, dest_dir: Path, layout: Layout) -> None:
        self._dest = dest_dir
        self._layout = layout
        self.count = 0
        self._used = False

    def __iter__(self) -> Iterator[dict]:
        if self._used:
            return
        self._used = True
        path = self._dest / "ir" / "cells.parquet"
        if not path.is_file():
            return
        columns = list(_RELATION_COLUMNS)
        where = (
            "sheet = ? AND row IN (SELECT unnest(?::BIGINT[])) "
            "AND col IN (SELECT unnest(?::BIGINT[])) "
            "AND (formula_raw IS NOT NULL OR formula_template IS NOT NULL "
            "OR ast_json IS NOT NULL)"
        )
        for sheet in self._layout.sheets:
            rows, cols = _relation_targets(sheet)
            if not rows or not cols:
                continue
            params: list[object] = [sheet.name, sorted(rows), sorted(cols)]
            for names, batch in _iter_parquet_rows(path, columns, where=where, params=params):
                for record in batch:
                    self.count += 1
                    yield dict(zip(names, record, strict=True))
                del batch


def _relation_targets(sheet: SheetLayout) -> tuple[set[int], set[int]]:
    rows: set[int] = set()
    cols: set[int] = set()
    for block in sheet.blocks:
        picked = relation_period_cols(sheet, block)
        if not picked:
            continue
        cols.update(picked)
        for row in block.rows:
            rows.add(row.row)
    return rows, cols


def _sheet_names(path: Path) -> list[str]:
    """First-seen sheet order, the same order as a full cell list."""
    if not path.is_file():
        return []
    import pyarrow.parquet as pq

    seen: list[str] = []
    known: set[str] = set()
    for batch in pq.ParquetFile(path).iter_batches(columns=["sheet"], batch_size=_PARQUET_BATCH):
        for name in batch.column(0).to_pylist():
            if name in known:
                continue
            known.add(name)
            seen.append(str(name))
    return seen


def _read_layout_sheet(dest_dir: Path, sheet: str, meta: dict | None) -> list:
    path = dest_dir / "ir" / "cells.parquet"
    if not path.is_file():
        return []
    cells: list[LayoutCell] = []
    for names, batch in _iter_parquet_rows(
        path,
        list(_LAYOUT_COLUMNS),
        where="sheet = ?",
        params=[sheet],
    ):
        pos = {name: index for index, name in enumerate(names)}
        sheet_i = pos["sheet"]
        row_i = pos["row"]
        col_i = pos["col"]
        addr_i = pos["addr"]
        formula_i = pos["formula_raw"]
        template_i = pos["formula_template"]
        cached_i = pos["cached_value"]
        hidden_i = pos["hidden"]
        format_i = pos["number_format"]
        for record in batch:
            cells.append(
                LayoutCell(
                    record[sheet_i],
                    record[row_i],
                    record[col_i],
                    record[addr_i],
                    record[formula_i],
                    record[template_i],
                    record[cached_i],
                    record[hidden_i],
                    record[format_i],
                )
            )
    if not cells:
        return []
    cells = _overlay_sheet_facts(dest_dir, sheet, cells)
    return _overlay_merges(cells, meta)


def _with_updates(cell, updates: dict):
    """Apply overlay fields. A layout record is updated in place; a dict is copied."""
    if isinstance(cell, LayoutCell):
        for key, value in updates.items():
            cell[key] = value
        return cell
    copied = dict(cell)
    copied.update(updates)
    return copied


def _read_flag_cells(dest_dir: Path, layout: Layout) -> list[dict]:
    path = dest_dir / "ir" / "cells.parquet"
    found: list[dict] = []
    for sheet in layout.sheets:
        rows = {
            row.row
            for block in sheet.blocks
            if getattr(block, "kind", "timeline") == "timeline"
            for row in block.rows
            if row.kind == "flag"
        }
        found.extend(_rows_on_sheet(path, sheet.name, rows, _FLAG_COLUMNS))
    return found


def _rows_on_sheet(
    path: Path, sheet: str, rows: set[int], columns: tuple[str, ...]
) -> list[dict]:
    if not rows or not path.is_file():
        return []
    return read_parquet_dicts(
        path,
        list(columns),
        where="sheet = ? AND row IN (SELECT unnest(?::BIGINT[]))",
        params=[sheet, [int(row) for row in rows]],
    )


def _overlay_sheet_facts(dest_dir: Path, sheet: str, cells: list[dict]) -> list[dict]:
    """Copy indent and outline for this sheet. A missing column leaves cells unchanged."""
    path = dest_dir / "raw" / "cells.parquet"
    if not cells or not path.is_file():
        return cells
    import pyarrow.parquet as pq

    columns = ["row", "col", *_PARSE_FACTS]
    try:
        schema = pq.read_schema(path)
    except (OSError, ValueError):
        return cells
    if any(name not in schema.names for name in columns):
        return cells
    raw_rows = read_parquet_dicts(
        path,
        columns,
        where=(
            "sheet = ? AND ("
            "COALESCE(style_indent, 0) > 0 OR "
            "COALESCE(outline_level, 0) > 0 OR "
            "COALESCE(col_outline_level, 0) > 0)"
        ),
        params=[sheet],
    )
    return _apply_sheet_facts(cells, _facts_from_rows(raw_rows))


def _group_raw_facts(path: Path) -> dict[str, dict[tuple[int, int], dict[str, int]]]:
    """Positive indent and outline levels from one raw scan, keyed by sheet."""
    if not path.is_file():
        return {}
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    columns = ["sheet", "row", "col", *_PARSE_FACTS]
    try:
        schema = pq.read_schema(path)
    except (OSError, ValueError):
        return {}
    if any(name not in schema.names for name in columns):
        return {}
    table = pq.read_table(path, columns=columns)
    mask = None
    for key in _PARSE_FACTS:
        filled = pc.fill_null(table.column(key), 0)
        positive = pc.greater(filled, 0)
        mask = positive if mask is None else pc.or_kleene(mask, positive)
    matched = int(pc.sum(pc.fill_null(mask, False)).as_py() or 0)
    if matched == 0:
        del table
        return {}
    picked = table.filter(mask)
    del table
    sheets = picked.column("sheet").to_pylist()
    rows = picked.column("row").to_pylist()
    cols = picked.column("col").to_pylist()
    fact_cols = {key: picked.column(key).to_pylist() for key in _PARSE_FACTS}
    del picked
    grouped: dict[str, dict[tuple[int, int], dict[str, int]]] = {}
    for index, sheet in enumerate(sheets):
        raw = {"row": rows[index], "col": cols[index]}
        for key in _PARSE_FACTS:
            raw[key] = fact_cols[key][index]
        extra = _picked_facts(raw)
        if not extra:
            continue
        name = str(sheet)
        bucket = grouped.get(name)
        if bucket is None:
            bucket = {}
            grouped[name] = bucket
        bucket[(int(raw.get("row") or 0), int(raw.get("col") or 0))] = extra
    return grouped


def _facts_from_rows(raw_rows: list[dict]) -> dict[tuple[int, int], dict[str, int]] | None:
    facts: dict[tuple[int, int], dict[str, int]] = {}
    for raw in raw_rows:
        extra = _picked_facts(raw)
        if extra:
            facts[(int(raw.get("row") or 0), int(raw.get("col") or 0))] = extra
    return facts or None


def _picked_facts(raw: dict) -> dict[str, int]:
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
    return picked


def _apply_sheet_facts(
    cells: list[dict],
    facts: dict[tuple[int, int], dict[str, int]] | None,
) -> list[dict]:
    """Copy positive facts onto cells. No facts leaves the list as it is."""
    if not cells or not facts:
        return cells
    merged: list = []
    for cell in cells:
        extra = facts.get((int(cell.get("row") or 0), int(cell.get("col") or 0)))
        if not extra:
            merged.append(cell)
            continue
        merged.append(_with_updates(cell, extra))
    return merged


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
    updated: list = []
    for cell in cells:
        key = (str(cell.get("sheet")), int(cell.get("row") or 0), int(cell.get("col") or 0))
        end_col = spans.get(key)
        if end_col is None:
            updated.append(cell)
            continue
        updated.append(_with_updates(cell, {"merge_end_col": end_col}))
    return updated


def _overlay_parse_facts(
    dest_dir: Path, cells: list[dict], *, columnar: bool = False
) -> list[dict]:
    """Copy alignment indent and outline levels from the raw parse onto IR cells.

    Formula IR does not carry those columns. A missing raw file, or a parquet
    written before the columns existed, leaves the IR cells unchanged.
    """
    if columnar:
        return _timed_step(
            "layout_cells_read",
            "raw/cells.parquet read done",
            lambda: _overlay_facts_columnar(dest_dir, cells),
        )
    path = dest_dir / "raw" / "cells.parquet"
    if not cells or not path.is_file():
        return cells
    facts: dict[tuple[object, int, int], dict[str, int]] = {}
    raw_rows = _timed_step(
        "layout_cells_read",
        "raw/cells.parquet read done",
        lambda: read_parquet(path),
    )
    for raw in raw_rows:
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
    merged: list = []
    for cell in cells:
        extra = facts.get((cell.get("sheet"), int(cell.get("row") or 0), int(cell.get("col") or 0)))
        if not extra:
            merged.append(cell)
            continue
        merged.append(_with_updates(cell, extra))
    return merged


def _overlay_facts_columnar(dest_dir: Path, cells: list[dict]) -> list:
    """Scan fact columns in Arrow and skip the dict list when every value is zero."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    path = dest_dir / "raw" / "cells.parquet"
    if not cells or not path.is_file():
        return cells
    columns = ["sheet", "row", "col", *_PARSE_FACTS]
    try:
        schema = pq.read_schema(path)
    except (OSError, ValueError):
        return cells
    if any(name not in schema.names for name in columns):
        return cells
    table = pq.read_table(path, columns=columns)
    mask = None
    for key in _PARSE_FACTS:
        filled = pc.fill_null(table.column(key), 0)
        positive = pc.greater(filled, 0)
        mask = positive if mask is None else pc.or_kleene(mask, positive)
    if int(pc.sum(pc.fill_null(mask, False)).as_py() or 0) == 0:
        del table
        return cells
    picked = table.filter(mask)
    del table
    facts: dict[tuple[object, int, int], dict[str, int]] = {}
    sheets = picked.column("sheet").to_pylist()
    rows = picked.column("row").to_pylist()
    cols = picked.column("col").to_pylist()
    fact_cols = {key: picked.column(key).to_pylist() for key in _PARSE_FACTS}
    del picked
    for index, sheet in enumerate(sheets):
        extra: dict[str, int] = {}
        for key in _PARSE_FACTS:
            value = fact_cols[key][index]
            if not value:
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                extra[key] = number
        if extra:
            facts[(sheet, int(rows[index] or 0), int(cols[index] or 0))] = extra
    if not facts:
        return cells
    merged: list = []
    for cell in cells:
        extra = facts.get((cell.get("sheet"), int(cell.get("row") or 0), int(cell.get("col") or 0)))
        if not extra:
            merged.append(cell)
            continue
        merged.append(_with_updates(cell, extra))
    return merged


def _read_edges(dest_dir: Path) -> list[dict]:
    path = dest_dir / "ir" / "edges.parquet"
    if not path.is_file():
        return []
    return read_parquet(path)
