from __future__ import annotations

from collections import Counter

from finance_context.context.assemble import (
    _SERIES_KINDS,
    _hints_for,
    _relations_for_block,
    _row_for_layout,
)
from finance_context.context.series import (
    format_number,
    normalize_series,
    scale_factor_for,
    value_status,
)
from finance_context.context.timeline import build_axes
from finance_context.excel.a1 import format_addr, index_to_col
from finance_context.formulas.csr import SheetUsage
from finance_context.layout.adjacency import row_adjacency
from finance_context.layout.models import AxisHeader, Layout, LayoutRow, is_series_column
from finance_context.layout.params import selector_rows
from finance_context.layout.periods import (
    classify_header,
    display_cell_text,
    is_calendar_hit,
    period_like_header,
)
from finance_context.layout.relations import RowRelation
from finance_context.layout.resolve import axes_for
from finance_context.layout.units import is_block_unit_caption
from finance_context.models.context import (
    SCHEMA_VERSION,
    ArtifactMeta,
    BlockRow,
    ContextAxis,
    ContextDocument,
    FinancialBlock,
    GraphPointer,
    NumericSummary,
    RoleCell,
    RowSeries,
    WorkbookRaw,
)
from finance_context.vocab import ValueStatus

# Row neighbors kept per side. One SUMIF(A:A) copied down 20k rows makes every
# row of A a neighbor of every formula row; past this many the list is cut.
ROW_NEIGHBOR_LIMIT = 256


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
    cells: list[dict],
    layout: Layout,
    relations: list[RowRelation] | None = None,
    source_filename: str | None = None,
    content_sha256: str | None = None,
    status: str = "succeeded",
    stage: str = "done",
    graph: GraphPointer | None = None,
    edges: list[dict] | None = None,
    usage: SheetUsage | None = None,
) -> ContextDocument:
    relations = list(relations or [])
    by_addr = {(c["sheet"], int(c["row"]), int(c["col"])): c for c in cells}
    by_sheet_row: dict[str, dict[int, list[dict]]] = {}
    for cell in cells:
        by_sheet_row.setdefault(cell["sheet"], {}).setdefault(int(cell["row"]), []).append(cell)
    date1904 = bool(workbook_meta.get("date1904"))
    defined_names = list(workbook_meta.get("defined_names") or [])
    switches = {
        name: selector_rows(rows, date1904, defined_names=defined_names, sheet=name)
        for name, rows in by_sheet_row.items()
    }
    axes, timeline_warnings = build_axes(
        layout, cells, date1904=bool(workbook_meta.get("date1904"))
    )
    axes_by_id = {axis.id: axis for axis in axes}
    precedents, dependents = row_adjacency(edges or [], limit=ROW_NEIGHBOR_LIMIT, usage=usage)
    hidden_warnings = _hidden_warnings(workbook_meta, cells)
    warnings: list[str] = []
    formula_count = 0
    missing_cached = 0
    unparsed = 0
    missing_addrs: list[str] = []
    for cell in cells:
        if cell.get("formula_raw"):
            formula_count += 1
            if cell.get("cached_value") is None:
                missing_cached += 1
                missing_addrs.append(f"{cell['sheet']}!{cell['addr']}")
            if cell.get("unparsed"):
                unparsed += 1
                warnings.append(f"Unparsed formula at {cell['sheet']}!{cell['addr']}")

    not_applicable: set[str] = set()
    blocks: list[FinancialBlock] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            kind = getattr(block, "kind", "timeline")
            views = _block_views(sheet, block)
            hint_headers = [header for _axis_id, headers in views for header in headers]
            axis_ids = (
                list(dict.fromkeys(axis_id for axis_id, _headers in views))
                if kind != "params"
                else []
            )
            periods = _local_periods(views, axes_by_id) if axis_ids else [
                _period_dict(header)
                for _axis_id, headers in views
                for header in headers
            ]
            rows: list[BlockRow] = []
            parent_by_row = {r.row: r.label for r in block.rows}
            block_unit = _header_unit(
                sheet,
                block,
                by_addr,
                bool(workbook_meta.get("date1904")),
            )
            running_unit = block_unit
            body_unit = False
            for layout_row in block.rows:
                parent = None
                if layout_row.parent_row:
                    parent = parent_by_row.get(layout_row.parent_row)
                neighbors = _formula_neighbors(
                    sheet.name, layout_row.row, precedents, dependents
                )
                label_path = list(layout_row.section_path)
                if parent and parent not in label_path:
                    label_path = [*label_path, parent]
                role_cells = _role_cells(
                    sheet.name, layout_row, by_addr, bool(workbook_meta.get("date1904"))
                )
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
                    # A caption inside a section (`% p.a.`) stops at the next section.
                    # The header caption (`RURm` beside the years) stays for the block.
                    running_unit = block_unit
                    body_unit = False
                unit_text = own_unit or (
                    None if is_block_unit_caption(layout_row.label) else running_unit
                )
                formats = _row_number_formats(
                    sheet_name=sheet.name,
                    row_num=layout_row.row,
                    headers=hint_headers,
                    by_addr=by_addr,
                )
                hints = _hints_for(
                    layout_row,
                    unit_text,
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
                        by_addr=by_addr,
                    )
                    values, statuses, normalized = _series_numbers(
                        sheet_name=sheet.name,
                        row_num=layout_row.row,
                        layout_row=layout_row,
                        headers=headers,
                        value_headers=row_headers,
                        by_addr=by_addr,
                        date1904=bool(workbook_meta.get("date1904")),
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
                    by_addr=by_addr,
                    date1904=bool(workbook_meta.get("date1904")),
                    label_path=label_path,
                    neighbors=neighbors,
                    formula=first.formula if first else None,
                    formula_exceptions=list(first.formula_exceptions) if first else [],
                    numeric_summary=first.numeric_summary if first else None,
                    hints=hints,
                    role_cells=role_cells,
                    series_unit=series_unit,
                    series=series,
                )
                for (_axis_id, headers), item in zip(views, series, strict=False):
                    for header, state in zip(headers, item.value_statuses, strict=False):
                        if state == "not_applicable":
                            not_applicable.add(
                                f"{sheet.name}!{format_addr(header.col, layout_row.row)}"
                            )
                rows.append(line)
            blocks.append(
                FinancialBlock(
                    block_id=block.block_id,
                    sheet=sheet.name,
                    label_col=block.label_col,
                    grain=None,
                    kind=kind,
                    axis_ids=axis_ids,
                    periods=periods,
                    rows=rows,
                    relations=_relations_for_block(relations, block.block_id),
                )
            )

    document_rows = [row for block in blocks for row in block.rows]
    expected_rows = sum(len(block.rows) for sheet in layout.sheets for block in sheet.blocks)
    if len(document_rows) != expected_rows:
        warnings.append(
            f"Content completeness {len(document_rows)}/{expected_rows} layout rows"
        )
    warnings.extend(timeline_warnings)
    missing_values = [addr for addr in missing_addrs if addr not in not_applicable]
    if missing_values:
        sample = ", ".join(missing_values[:3])
        extra = f" (e.g. {sample})" if sample else ""
        warnings.append(f"{len(missing_values)} formula cell(s) missing cached values{extra}")
    if graph is not None and graph.dangling:
        warnings.append(
            f"Graph: {graph.dangling} unresolved formula targets (see graph.json)"
        )
    warnings.extend(hidden_warnings)
    warnings.extend(_unknown_header_warnings(layout))

    sheets = [
        s["name"] if isinstance(s, dict) else getattr(s, "name", str(s))
        for s in workbook_meta.get("sheets", [])
    ]
    workbook = WorkbookRaw(
        sheets=sheets,
        sheet_count=len(sheets),
        cell_count=len(cells),
        has_vba=bool(workbook_meta.get("has_vba")),
        has_xlm=bool(workbook_meta.get("has_xlm")),
        externals=list(workbook_meta.get("externals") or []),
        locale_hint=workbook_meta.get("locale_hint"),
        date1904=bool(workbook_meta.get("date1904")),
        defined_names=_without_name_scope(defined_names),
        iterate=bool(workbook_meta.get("iterate")),
        formula_count=formula_count,
        missing_cached_values=missing_cached,
        unparsed_formulas=unparsed,
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
    return ContextDocument(
        schema_version=SCHEMA_VERSION,
        meta=meta,
        workbook=workbook,
        axes=axes,
        blocks=blocks,
        graph=graph or GraphPointer(),
        warnings=warnings[:50],
    )




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
        try:
            numbers.append(float(text.replace(",", "")))
        except ValueError:
            continue
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






