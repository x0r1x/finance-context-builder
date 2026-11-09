from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path

from finance_context.context.assemble import (
    _SERIES_KINDS,
    _relations_for_block,
    _resolved_hints,
    _row_for_layout,
)
from finance_context.context.series import (
    format_number,
    normalize_series,
    scale_factor_for,
    value_status,
)
from finance_context.context.timeline import (
    _period_warning_value,
    build_axes,
)
from finance_context.excel.a1 import format_addr, index_to_col
from finance_context.formulas.csr import SheetUsage
from finance_context.formulas.edge_file import iterate_edges
from finance_context.layout.adjacency import RangeReads, row_adjacency
from finance_context.layout.cells import parse_number
from finance_context.layout.models import AxisHeader, Layout, LayoutRow, is_series_column
from finance_context.layout.params import selector_rows
from finance_context.layout.periods import (
    classify_header,
    display_cell_text,
    is_calendar_hit,
    period_like_header,
    unparsed_period_shape,
)
from finance_context.layout.relations import RowRelation
from finance_context.layout.resolve import axes_for
from finance_context.layout.units import is_block_unit_caption
from finance_context.models.context import (
    BLOCK_ROW_ADAPTER,
    SCHEMA_VERSION,
    ArtifactMeta,
    ContextAxis,
    ContextDocument,
    ContextRange,
    FinancialBlock,
    GraphPointer,
    NumericSummary,
    RoleCell,
    RowSeries,
    WorkbookRaw,
)
from finance_context.render.markdown import (
    AxisRows,
    MarkdownBody,
    _block_intro,
    _markdown_tables,
    _relations_section,
)
from finance_context.store.fs import JsonItems, _iter_json, atomic_replace, temporary_path
from finance_context.vocab import ValueStatus

# context.json written by build_context. The returned document does not keep rows.
_CONTEXT_DIRS: dict[int, Path] = {}


def _without_name_scope(items: list) -> list:
    """Drop the sheet a name is local to. Context keeps the name and its formula."""
    published = []
    for item in items:
        if isinstance(item, dict):
            published.append({key: value for key, value in item.items() if key != "sheet"})
        else:
            published.append(item)
    return published


