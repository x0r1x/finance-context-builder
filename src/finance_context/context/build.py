from __future__ import annotations

from collections import Counter

from finance_context.context.assemble import (
    _SERIES_KINDS,
    _candidates_for,
    _hints_for,
    _relations_for_block,
    _row_for_layout,
)
from finance_context.context.series import (
    format_number,
    normalize_series,
    phase_gate,
    scale_factor_for,
    value_status,
)
from finance_context.context.timeline import build_axes
from finance_context.excel.a1 import format_addr
from finance_context.layout.models import AxisHeader, Layout, LayoutRow
from finance_context.layout.params import is_scenario_selector_label
from finance_context.layout.periods import display_cell_text
from finance_context.layout.resolve import axes_for
from finance_context.mapping.adjacency import cell_ref_row, row_adjacency, row_key_ref
from finance_context.mapping.eval import (
    assess_mapping_quality,
    context_report_metrics,
    inventory_coverage_counts,
)
from finance_context.mapping.models import (
    MappedRow,
    MappingDocument,
    RowContext,
    RowRelation,
)
from finance_context.mapping.rowroles import infer_row_roles
from finance_context.mapping.semantics import classify_semantics
from finance_context.mapping.taxonomy import load_taxonomy
from finance_context.models.context import (
    SCHEMA_VERSION,
    ArtifactMeta,
    BlockRow,
    ContextAxis,
    ContextDocument,
    FinancialBlock,
    GraphPointer,
    MappingStats,
    NumericSummary,
    RoleCell,
    RowSeries,
    WorkbookRaw,
)
from finance_context.vocab import ValueStatus

_SERIES_ROLES = {
    "historical",
    "forecast",
    "stub",
    "relative",
    "value",
    "scenario",
}