def build_context(
    *,
    job_id: str,
    workbook_meta: dict,
    cells: list[dict] | None = None,
    layout: Layout,
    relations: list[RowRelation] | None = None,
    source_filename: str | None = None,
    content_sha256: str | None = None,
    status: str = "succeeded",
    stage: str = "done",
    graph: GraphPointer | None = None,
    edges: list[dict] | None = None,
    usage: SheetUsage | None = None,
    book_dir: Path | None = None,
    context_dir: Path,
) -> ContextDocument:
    relations = list(relations or [])
    date1904 = bool(workbook_meta.get("date1904"))
    defined_names = list(workbook_meta.get("defined_names") or [])
    hidden_indexes = None
    cell_path: Path | None = None
    memory_by_addr: dict | None = None
    if cells is None:
        if book_dir is None:
            raise ValueError("build_context needs cells or book_dir")
        cell_path = book_dir / "ir" / "cells.parquet"
        hidden_indexes = _occupied_from_file(cell_path)
        switch_rows = _switch_rows(cell_path, defined_names)
        cell_total = _file_rows(cell_path)
    else:
        switch_rows = None
        cell_total = len(cells)
        memory_by_addr = {(c["sheet"], int(c["row"]), int(c["col"])): c for c in cells}
    source = _CellSource(cell_path, memory_by_addr)
    if switch_rows is None:
        by_sheet_row: dict[str, dict[int, list[dict]]] = {}
        assert cells is not None
        for cell in cells:
            by_sheet_row.setdefault(cell["sheet"], {}).setdefault(int(cell["row"]), []).append(cell)
    else:
        by_sheet_row = switch_rows
    switches = {
        name: selector_rows(rows, date1904, defined_names=defined_names, sheet=name)
        for name, rows in by_sheet_row.items()
    }
    # Selector rows are plain ints. The formula table is not read again.
    by_sheet_row = None
    switch_rows = None
    # build_axes reads flag rows. _header_unit reads the header label. The
    # file path loads those rows only; period warnings still scan the file.
    if cell_path is None:
        assert cells is not None and memory_by_addr is not None
        header_by_addr = memory_by_addr
        axes, timeline_warnings = build_axes(
            layout,
            cells,
            date1904=date1904,
            by_addr=memory_by_addr,
            period_warnings=True,
        )
    else:
        header_by_addr = _load_rows(source, _axis_cell_rows(layout))
        axes, timeline_warnings = build_axes(
            layout,
            [],
            date1904=date1904,
            by_addr=header_by_addr,
            period_warnings=False,
        )
        timeline_warnings.extend(_period_warnings(cell_path, date1904))
    axes_by_id = {axis.id: axis for axis in axes}
    reads = RangeReads()
    # An explicit list wins. The pipeline passes none and the rows are read
    # from ir/edges.parquet in file order, one batch at a time.
    if edges is None and book_dir is not None:
        edge_source = book_dir / "ir" / "edges.parquet"
    else:
        edge_source = edges
    precedents, dependents = row_adjacency(
        iterate_edges(edge_source),
        limit=0,
        usage=usage,
        ranges=reads,
        rows=_consumed_rows(layout),
    )
    if hidden_indexes is None:
        hidden_warnings = _hidden_warnings(workbook_meta, cells)
    else:
        hidden_warnings = _hidden_warnings_from_indexes(workbook_meta, hidden_indexes)
    # One line per malformed sheet. First, so warnings[:50] cannot drop them
    # behind per-block notes.
    stored_order = workbook_meta.get("cell_order_warnings") or []
    warnings: list[str] = (
        [item for item in stored_order if isinstance(item, str) and item]
        if isinstance(stored_order, list)
        else []
    )
    not_applicable: set[str] = set()
    published: list[FinancialBlock] = []
    streamed_rows = 0
    date_flag = bool(workbook_meta.get("date1904"))
    body = MarkdownBody(context_dir)
    built: list[ContextDocument] = []

    def _stream_blocks():
        for sheet in layout.sheets:
            for block in sheet.blocks:
                yield from _stream_one_block(
                    sheet,
                    block,
                    axes_by_id=axes_by_id,
                    header_by_addr=header_by_addr,
                    source=source,
                    switches=switches,
                    precedents=precedents,
                    dependents=dependents,
                    reads=reads,
                    relations=relations,
                    date1904=date_flag,
                    not_applicable=not_applicable,
                    published=published,
                    body=body,
                    context_dir=context_dir,
                    on_rows=lambda count: _add_streamed(count),
                )

    def _add_streamed(count: int) -> None:
        nonlocal streamed_rows
        streamed_rows += count

    def _finish() -> dict:
        if cell_path is None:
            assert cells is not None
            formula_stats = _count_formula_cells(cells, not_applicable)
        else:
            formula_stats = _formula_stats(cell_path, not_applicable)
        # Same place as before the block loop: that loop does not append warnings.
        if formula_stats.unparsed:
            sample = ", ".join(formula_stats.unparsed_addrs[:_WARNING_SAMPLE])
            warnings.append(f"{formula_stats.unparsed} unparsed formula(s) (e.g. {sample})")
        expected_rows = sum(len(block.rows) for sheet in layout.sheets for block in sheet.blocks)
        if streamed_rows != expected_rows:
            warnings.append(
                f"Content completeness {streamed_rows}/{expected_rows} layout rows"
            )
        warnings.extend(timeline_warnings)
        if formula_stats.missing_values:
            sample = ", ".join(formula_stats.missing_addrs[:_WARNING_SAMPLE])
            extra = f" (e.g. {sample})" if sample else ""
            warnings.append(
                f"{formula_stats.missing_values} formula cell(s) missing cached values{extra}"
            )
        if graph is not None and graph.dangling:
            warnings.append(
                f"Graph: {graph.dangling} unresolved formula targets (see graph.json)"
            )
        warnings.extend(hidden_warnings)
        warnings.extend(_unknown_header_warnings(layout))
        sheets = [
            item["name"] if isinstance(item, dict) else getattr(item, "name", str(item))
            for item in workbook_meta.get("sheets", [])
        ]
        workbook = WorkbookRaw(
            sheets=sheets,
            sheet_count=len(sheets),
            cell_count=cell_total,
            has_vba=bool(workbook_meta.get("has_vba")),
            has_xlm=bool(workbook_meta.get("has_xlm")),
            externals=list(workbook_meta.get("externals") or []),
            locale_hint=workbook_meta.get("locale_hint"),
            date1904=date_flag,
            defined_names=_without_name_scope(defined_names),
            iterate=bool(workbook_meta.get("iterate")),
            formula_count=formula_stats.formula_count,
            missing_cached_values=formula_stats.missing_cached,
            unparsed_formulas=formula_stats.unparsed,
        )
        meta = ArtifactMeta(
            schema_version=SCHEMA_VERSION,
            job_id=job_id,
            status=status,  # type: ignore[arg-type]
            stage=stage,
            source_filename=source_filename,
            content_sha256=content_sha256,
            warnings=warnings[:50],
            questions=[],
        )
        document = ContextDocument(
            schema_version=SCHEMA_VERSION,
            meta=meta,
            workbook=workbook,
            axes=axes,
            blocks=published,
            ranges=[ContextRange.model_validate(item) for item in reads.records(usage)],
            graph=graph or GraphPointer(),
            warnings=warnings[:50],
        )
        built.append(document)
        body.close()
        _CONTEXT_DIRS[id(document)] = context_dir
        return document.model_dump(mode="json", exclude={"axes", "blocks"})

    axes_payload = [axis.model_dump(mode="json") for axis in axes]
    try:
        _write_streamed_context(
            context_dir / "context.json",
            axes_payload,
            _stream_blocks,
            _finish,
        )
    except BaseException:
        body.abort()
        raise
    return built[0]




def read_streamed_context(doc: ContextDocument) -> ContextDocument:
    """Load context.json for a document this process just built.

    ``build_context`` writes rows into that file and leaves ``blocks[].rows``
    empty, so a caller that needs the lines reads them back one document at a
    time instead of keeping them on the model.
    """
    directory = _CONTEXT_DIRS[id(doc)]
    return ContextDocument.model_validate_json(
        (directory / "context.json").read_text(encoding="utf-8")
    )