def build_context(
    *,
    job_id: str,
    workbook_meta: dict,
    cells: list[dict],
    layout: Layout,
    mapping: MappingDocument,
    concept_accept_min: float,
    source_filename: str | None = None,
    content_sha256: str | None = None,
    status: str = "succeeded",
    stage: str = "done",
    graph: GraphPointer | None = None,
    edges: list[dict] | None = None,
    concepts: list | None = None,
) -> ContextDocument:
    catalog = list(concepts) if concepts is not None else load_taxonomy()
    by_concept = {item.id: item for item in catalog}
    by_addr = {(c["sheet"], int(c["row"]), int(c["col"])): c for c in cells}
    axes, timeline_warnings = build_axes(
        layout, cells, date1904=bool(workbook_meta.get("date1904"))
    )
    axes_by_id = {axis.id: axis for axis in axes}
    mapped_by_key = {row.row_key: row for row in mapping.rows}
    _precedents, dependents = row_adjacency(edges or [])
    concept_by_ref = {
        row_key_ref(row.sheet, row.row): row.concept_id
        for row in mapping.rows
        if row.concept_id
    }
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

    roll_time = _roll_forward_time(mapping.relations)
    out_of_phase: set[str] = set()
    blocks: list[FinancialBlock] = []
    for sheet in layout.sheets:
        for block in sheet.blocks:
            kind = getattr(block, "kind", "timeline")
            views = _block_views(sheet, block, axes_by_id)
            hint_headers = [header for _axis_id, headers, _phases in views for header in headers]
            axis_ids = (
                list(dict.fromkeys(axis_id for axis_id, _headers, _phases in views))
                if kind != "params"
                else []
            )
            periods = _local_periods(views, axes_by_id) if axis_ids else [
                {
                    "col": header.col,
                    "text": header.text,
                    "role": header.role,
                    "period_key": header.period_key,
                }
                for _axis_id, headers, _phases in views
                for header in headers
            ]
            rows: list[BlockRow] = []
            parent_by_row = {r.row: r.label for r in block.rows}
            labeled = [r.label for r in block.rows if r.label]
            for index, layout_row in enumerate(block.rows):
                row_key = f"{sheet.name}|{layout_row.row}|{block.block_id}"
                mapped = mapped_by_key.get(row_key)
                parent = None
                if layout_row.parent_row:
                    parent = parent_by_row.get(layout_row.parent_row)
                neighbors = _neighbors(labeled, index)
                label_path = list(layout_row.section_path)
                if parent and parent not in label_path:
                    label_path = [*label_path, parent]
                role_cells = _role_cells(
                    sheet.name, layout_row, by_addr, bool(workbook_meta.get("date1904"))
                )
                unit_text = next(
                    (
                        item.cached_value
                        for item in role_cells
                        if item.role == "unit" and item.cached_value
                    ),
                    None,
                )
                formats = _row_number_formats(
                    sheet_name=sheet.name,
                    row_num=layout_row.row,
                    headers=hint_headers,
                    by_addr=by_addr,
                )
                static = _is_static_row(kind, sheet.name, layout_row, hint_headers, by_addr)
                hints = _hints_for(
                    mapped,
                    layout_row,
                    unit_text,
                    sheet=sheet.name,
                    parent=parent,
                    number_formats=formats,
                    static=static,
                    concepts=by_concept,
                )
                rolled = roll_time.get(row_key)
                if rolled is not None and not static:
                    update = {"time_semantics": rolled}
                    if rolled == "flow":
                        update["nature"] = "flow"
                    hints = hints.model_copy(update=update)
                series_unit = hints.unit
                role_ctx = RowContext(
                    row_key=row_key,
                    sheet=sheet.name,
                    row=layout_row.row,
                    block_id=block.block_id,
                    label=layout_row.label,
                    parent_label=parent,
                    section_path=list(layout_row.section_path),
                    kind=layout_row.kind,
                    article_role=mapped.article_role if mapped else "database_like",
                )
                feeds_cfads = _row_feeds_cfads(
                    sheet.name, layout_row.row, dependents, concept_by_ref
                )
                context_role, secondary = infer_row_roles(
                    role_ctx,
                    mapped.concept_id if mapped else None,
                    feeds_cfads=feeds_cfads,
                )
                if mapped is not None:
                    secondary = list(
                        dict.fromkeys([*mapped.secondary_concepts, *secondary])
                    )
                identity, reporting_roles, cash = classify_semantics(
                    label=layout_row.label,
                    concept_id=mapped.concept_id if mapped else None,
                    score=mapped.score if mapped else None,
                    alternatives=list(mapped.alternatives) if mapped else [],
                    context_role=context_role,
                    secondary_concepts=secondary,
                )
                candidates = _candidates_for(mapped)
                series: list[RowSeries] = []
                for axis_id, headers, phases in views:
                    row_headers = headers
                    if is_scenario_selector_label(layout_row.label):
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
                        mapped=mapped,
                        headers=headers,
                        value_headers=row_headers,
                        by_addr=by_addr,
                        date1904=bool(workbook_meta.get("date1904")),
                        period_phases=phases,
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
                    mapped=mapped,
                    headers=[],
                    value_headers=[],
                    by_addr=by_addr,
                    date1904=bool(workbook_meta.get("date1904")),
                    label_path=label_path,
                    neighbors=neighbors,
                    formula=first.formula if first else None,
                    formula_exceptions=list(first.formula_exceptions) if first else [],
                    numeric_summary=first.numeric_summary if first else None,
                    candidates=candidates,
                    hints=hints,
                    role_cells=role_cells,
                    series_unit=series_unit,
                    concepts=by_concept,
                    context_role=context_role,
                    secondary_concepts=secondary,
                    semantic_identity=identity,
                    reporting_roles=reporting_roles,
                    cash_semantics=cash,
                    series=series,
                    static=static,
                )
                for (_axis_id, headers, _phases), item in zip(views, series, strict=False):
                    for header, state in zip(headers, item.value_statuses, strict=False):
                        if state == "not_applicable":
                            out_of_phase.add(
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
                    relations=_relations_for_block(mapping.relations, block.block_id),
                )
            )

    document_rows = [row for block in blocks for row in block.rows]
    expected_rows = sum(len(block.rows) for sheet in layout.sheets for block in sheet.blocks)
    if len(document_rows) != expected_rows:
        warnings.append(
            f"Content completeness {len(document_rows)}/{expected_rows} layout rows"
        )
    warnings.extend(timeline_warnings)
    in_phase_missing = [addr for addr in missing_addrs if addr not in out_of_phase]
    if in_phase_missing:
        sample = ", ".join(in_phase_missing[:3])
        extra = f" (e.g. {sample})" if sample else ""
        warnings.append(f"{len(in_phase_missing)} formula cell(s) missing cached values{extra}")
    if graph is not None and graph.dangling:
        warnings.append(
            f"Graph: {graph.dangling} unresolved formula targets (see graph.json)"
        )

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
        defined_names=list(workbook_meta.get("defined_names") or []),
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
    counts = inventory_coverage_counts(document_rows)
    unmapped_series = sum(
        1
        for row in document_rows
        if row.kind in _SERIES_KINDS and row.disposition == "abstained"
    )
    stats = context_report_metrics(
        layout_rows=expected_rows,
        inventory_rows=len(document_rows),
        mapped=int(counts["mapped"]),
        abstained=int(counts["abstained"]),
        excluded=int(counts["excluded"]),
        abstract=int(counts["abstract"]),
        unmapped_series=unmapped_series,
    )
    stats["mapping_quality"] = assess_mapping_quality(
        document_rows,
        blocks,
        concepts=catalog,
        concept_accept_min=concept_accept_min,
    )
    return ContextDocument(
        schema_version=SCHEMA_VERSION,
        meta=meta,
        workbook=workbook,
        axes=axes,
        blocks=blocks,
        mapping_stats=MappingStats.model_validate(stats),
        graph=graph or GraphPointer(),
        warnings=warnings[:50],
    )




def _local_periods(
    views: list[tuple[str, list[AxisHeader], dict[str, str | None]]],
    axes_by_id: dict[str, ContextAxis],
) -> list[dict[str, object]]:
    """Column map for a block whose columns differ from the shared timeline."""
    if len(views) != 1:
        return []
    axis_id, headers, _phases = views[0]
    local = axes_by_id.get(axis_id)
    if local is None:
        return []
    shared = axes_by_id.get(local.timeline_id or local.id) or local
    canon = {period.period_key: period.col for period in shared.periods}
    if all(canon.get(header.period_key) == header.col for header in headers):
        return []
    return [
        {
            "col": header.col,
            "text": header.text,
            "role": header.role,
            "period_key": header.period_key,
        }
        for header in headers
    ]


def _block_views(
    sheet,
    block,
    axes_by_id: dict[str, ContextAxis],
) -> list[tuple[str, list[AxisHeader], dict[str, str | None]]]:
    kind = getattr(block, "kind", "timeline")
    if kind == "params":
        headers = [
            header
            for header in (block.axis.headers if block.axis is not None else [])
            if header.role in {"value", "scenario"}
        ]
        return [(block.block_id, headers, {})]
    views: list[tuple[str, list[AxisHeader], dict[str, str | None]]] = []
    published = list(block.timeline_ids)
    if len(published) != len(block.axis_ids):
        published = list(block.axis_ids)
    published_for = dict(zip(block.axis_ids, published, strict=False))
    for axis in axes_for(sheet, block):
        published_id = published_for.get(axis.id, axis.id)
        annotated = axes_by_id.get(published_id)
        by_key = {period.period_key: period for period in annotated.periods} if annotated else {}
        headers: list[AxisHeader] = []
        phases: dict[str, str | None] = {}
        for period in axis.periods:
            structural = period.period_key in {"actual", "plan", "total", "stub"}
            if period.role not in _SERIES_ROLES or structural:
                continue
            canon = by_key.get(period.period_key)
            role = canon.role if canon is not None and canon.role in _SERIES_ROLES else period.role
            headers.append(
                AxisHeader(
                    col=period.col,
                    text=period.text,
                    role=role,
                    period_key=period.period_key,
                )
            )
            phases[period.period_key] = getattr(canon, "phase", None) if canon is not None else None
        views.append((axis.id, headers, phases))
    return views