def _write_streamed_context(
    path: Path,
    axes_payload: list,
    blocks: Callable[[], Iterator[dict]],
    finish: Callable[[], dict],
) -> None:
    """``context.json`` in sorted-key order. Blocks are written before the tail.

    Warnings, formula totals, and the workbook are known only after the row
    loop. Those keys sort after ``blocks``, so the tail is dumped then.
    """
    encoder = json.JSONEncoder(ensure_ascii=False, indent=2, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    src = temporary_path(path)
    try:
        with src.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write('{\n  "axes": ')
            handle.writelines(_iter_json(axes_payload, 1, encoder, True))
            handle.write(',\n  "blocks": ')
            handle.writelines(_iter_json(JsonItems(blocks), 1, encoder, True))
            tail = finish()
            for key in sorted(tail):
                encoded = json.dumps(key, ensure_ascii=False)
                handle.write(f",\n  {encoded}: ")
                handle.writelines(_iter_json(tail[key], 1, encoder, True))
            handle.write("\n}\n")
            handle.flush()
            os.fsync(handle.fileno())
        atomic_replace(src, path)
    except BaseException:
        src.unlink(missing_ok=True)
        raise
    src.unlink(missing_ok=True)


def _stream_one_block(
    sheet,
    block,
    *,
    axes_by_id: dict[str, ContextAxis],
    header_by_addr: dict,
    source: _CellSource,
    switches: dict,
    precedents: dict,
    dependents: dict,
    reads: RangeReads,
    relations: list[RowRelation],
    date1904: bool,
    not_applicable: set[str],
    published: list[FinancialBlock],
    body: MarkdownBody,
    context_dir: Path,
    on_rows: Callable[[int], None],
) -> Iterator[dict]:
    """One block: header keys, then each row, then the markdown for that block."""
    kind = getattr(block, "kind", "timeline")
    views = _block_views(sheet, block)
    hint_headers = [header for _axis_id, headers in views for header in headers]
    axis_ids = (
        list(dict.fromkeys(axis_id for axis_id, _headers in views)) if kind != "params" else []
    )
    periods = (
        _local_periods(views, axes_by_id)
        if axis_ids
        else [_period_dict(header) for _axis_id, headers in views for header in headers]
    )
    skeleton = FinancialBlock(
        block_id=block.block_id,
        sheet=sheet.name,
        label_col=block.label_col,
        grain=None,
        kind=kind,
        axis_ids=axis_ids,
        periods=periods,
        rows=[],
        relations=_relations_for_block(relations, block.block_id),
    )
    published.append(skeleton)
    if not block.rows:
        body.extend(_block_intro(skeleton, axes_by_id, 0))
        yield skeleton.model_dump(mode="json")
        return
    parent_by_row = {row.row: row.label for row in block.rows}
    block_unit = _header_unit(sheet, block, header_by_addr, date1904)
    block_cells = source.load(sheet.name, {int(row.row) for row in block.rows})
    tables = _markdown_tables(skeleton, axes_by_id)
    writers = [
        AxisRows(context_dir, heading, headers, axis_id)
        for heading, headers, axis_id in tables
    ]
    state = {"n": 0}

    def _rows() -> Iterator[dict]:
        running_unit = block_unit
        body_unit = False
        try:
            for layout_row in block.rows:
                parent = parent_by_row.get(layout_row.parent_row) if layout_row.parent_row else None
                neighbors = _formula_neighbors(sheet.name, layout_row.row, precedents, dependents)
                label_path = list(layout_row.section_path)
                if parent and parent not in label_path:
                    label_path = [*label_path, parent]
                role_cells = _role_cells(sheet.name, layout_row, block_cells, date1904)
                own_unit = next(
                    (
                        item.cached_value
                        for item in role_cells
                        if item.role == "unit" and item.cached_value
                    ),
                    None,
                )
                if is_block_unit_caption(layout_row.label):
                    running_unit = layout_row.label.strip()
                    body_unit = True
                elif body_unit and layout_row.kind == "abstract":
                    # A caption inside a section stops at the next section.
                    # The header caption stays for the block.
                    running_unit = block_unit
                    body_unit = False
                unit_text = own_unit or (
                    None if is_block_unit_caption(layout_row.label) else running_unit
                )
                formats = _row_number_formats(
                    sheet_name=sheet.name,
                    row_num=layout_row.row,
                    headers=hint_headers,
                    by_addr=block_cells,
                )
                unit_from_cell = next(
                    (
                        item.cached_value
                        for item in role_cells
                        if item.role == "unit"
                    ),
                    None,
                )
                hints = _resolved_hints(
                    layout_row,
                    unit_text,
                    unit_from_cell=unit_from_cell,
                    number_formats=formats,
                )
                series_unit = hints.unit
                series: list[RowSeries] = []
                for axis_id, headers in views:
                    row_headers = headers
                    if layout_row.row in switches.get(sheet.name, ()):
                        row_headers = [header for header in headers if header.role == "value"]
                    fingerprint, exceptions, summary = _row_formula_and_numbers(
                        sheet_name=sheet.name,
                        row_num=layout_row.row,
                        headers=row_headers,
                        by_addr=block_cells,
                    )
                    values, statuses, normalized = _series_numbers(
                        sheet_name=sheet.name,
                        row_num=layout_row.row,
                        layout_row=layout_row,
                        headers=headers,
                        value_headers=row_headers,
                        by_addr=block_cells,
                        date1904=date1904,
                        factor=scale_factor_for(hints.scale),
                    )
                    series.append(
                        RowSeries(
                            axis_id=axis_id,
                            formula=fingerprint,
                            formula_exceptions=exceptions,
                            numeric_summary=summary,
                            values=values,
                            value_statuses=statuses,
                            normalized_values=normalized,
                        )
                    )
                first = series[0] if series else None
                line = _row_for_layout(
                    sheet_name=sheet.name,
                    block_id=block.block_id,
                    layout_row=layout_row,
                    layout_row_parent=parent,
                    headers=[],
                    value_headers=[],
                    by_addr=block_cells,
                    date1904=date1904,
                    label_path=label_path,
                    neighbors=neighbors,
                    neighbor_ranges=reads.for_row(sheet.name, layout_row.row),
                    formula=first.formula if first else None,
                    formula_exceptions=list(first.formula_exceptions) if first else [],
                    numeric_summary=first.numeric_summary if first else None,
                    hints=hints,
                    role_cells=role_cells,
                    series_unit=series_unit,
                    series=series,
                )
                for (_axis_id, headers), item in zip(views, series, strict=False):
                    for header, item_state in zip(headers, item.value_statuses, strict=False):
                        if item_state == "not_applicable":
                            not_applicable.add(
                                f"{sheet.name}!{format_addr(header.col, layout_row.row)}"
                            )
                for writer in writers:
                    writer.add(line)
                state["n"] += 1
                yield BLOCK_ROW_ADAPTER.dump_python(line, mode="json")
        finally:
            block_cells.clear()

    dumped = skeleton.model_dump(mode="json", exclude={"rows"})
    dumped["rows"] = JsonItems(_rows)
    try:
        yield dumped
        on_rows(state["n"])
        body.row_count += state["n"]
        body.extend(_block_intro(skeleton, axes_by_id, state["n"]))
        for writer in writers:
            writer.assemble(body)
        if state["n"] and skeleton.relations:
            body.extend(_relations_section(skeleton.relations))
    finally:
        for writer in writers:
            writer.discard()


class _CellSource:
    """Cells for one sheet and one set of rows. The file path reads that slice."""

    def __init__(self, path: Path | None, memory: dict | None) -> None:
        self.path = path
        self.memory = memory

    def load(self, sheet: str, rows: set[int]) -> dict:
        if not rows:
            return {}
        if self.path is not None:
            return _load_sheet_rows(self.path, sheet, rows)
        assert self.memory is not None
        return {
            key: cell
            for key, cell in self.memory.items()
            if key[0] == sheet and int(key[1]) in rows
        }


def _axis_cell_rows(layout: Layout) -> dict[str, set[int]]:
    """Header rows and flag rows. ``build_axes`` and ``_header_unit`` read only these."""
    found: dict[str, set[int]] = {}
    for sheet in layout.sheets:
        rows = found.setdefault(sheet.name, set())
        for axis in sheet.axes:
            header = getattr(axis, "header_row", None)
            if header:
                rows.add(int(header))
        for block in sheet.blocks:
            axis = getattr(block, "axis", None)
            if axis is not None and getattr(axis, "row", None):
                rows.add(int(axis.row))
            for row in block.rows:
                if row.kind == "flag":
                    rows.add(int(row.row))
    return {name: rows for name, rows in found.items() if rows}


def _load_rows(source: _CellSource, wanted: dict[str, set[int]]) -> dict:
    found: dict = {}
    for sheet, rows in wanted.items():
        found.update(source.load(sheet, rows))
    return found


def _load_sheet_rows(path: Path, sheet: str, rows: set[int]) -> dict:
    from finance_context.store.duckdb_io import _iter_parquet_rows

    if not path.is_file() or not rows:
        return {}
    columns = list(_BUILD_FIELDS)
    where = "sheet = ? AND row IN (SELECT unnest(?::BIGINT[]))"
    found: dict = {}
    for names, batch in _iter_parquet_rows(
        path,
        columns,
        where=where,
        params=[sheet, [int(row) for row in rows]],
    ):
        if list(names) != columns:
            raise ValueError(f"cells.parquet columns {names} != {columns}")
        for record in batch:
            cell = _BuildCell(*record)
            found[(cell.sheet, int(cell.row), int(cell.col))] = cell
    return found


def _local_periods(
    views: list[tuple[str, list[AxisHeader]]],
    axes_by_id: dict[str, ContextAxis],
) -> list[dict[str, object]]:
    """Column map for a block whose columns differ from the shared timeline."""
    if len(views) != 1:
        return []
    axis_id, headers = views[0]
    local = axes_by_id.get(axis_id)
    if local is None:
        return []
    shared = axes_by_id.get(local.timeline_id or local.id) or local
    canon = {period.period_key: period.col for period in shared.periods}
    if all(canon.get(header.period_key) == header.col for header in headers):
        return []
    return [_period_dict(header) for header in headers]


def _block_views(
    sheet,
    block,
) -> list[tuple[str, list[AxisHeader]]]:
    kind = getattr(block, "kind", "timeline")
    if kind == "params":
        headers = [
            header
            for header in (block.axis.headers if block.axis is not None else [])
            if header.role in {"value", "unknown"}
        ]
        return [(block.block_id, headers)]
    views: list[tuple[str, list[AxisHeader]]] = []
    for axis in axes_for(sheet, block):
        headers: list[AxisHeader] = []
        for period in axis.periods:
            if not is_series_column(period.kind, period.period_key):
                continue
            headers.append(
                AxisHeader(
                    col=period.col,
                    text=period.text,
                    kind=period.kind,
                    variant=period.variant,
                    period_key=period.period_key,
                    explicit_role=period.explicit_role,
                    hidden=bool(getattr(period, "hidden", False)),
                )
            )
        views.append((axis.id, headers))
    return views


def _series_numbers(
    *,
    sheet_name: str,
    row_num: int,
    layout_row: LayoutRow,
    headers: list,
    value_headers: list,
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
    factor: int | None,
) -> tuple[list[str | None], list[ValueStatus], list[str | None]]:
    keep_cols = {header.col for header in value_headers}
    values: list[str | None] = []
    statuses: list[ValueStatus] = []
    for header in headers:
        if header.col not in keep_cols:
            values.append(None)
            statuses.append("not_applicable")
            continue
        cell = by_addr.get((sheet_name, row_num, header.col))
        cached = None if cell is None else cell.get("cached_value")
        fmt = None if cell is None else cell.get("number_format")
        displayed = display_cell_text(
            cached if cached is not None else None,
            fmt,
            date1904=date1904,
        )
        if displayed is not None:
            cached = displayed
        text = None if cached in (None, "") else str(cached)
        values.append(text)
        structural = layout_row.kind not in _SERIES_KINDS and text is None
        statuses.append(value_status(text, applicable=not structural))
    return values, statuses, normalize_series(values, factor)






def _row_number_formats(
    *,
    sheet_name: str,
    row_num: int,
    headers: list,
    by_addr: dict[tuple[str, int, int], dict],
) -> list[str]:
    out: list[str] = []
    for header in headers:
        cell = by_addr.get((sheet_name, row_num, header.col))
        fmt = None if cell is None else cell.get("number_format")
        if fmt:
            out.append(str(fmt))
    return out


def _formula_neighbors(
    sheet: str,
    row: int,
    precedents: dict[tuple[str, int], list[str]],
    dependents: dict[tuple[str, int], list[str]],
) -> list[str]:
    return list(
        dict.fromkeys([*precedents.get((sheet, row), []), *dependents.get((sheet, row), [])])
    )











def _row_formula_and_numbers(
    *,
    sheet_name: str,
    row_num: int,
    headers: list,
    by_addr: dict[tuple[str, int, int], dict],
) -> tuple[str | None, list[str], NumericSummary | None]:
    templates: list[str] = []
    exceptions: list[str] = []
    numbers: list[float] = []
    raw_first: str | None = None
    raw_last: str | None = None
    for header in headers:
        cell = by_addr.get((sheet_name, row_num, header.col))
        if cell is None:
            continue
        template = cell.get("formula_template")
        if template:
            templates.append(str(template))
        cached = cell.get("cached_value")
        if cached in (None, ""):
            continue
        text = str(cached)
        if raw_first is None:
            raw_first = text
        raw_last = text
        number = parse_number(text)
        if number is None:
            continue
        numbers.append(number)
    fingerprint = Counter(templates).most_common(1)[0][0] if templates else None
    if fingerprint:
        for header in headers:
            cell = by_addr.get((sheet_name, row_num, header.col))
            if cell is None:
                continue
            template = cell.get("formula_template")
            if template and str(template) != fingerprint:
                exceptions.append(f"{sheet_name}!{format_addr(header.col, row_num)}")
    summary = None
    if raw_first is not None or numbers:
        summary = NumericSummary(
            first=raw_first,
            last=raw_last,
            minimum=format_number(min(numbers)) if numbers else None,
            maximum=format_number(max(numbers)) if numbers else None,
            constant=len(set(numbers)) <= 1 if numbers else False,
            n=len(numbers),
        )
    return fingerprint, exceptions[:8], summary


def _role_cells(
    sheet: str,
    layout_row: LayoutRow,
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
) -> list[RoleCell]:
    out: list[RoleCell] = []
    for item in layout_row.cells:
        cell = by_addr.get((sheet, layout_row.row, item.col))
        cached = None if cell is None else cell.get("cached_value")
        fmt = None if cell is None else cell.get("number_format")
        displayed = display_cell_text(
            cached if cached is not None else None, fmt, date1904=date1904
        )
        out.append(
            RoleCell(
                addr=format_addr(item.col, layout_row.row),
                col=item.col,
                role=item.role,
                cached_value=displayed if displayed is not None else cached,
                header=item.header,
                hidden=bool(cell and cell.get("hidden")),
            )
        )
    return out


def _header_unit(sheet, block, by_addr: dict, date1904: bool) -> str | None:
    """The label cell of the period-header row when it is a unit caption.

    ``RURm`` beside ``2020`` is that caption. ``Year`` in the same cell is the
    timeline name, and a ``k£`` in the unit column is that row's own unit.
    """
    rows = {axis.header_row for axis in axes_for(sheet, block)}
    if block.axis is not None:
        rows.add(block.axis.row)
    label_col = getattr(block, "label_col", None)
    if not rows or label_col is None:
        return None
    found: list[tuple[int, str]] = []
    for (name, row, col), cell in by_addr.items():
        if name != sheet.name or row not in rows or int(col) != int(label_col):
            continue
        text = display_cell_text(
            cell.get("cached_value"),
            cell.get("number_format"),
            date1904=date1904,
        )
        if text and is_block_unit_caption(text):
            found.append((int(row), text.strip()))
    if not found:
        return None
    found.sort()
    return found[0][1]


def _unknown_header_warnings(layout: Layout) -> list[str]:
    """Params columns whose header is not a format word. Their values stay, the role is ``unknown``.

    Two or more period headers mean a timeline whose axis did not form: some did
    not parse (``9М 2025``), or a miss between parsed ones broke the run.
    """
    warnings: list[str] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            if block.kind != "params" or block.axis is None:
                continue
            unknown = [
                header
                for header in block.axis.headers
                if header.role == "unknown" and header.text
            ]
            periodish = [header for header in unknown if _period_header(header.text)]
            if len(periodish) >= 2:
                warnings.append(_no_axis_warning(sheet.name, block.axis.row, periodish))
                unknown = [header for header in unknown if header not in periodish]
            if not unknown:
                continue
            quoted = ", ".join(f"«{header.text}»" for header in unknown)
            warnings.append(f"Unrecognized column headers in {block.block_id}: {quoted}")
    return warnings


def _period_header(text: str) -> bool:
    return period_like_header(text) or is_calendar_hit(classify_header(text))


def _no_axis_warning(sheet: str, row: int, headers: list[AxisHeader]) -> str:
    cols = sorted(header.col for header in headers)
    span = f"{sheet}!{format_addr(cols[0], row)}:{format_addr(cols[-1], row)}"
    quoted = ", ".join(f"«{header.text}»" for header in headers)
    return (
        f"No time axis on {sheet}: headers {span} look like periods but do not form"
        f" an axis ({quoted}); columns kept as unknown"
    )


# The warning text shows three examples. Counts stay complete.
_WARNING_SAMPLE = 3


class _FormulaStats:
    def __init__(self) -> None:
        self.formula_count = 0
        self.missing_cached = 0
        self.unparsed = 0
        self.missing_values = 0
        self.missing_addrs: list[str] = []
        self.unparsed_addrs: list[str] = []


def _remember(sample: list[str], addr: str) -> None:
    if len(sample) < _WARNING_SAMPLE:
        sample.append(addr)


def _count_formula_cells(cells: list, not_applicable: set[str]) -> _FormulaStats:
    """Formula totals for cells already in memory. Samples stop at three."""
    stats = _FormulaStats()
    for cell in cells:
        if not cell.get("formula_raw"):
            continue
        stats.formula_count += 1
        missing = cell.get("cached_value") is None
        flag = bool(cell.get("unparsed"))
        if not missing and not flag:
            continue
        addr = f"{cell['sheet']}!{cell['addr']}"
        if missing:
            stats.missing_cached += 1
            if addr not in not_applicable:
                stats.missing_values += 1
                _remember(stats.missing_addrs, addr)
        if flag:
            stats.unparsed += 1
            _remember(stats.unparsed_addrs, addr)
    return stats


def _consumed_rows(layout: Layout) -> set[tuple[str, int]]:
    """Layout rows whose formula neighbors are published. Axis headers are not."""
    found: set[tuple[str, int]] = set()
    for sheet in layout.sheets:
        for block in sheet.blocks:
            for layout_row in block.rows:
                found.add((sheet.name, int(layout_row.row)))
    return found


def _layout_rows(layout: Layout) -> dict[str, set[int]]:
    found: dict[str, set[int]] = {}
    for sheet in layout.sheets:
        rows = found.setdefault(sheet.name, set())
        for block in sheet.blocks:
            for row in block.rows:
                rows.add(int(row.row))
            axis = getattr(block, "axis", None)
            if axis is not None and getattr(axis, "row", None):
                rows.add(int(axis.row))
        for axis in sheet.axes:
            header = getattr(axis, "header_row", None)
            if header:
                rows.add(int(header))
    return {name: rows for name, rows in found.items() if rows}


# Fields the file path reads back. addr, formula_raw, and unparsed stay on disk:
# period warnings and formula counts scan the file, and this path never reads them.
_BUILD_FIELDS = (
    "sheet",
    "row",
    "col",
    "formula_template",
    "cached_value",
    "hidden",
    "number_format",
)
_BUILD_FIELD_SET = frozenset(_BUILD_FIELDS)


class _BuildCell:
    """One layout value cell. Dict-style reads, slot storage."""

    __slots__ = _BUILD_FIELDS

    def __init__(
        self,
        sheet: object,
        row: object,
        col: object,
        formula_template: object,
        cached_value: object,
        hidden: object,
        number_format: object,
    ) -> None:
        self.sheet = sheet
        self.row = row
        self.col = col
        self.formula_template = formula_template
        self.cached_value = cached_value
        self.hidden = hidden
        self.number_format = number_format

    def get(self, key: str, default: object = None) -> object:
        if key not in _BUILD_FIELD_SET:
            return default
        return getattr(self, key)

    def __getitem__(self, key: str) -> object:
        if key not in _BUILD_FIELD_SET:
            raise KeyError(key)
        return getattr(self, key)


def _layout_value_cells(path: Path, layout: Layout) -> list[_BuildCell]:
    from finance_context.store.duckdb_io import _iter_parquet_rows

    if not path.is_file():
        return []
    columns = list(_BUILD_FIELDS)
    found: list[_BuildCell] = []
    where = "sheet = ? AND row IN (SELECT unnest(?::BIGINT[]))"
    for sheet, rows in _layout_rows(layout).items():
        for names, batch in _iter_parquet_rows(
            path,
            columns,
            where=where,
            params=[sheet, [int(row) for row in rows]],
        ):
            if list(names) != columns:
                raise ValueError(f"cells.parquet columns {names} != {columns}")
            for record in batch:
                found.append(_BuildCell(*record))
    return found


def _iter_cell_columns(path: Path, columns: list[str]):
    import pyarrow.parquet as pq

    if not path.is_file():
        return
    for batch in pq.ParquetFile(path).iter_batches(columns=columns, batch_size=65536):
        yield [batch.column(index).to_pylist() for index in range(len(columns))]


def _formula_stats(path: Path, not_applicable: set[str]) -> _FormulaStats:
    """Formula totals for the file. Samples stop at three addresses."""
    stats = _FormulaStats()
    for sheets, addrs, formulas, cached, flags in _iter_cell_columns(
        path, ["sheet", "addr", "formula_raw", "cached_value", "unparsed"]
    ):
        for index, formula in enumerate(formulas):
            if not formula:
                continue
            stats.formula_count += 1
            missing = cached[index] is None
            flag = bool(flags[index])
            if not missing and not flag:
                continue
            addr = f"{sheets[index]}!{addrs[index]}"
            if missing:
                stats.missing_cached += 1
                if addr not in not_applicable:
                    stats.missing_values += 1
                    _remember(stats.missing_addrs, addr)
            if flag:
                stats.unparsed += 1
                _remember(stats.unparsed_addrs, addr)
    return stats


def _occupied_from_file(path: Path) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    rows: dict[str, set[int]] = {}
    cols: dict[str, set[int]] = {}
    for sheets, row_numbers, col_numbers, formulas, cached in _iter_cell_columns(
        path, ["sheet", "row", "col", "formula_raw", "cached_value"]
    ):
        for index, sheet_name in enumerate(sheets):
            cell = {"formula_raw": formulas[index], "cached_value": cached[index]}
            if not _cell_has_content(cell):
                continue
            sheet = str(sheet_name or "")
            rows.setdefault(sheet, set()).add(int(row_numbers[index]))
            cols.setdefault(sheet, set()).add(int(col_numbers[index]))
    return rows, cols


def _switch_rows(
    path: Path, defined_names: list | None
) -> dict[str, dict[int, list[dict]]]:
    """INDEX, CHOOSE, and OFFSET formulas, plus the constants they name.

    A SUM or a plain reference cannot select a column. Keeping one dict per
    formula on the sheet does not change which rows ``selector_rows`` returns,
    and those other formulas are what made this table large. A formula outside
    the layout still counts: two of them are enough to mark a constant.
    """
    from finance_context.layout.params import _names_on_sheet, _switch_args, _switch_call
    from finance_context.store.fs import read_parquet_dicts

    by_sheet: dict[str, dict[int, list[dict]]] = {}
    wanted: dict[str, set[str]] = {}
    names_by_sheet: dict[str, dict[str, str]] = {}
    for sheets, rows, cols, addrs, raw in _iter_cell_columns(
        path, ["sheet", "row", "col", "addr", "formula_raw"]
    ):
        for index, formula in enumerate(raw):
            if not formula:
                continue
            text = str(formula)
            if not _switch_call(text):
                continue
            sheet = str(sheets[index])
            # The index is the same for every formula on a sheet. setdefault would
            # still build it each time, because the argument is evaluated first.
            names = names_by_sheet.get(sheet)
            if names is None:
                names = _names_on_sheet(defined_names, sheet)
                names_by_sheet[sheet] = names
            args = _switch_args(text, names)
            if not args:
                continue
            row_n = int(rows[index])
            by_sheet.setdefault(sheet, {}).setdefault(row_n, []).append(
                {
                    "sheet": sheets[index],
                    "row": row_n,
                    "col": int(cols[index]),
                    "addr": addrs[index],
                    "formula_raw": formula,
                }
            )
            bucket = wanted.setdefault(sheet, set())
            for addr in args:
                bucket.add(addr.upper().replace("$", ""))
    if not path.is_file():
        return by_sheet
    for sheet, addrs in wanted.items():
        if not addrs:
            continue
        present = {
            str(cell.get("addr") or "").upper().replace("$", "")
            for rows in by_sheet.get(sheet, {}).values()
            for cell in rows
        }
        missing = [addr for addr in addrs if addr not in present]
        if not missing:
            continue
        fetched = read_parquet_dicts(
            path,
            ["sheet", "row", "col", "addr", "formula_raw"],
            where="sheet = ? AND upper(replace(addr, '$', '')) IN (SELECT unnest(?::VARCHAR[]))",
            params=[sheet, missing],
        )
        for cell in fetched:
            by_sheet.setdefault(sheet, {}).setdefault(int(cell["row"]), []).append(cell)
    return by_sheet


def _period_warnings(path: Path, date1904: bool) -> list[str]:
    """Unparsed period headers in file order. One row is four column values, not a dict."""
    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    for sheets, addrs, cached, formats in _iter_cell_columns(
        path, ["sheet", "addr", "cached_value", "number_format"]
    ):
        for index, value in enumerate(cached):
            if value is None:
                continue
            text = _period_warning_value(value, formats[index], date1904)
            if not text or not unparsed_period_shape(text):
                continue
            sheet = sheets[index]
            key = (str(sheet), text)
            if key in seen:
                continue
            seen.add(key)
            out.append(f"Unparsed period header {sheet}!{addrs[index]}: «{text}»")
    return out


def _file_rows(path: Path) -> int:
    import pyarrow.parquet as pq

    if not path.is_file():
        return 0
    return pq.ParquetFile(path).metadata.num_rows


def _hidden_warnings_from_indexes(
    workbook_meta: dict,
    indexes: tuple[dict[str, set[int]], dict[str, set[int]]],
) -> list[str]:
    occupied_rows, occupied_cols = indexes
    warnings: list[str] = []
    for sheet in workbook_meta.get("sheets") or []:
        if not isinstance(sheet, dict) or not sheet.get("name"):
            continue
        name = str(sheet["name"])
        if sheet.get("hidden"):
            warnings.append(f"Hidden sheet {name} is included")
        rows = _span_list(
            row
            for row in sheet.get("hidden_rows") or []
            if int(row) in occupied_rows.get(name, ())
        )
        if rows:
            warnings.append(f"Hidden rows {name}!{rows} are included")
        cols = _span_list(
            (
                col
                for col in sheet.get("hidden_cols") or []
                if int(col) in occupied_cols.get(name, ())
            ),
            index_to_col,
        )
        if cols:
            warnings.append(f"Hidden columns {name}!{cols} are included")
    return warnings


def _hidden_warnings(workbook_meta: dict, cells: list[dict]) -> list[str]:
    occupied_rows, occupied_cols = _occupied_hidden_indexes(cells)
    warnings: list[str] = []
    for sheet in workbook_meta.get("sheets") or []:
        if not isinstance(sheet, dict) or not sheet.get("name"):
            continue
        name = str(sheet["name"])
        if sheet.get("hidden"):
            warnings.append(f"Hidden sheet {name} is included")
        rows = _span_list(
            row
            for row in sheet.get("hidden_rows") or []
            if int(row) in occupied_rows.get(name, ())
        )
        if rows:
            warnings.append(f"Hidden rows {name}!{rows} are included")
        cols = _span_list(
            (
                col
                for col in sheet.get("hidden_cols") or []
                if int(col) in occupied_cols.get(name, ())
            ),
            index_to_col,
        )
        if cols:
            warnings.append(f"Hidden columns {name}!{cols} are included")
    return warnings


def _occupied_hidden_indexes(
    cells: list[dict],
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """Rows and columns that hold a value or a formula."""
    rows: dict[str, set[int]] = {}
    cols: dict[str, set[int]] = {}
    for cell in cells:
        if not _cell_has_content(cell):
            continue
        sheet = str(cell.get("sheet") or "")
        rows.setdefault(sheet, set()).add(int(cell["row"]))
        cols.setdefault(sheet, set()).add(int(cell["col"]))
    return rows, cols


def _cell_has_content(cell: dict) -> bool:
    formula = cell.get("formula_raw")
    if isinstance(formula, str) and formula.strip():
        return True
    value = cell.get("cached_value")
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _span_list(values: list, render=str) -> str:
    nums = sorted({int(value) for value in values})
    if not nums:
        return ""
    spans: list[str] = []
    start = prev = nums[0]
    for num in nums[1:]:
        if num == prev + 1:
            prev = num
            continue
        spans.append(_one_span(start, prev, render))
        start = prev = num
    spans.append(_one_span(start, prev, render))
    return ",".join(spans)


def _one_span(start: int, end: int, render) -> str:
    if start == end:
        return str(render(start))
    return f"{render(start)}:{render(end)}"


def _period_dict(header: AxisHeader) -> dict[str, object]:
    item: dict[str, object] = {
        "col": header.col,
        "text": header.text,
        "period_key": header.period_key,
    }
    # Params columns keep value/unit/note. Period columns have no role.
    if header.role:
        item["role"] = header.role
    if header.hidden:
        item["hidden"] = True
    return item