def _series_numbers(
    *,
    sheet_name: str,
    row_num: int,
    layout_row: LayoutRow,
    mapped: MappedRow | None,
    headers: list,
    value_headers: list,
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
    period_phases: dict[str, str | None],
    factor: int | None,
) -> tuple[list[str | None], list[ValueStatus], list[str | None]]:
    keep_cols = {header.col for header in value_headers}
    concept_id = mapped.concept_id if mapped else None
    gate = phase_gate(mapped.label if mapped else layout_row.label, concept_id)
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
        phase = period_phases.get(str(header.period_key))
        outside_phase = bool(gate and phase and gate != phase and text is None)
        structural = layout_row.kind not in _SERIES_KINDS and text is None
        statuses.append(value_status(text, applicable=not outside_phase and not structural))
    return values, statuses, normalize_series(values, factor)






def _roll_forward_time(relations: list[RowRelation]) -> dict[str, str]:
    """b/f is the opening, c/f the closing; the lines between them and their aliases move."""
    found: dict[str, str] = {}
    for rel in relations:
        if rel.kind != "roll_forward" or not rel.source_row_key or not rel.target_row_key:
            continue
        opening = _split_row_key(rel.source_row_key)
        closing = _split_row_key(rel.target_row_key)
        if opening is None or closing is None:
            continue
        if opening[0] != closing[0] or opening[2] != closing[2]:
            continue
        sheet, first, block_id = opening
        last = closing[1]
        found[rel.source_row_key] = "bop"
        found[rel.target_row_key] = "eop"
        for row in range(first + 1, last):
            found.setdefault(f"{sheet}|{row}|{block_id}", "flow")
    for rel in relations:
        if rel.kind != "alias" or not rel.source_row_key or not rel.target_row_key:
            continue
        if found.get(rel.source_row_key) == "flow":
            found.setdefault(rel.target_row_key, "flow")
    return found


def _split_row_key(row_key: str) -> tuple[str, int, str] | None:
    parts = row_key.split("|")
    if len(parts) != 3 or not parts[1].isdigit():
        return None
    return parts[0], int(parts[1]), parts[2]




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


def _neighbors(labels: list[str], index: int, span: int = 2) -> list[str]:
    start = max(0, index - span)
    end = min(len(labels), index + span + 1)
    return [label for i, label in enumerate(labels[start:end], start=start) if i != index]














def _row_feeds_cfads(
    sheet: str,
    row: int,
    dependents: dict[tuple[str, int], list[str]],
    concept_by_ref: dict[str, str | None],
) -> bool:
    for ref in dependents.get((sheet, row), []):
        if concept_by_ref.get(ref) == "cf.cfads":
            return True
        parsed = cell_ref_row(ref)
        if parsed and concept_by_ref.get(row_key_ref(*parsed)) == "cf.cfads":
            return True
    return False


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
            )
        )
    return out


def _is_static_row(
    kind: str,
    sheet: str,
    layout_row: LayoutRow,
    headers: list,
    by_addr: dict[tuple[str, int, int], dict],
) -> bool:
    """A params line or a scalar left of the ruler is a point value, not a period series."""
    if layout_row.kind not in {"fact", "helper", "flag"}:
        return False
    if kind == "params":
        return True
    if not any(item.role == "value" for item in layout_row.cells):
        return False
    return not any(
        (by_addr.get((sheet, layout_row.row, header.col)) or {}).get("cached_value")
        not in (None, "")
        for header in headers
    )








