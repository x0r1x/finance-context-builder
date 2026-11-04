from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

from finance_context.context.measure import Measure
from finance_context.context.series import format_number
from finance_context.excel.a1 import index_to_col
from finance_context.layout.cells import parse_number
from finance_context.models.context import (
    BlockRow,
    ContextAxis,
    ContextDocument,
    FinancialBlock,
    RowSeries,
)
from finance_context.store.fs import atomic_replace, atomic_write_bytes, temporary_path

_MD_ESCAPE = str.maketrans({"|": "\\|", "\n": " "})
# Bump when period-cell text changes, so a finished job rewrites context.md.
MARKDOWN_RENDERER = "structure-columns"
_RENDERER_STAMP = "context.md.renderer"
# Per-job sidecars written while rows are streamed. Render prepends the header.
MARKDOWN_BODY = ".context.md.body"
MARKDOWN_ROWS = ".context.rows"


def render_markdown(doc: ContextDocument) -> str:
    """Markdown with the same blocks, rows, and values as context.json."""
    lines = _preamble(doc, sum(len(block.rows) for block in doc.blocks))
    axes_by_id = {axis.id: axis for axis in doc.axes}
    for block in doc.blocks:
        lines.extend(_block_section(block, axes_by_id))
    return "\n".join(lines).rstrip() + "\n"


def _preamble(doc: ContextDocument, row_count: int) -> list[str]:
    lines: list[str] = [
        "# Financial context",
        "",
        f"- Schema: `{doc.schema_version}`",
        f"- Job: `{doc.meta.job_id}`",
        f"- Status: `{doc.meta.status}`",
        f"- Source: `{doc.meta.source_filename or 'n/a'}`",
        f"- Sheets: {doc.workbook.sheet_count}",
        f"- Cells: {doc.workbook.cell_count}",
        f"- Formulas: {doc.workbook.formula_count}",
        f"- Content completeness: 1.00 ({row_count}/{row_count} layout rows)",
        "",
    ]
    if doc.axes:
        lines.extend(_axes_section(doc.axes))
    if doc.warnings:
        lines.extend(["## Warnings", ""])
        for warning in doc.warnings:
            lines.append(f"- {_cell(warning)}")
        lines.append("")
    return lines


def commit_renderer_stamp(dest: Path) -> None:
    """Mark context.md current. Call this after a republish succeeds."""
    (dest / _RENDERER_STAMP).write_text(MARKDOWN_RENDERER + "\n", encoding="utf-8")


def write_context_markdown(dest: Path, doc: ContextDocument) -> None:
    """Write context.md and the renderer stamp. The workbook is not read.

    A build leaves the block bodies in a sidecar. The header, which needs the
    formula count and the warnings, is prepended here. A document loaded from
    disk has no sidecar and is rendered from its rows.
    """
    dest.mkdir(parents=True, exist_ok=True)
    body = dest / MARKDOWN_BODY
    if body.is_file():
        _publish_streamed_markdown(dest, doc, body)
        return
    atomic_write_bytes(dest / "context.md", render_markdown(doc).encode("utf-8"))
    commit_renderer_stamp(dest)


def _publish_streamed_markdown(dest: Path, doc: ContextDocument, body: Path) -> None:
    count_path = dest / MARKDOWN_ROWS
    row_count = int(count_path.read_text(encoding="utf-8")) if count_path.is_file() else 0
    header = "\n".join(_preamble(doc, row_count))
    target = dest / "context.md"
    tmp = temporary_path(target)
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as out:
            out.write(header)
            if body.stat().st_size:
                out.write("\n")
                with body.open(encoding="utf-8", newline="\n") as src:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        atomic_replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.unlink(missing_ok=True)
    body.unlink(missing_ok=True)
    count_path.unlink(missing_ok=True)
    commit_renderer_stamp(dest)


def refresh_context_markdown(dest: Path) -> bool:
    """Rewrite context.md from context.json when the renderer stamp is old.

    A matching stamp leaves the file alone. The stamp stays unchanged so a
    failed republish is tried again. Graph files and the workbook stay.
    """
    markdown_path = dest / "context.md"
    stamp_path = dest / _RENDERER_STAMP
    current = stamp_path.read_text(encoding="utf-8").strip() if stamp_path.is_file() else ""
    if current == MARKDOWN_RENDERER and markdown_path.is_file():
        return False
    context_path = dest / "context.json"
    if not context_path.is_file():
        return False
    doc = ContextDocument.model_validate_json(context_path.read_text(encoding="utf-8"))
    dest.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(markdown_path, render_markdown(doc).encode("utf-8"))
    return True


def _axes_section(axes: list[ContextAxis]) -> list[str]:
    groups: dict[str, list[ContextAxis]] = {}
    order: list[str] = []
    for axis in axes:
        key = axis.timeline_id or axis.id
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(axis)
    lines = ["## Axes", ""]
    for key in order:
        members = groups[key]
        if len(members) < 2:
            lines.extend(_one_axis(members[0]))
            continue
        canon = next((item for item in members if item.id == key), members[0])
        if _month_runs(canon) is not None:
            lines.extend(
                [
                    f"Grain: `{canon.grain or 'n/a'}`. Periods: {len(canon.periods)}.",
                    "",
                ]
            )
            lines.extend(_month_summary(canon))
            lines.append("")
        else:
            lines.extend(_shared_axis_table(members, canon))
    return lines


def _one_axis(axis: ContextAxis) -> list[str]:
    lines = [
        f"### `{axis.id}`",
        "",
        (f"Grain: `{axis.grain or 'n/a'}`. Sheet: `{axis.sheet}`. Periods: {len(axis.periods)}."),
        "",
    ]
    if _month_runs(axis) is not None:
        lines.extend(_month_summary(axis))
    else:
        lines.extend(_period_table(axis))
    lines.append("")
    return lines


def _shared_axis_table(members: list[ContextAxis], canon: ContextAxis) -> list[str]:
    """One grid: each axis is a row of its column letters, attributes follow once."""
    header = ["Axis", *[_cell(period.period_key) for period in canon.periods]]
    lines = [
        f"Grain: `{canon.grain or 'n/a'}`. Periods: {len(canon.periods)}.",
        "",
        _axis_row(header),
        _axis_row(["---"] * len(header)),
    ]
    for axis in members:
        letters = {period.period_key: _column_letter(period.col) for period in axis.periods}
        lines.append(
            _axis_row(
                [_cell(axis.id), *[letters.get(period.period_key, "") for period in canon.periods]]
            )
        )
    for attribute in _visible_attributes(canon):
        cells = [attribute, *[_period_cell(attribute, period) for period in canon.periods]]
        lines.append(_axis_row(cells))
    lines.append("")
    return lines


def _redundant_calendar(period) -> bool:
    calendar = period.calendar_year
    if not calendar:
        return True
    key = period.period_key
    return calendar == key or calendar == key[:4]


def _visible_attributes(axis: ContextAxis) -> list[str]:
    periods = axis.periods
    attributes: list[str] = []
    if any(period.start_date for period in periods):
        attributes.append("Start")
    if any(period.end_date for period in periods):
        attributes.append("End")
    if any(period.group_key for period in periods):
        attributes.append("Group")
    if any(period.calendar_year and not _redundant_calendar(period) for period in periods):
        attributes.append("Calendar")
    if any(period.flags for period in periods):
        attributes.append("Flags")
    return attributes


def _axis_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _period_table(axis: ContextAxis) -> list[str]:
    """One column per period; attributes are rows under the period keys."""
    header = [_cell(axis.id), *[_cell(period.period_key) for period in axis.periods]]
    lines = [_axis_row(header), _axis_row(["---"] * len(header))]
    for attribute in _visible_attributes(axis):
        cells = [attribute, *[_period_cell(attribute, period) for period in axis.periods]]
        lines.append(_axis_row(cells))
    return lines


def _period_cell(attribute: str, period) -> str:
    if attribute == "Start":
        return _cell(period.start_date or "")
    if attribute == "End":
        return _cell(period.end_date or "")
    if attribute == "Group":
        return _cell(period.group_key or "")
    if attribute == "Calendar":
        if _redundant_calendar(period):
            return ""
        return _cell(period.calendar_year or "")
    flags = ", ".join(name for name, on in period.flags.items() if on)
    return _cell(flags)


def _month_key(period_key: str) -> tuple[int, int] | None:
    if len(period_key) == 7 and period_key[4] == "-" and period_key[:4].isdigit():
        month = period_key[5:7]
        if month.isdigit():
            return int(period_key[:4]), int(month)
    return None


def _month_runs(axis: ContextAxis) -> list[tuple[str, list[str]]] | None:
    """Year lines for a month axis with no flags and a stable group inside each year."""
    if any(period.flags for period in axis.periods):
        return None
    parsed: list[tuple[str, str]] = []
    for period in axis.periods:
        if _month_key(period.period_key) is None:
            return None
        parsed.append((period.group_key or period.period_key[:4], period.period_key))
    runs: list[tuple[str, list[str]]] = []
    groups_in_year: dict[str, set[str | None]] = {}
    for period in axis.periods:
        year = period.period_key[:4]
        groups_in_year.setdefault(year, set()).add(period.group_key)
    if any(len(groups) > 1 for groups in groups_in_year.values()):
        return None
    for group, key in parsed:
        if runs and runs[-1][0] == group:
            runs[-1][1].append(key)
        else:
            runs.append((group, [key]))
    return runs


def _month_summary(axis: ContextAxis) -> list[str]:
    runs = _month_runs(axis) or []
    header = [_cell(axis.id), *[_cell(group) for group, _keys in runs]]
    spans = [keys[0] if keys[0] == keys[-1] else f"{keys[0]} .. {keys[-1]}" for _g, keys in runs]
    return [
        _axis_row(header),
        _axis_row(["---"] * len(header)),
        _axis_row(["Periods", *[_cell(span) for span in spans]]),
    ]


def _referenced_axes(
    block: FinancialBlock, axes_by_id: dict[str, ContextAxis]
) -> list[ContextAxis]:
    return [axes_by_id[axis_id] for axis_id in block.axis_ids if axis_id in axes_by_id]


def _block_intro(
    block: FinancialBlock, axes_by_id: dict[str, ContextAxis], row_count: int
) -> list[str]:
    referenced = _referenced_axes(block, axes_by_id)
    grain = f" grain={block.grain}" if block.grain else ""
    title = (
        f"## Parameters / {block.sheet}"
        if block.kind == "params"
        else f"## {block.sheet} / `{block.block_id}`"
    )
    if referenced:
        names = ", ".join(f"`{axis.id}`" for axis in referenced)
        timelines = list(
            dict.fromkeys(
                axis.timeline_id
                for axis in referenced
                if axis.timeline_id and axis.timeline_id != axis.id
            )
        )
        if timelines:
            shared = ", ".join(f"`{item}`" for item in timelines)
            summary = (
                f"Block: `{block.block_id}`. Kind: `{block.kind}`. "
                f"Axis: {names}. Timeline: {shared}. Rows: {row_count}."
            )
        else:
            summary = (
                f"Block: `{block.block_id}`. Kind: `{block.kind}`. "
                f"Axes: {names}. Rows: {row_count}."
            )
    else:
        summary = (
            f"Block: `{block.block_id}`. Kind: `{block.kind}`.{grain} "
            f"Periods: {len(block.periods)}. Rows: {row_count}."
        )
    return [title, "", summary, ""]


def _markdown_tables(
    block: FinancialBlock, axes_by_id: dict[str, ContextAxis]
) -> list[tuple[str | None, list[dict], str | None]]:
    """One ``(heading, period headers, axis id)`` per value table, in write order."""
    referenced = _referenced_axes(block, axes_by_id)
    if not referenced:
        return [(None, list(block.periods), None)]
    tables: list[tuple[str | None, list[dict], str | None]] = []
    multi = len(referenced) > 1
    for axis in referenced:
        if len(referenced) == 1 and block.periods:
            headers = list(block.periods)
        else:
            headers = [
                {"text": period.text, "period_key": period.period_key, "col": period.col}
                for period in axis.periods
            ]
        heading = f"### `{axis.id}`" if multi else None
        tables.append((heading, headers, axis.id))
    return tables


def _block_section(block: FinancialBlock, axes_by_id: dict[str, ContextAxis]) -> list[str]:
    lines = _block_intro(block, axes_by_id, len(block.rows))
    if not block.rows:
        return lines
    for heading, headers, axis_id in _markdown_tables(block, axes_by_id):
        if heading:
            lines.extend([heading, ""])
        lines.extend(_value_table(block.rows, headers, axis_id))
    if block.relations:
        lines.extend(_relations_section(block.relations))
    return lines


def _table_header(periods: list[dict]) -> list[str]:
    headers = [_period_label(item) for item in periods]
    cols = ["Label", "Row", "Path", "Kind", "Unit", "Formula", "Cells", *headers]
    return [
        "| " + " | ".join(_cell(col) for col in cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]


def _value_table(rows: list[BlockRow], periods: list[dict], axis_id: str | None) -> list[str]:
    lines = _table_header(periods)
    period_cols = {item.get("col") for item in periods}
    for row in rows:
        series = _series_for(row, axis_id)
        lines.append(_row_line(row, periods, series, period_cols))
    lines.append("")
    return lines


class MarkdownBody:
    """Block bodies for one job. Row lines sit in a per-axis temp file.

    ``extend`` matches ``"\\n".join``: a newline between lines, none after the
    last. Render prepends the header, which is not known until the row loop
    has finished.
    """

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / MARKDOWN_BODY
        self._count_path = directory / MARKDOWN_ROWS
        self._handle = self.path.open("w", encoding="utf-8", newline="\n")
        self._pending = False
        self.row_count = 0
        self._directory = directory

    def extend(self, lines: list[str]) -> None:
        for line in lines:
            if self._pending:
                self._handle.write("\n")
            self._handle.write(line)
            self._pending = True

    def extend_file(self, path: Path) -> None:
        with path.open(encoding="utf-8", newline="\n") as handle:
            for raw in handle:
                self.extend([raw[:-1] if raw.endswith("\n") else raw])

    def close(self) -> None:
        if self._handle.closed:
            return
        self._handle.flush()
        os.fsync(self._handle.fileno())
        self._handle.close()
        self._count_path.write_text(f"{self.row_count}\n", encoding="utf-8")

    def abort(self) -> None:
        if not self._handle.closed:
            self._handle.close()
        self.path.unlink(missing_ok=True)
        self._count_path.unlink(missing_ok=True)


class AxisRows:
    """One axis table's row lines. Several axes of one block each get a file."""

    def __init__(
        self,
        directory: Path,
        heading: str | None,
        headers: list[dict],
        axis_id: str | None,
    ) -> None:
        self.heading = heading
        self.headers = headers
        self.axis_id = axis_id
        self._period_cols = {item.get("col") for item in headers}
        descriptor, name = tempfile.mkstemp(prefix=".axis.", suffix=".md", dir=directory)
        os.close(descriptor)
        self.path = Path(name)
        self._handle = self.path.open("w", encoding="utf-8", newline="\n")
        self._pending = False

    def add(self, row: BlockRow) -> None:
        text = _row_line(
            row, self.headers, _series_for(row, self.axis_id), self._period_cols
        )
        if self._pending:
            self._handle.write("\n")
        self._handle.write(text)
        self._pending = True

    def assemble(self, body: MarkdownBody) -> None:
        if not self._handle.closed:
            self._handle.close()
        if self.heading:
            body.extend([self.heading, ""])
        body.extend(_table_header(self.headers))
        body.extend_file(self.path)
        body.extend([""])
        self.path.unlink(missing_ok=True)

    def discard(self) -> None:
        if not self._handle.closed:
            self._handle.close()
        self.path.unlink(missing_ok=True)


def _role_cells_label(row: BlockRow, period_cols: set) -> str:
    """Cells left of the ruler: `L total: -86400`, `G Start: 01.01.2024`.

    Units have their own column.
    """
    parts: list[str] = []
    for item in row.cells:
        if item.role == "unit" or item.col in period_cols or item.cached_value in (None, ""):
            continue
        letter = _column_letter(item.col)
        name = item.header or item.role
        parts.append(f"{letter} {name}: {item.cached_value}")
    return "; ".join(parts)


def _series_for(row: BlockRow, axis_id: str | None) -> RowSeries | None:
    if axis_id is None:
        return None
    return next((item for item in row.series if item.axis_id == axis_id), None)


def _row_line(
    row: BlockRow,
    periods: list[dict],
    series: RowSeries | None = None,
    period_cols: set | None = None,
) -> str:
    unit = _unit_label(row)
    source_values = series.values if series is not None else row.values
    source_statuses = series.value_statuses if series is not None else row.value_statuses
    source_normalized = series.normalized_values if series is not None else row.normalized_values
    formula = series.formula if series is not None and series.formula else row.formula
    values = list(source_values)
    statuses = list(source_statuses)
    normalized = list(source_normalized)
    if len(values) < len(periods):
        values.extend([None] * (len(periods) - len(values)))
    cells = [
        _cell(row.label),
        _cell(row.row_key),
        _cell(" / ".join(row.label_path)),
        _cell(row.kind),
        _cell(unit),
        _cell(formula or ""),
        _cell(_role_cells_label(row, period_cols or set())),
        *[
            _cell(
                _series_cell(
                    value,
                    _at(statuses, index),
                    _at(normalized, index),
                    percent=_is_percent_row(row),
                    addr=_cell_addr(row, periods[index]),
                )
            )
            for index, value in enumerate(values[: len(periods)])
        ],
    ]
    return "| " + " | ".join(cells) + " |"


def _unit_label(row: BlockRow) -> str:
    unit = next((cell.cached_value for cell in row.cells if cell.role == "unit"), None)
    if not unit:
        unit = (
            Measure(
                unit=row.hints.unit,
                currency=row.hints.currency,
                scale=row.hints.scale,
                per=row.hints.unit_per,
            ).display()
            or row.unit
            or row.hints.unit
            or ""
        )
    if row.scale_factor not in (None, 1):
        base = unit or "unit"
        return f"{base} ×{row.scale_factor}"
    return unit or ""


def _is_percent_row(row: BlockRow) -> bool:
    """A `%` unit cell, or a percent number format carried as the unit token."""
    return any(
        cell.role == "unit" and cell.cached_value and "%" in str(cell.cached_value)
        for cell in row.cells
    )


def _series_cell(
    value: str | None,
    status: str | None,
    normalized: str | None,
    *,
    percent: bool = False,
    addr: str = "",
) -> str:
    if status == "not_applicable":
        text = "n/a"
    elif status == "empty" or (status is None and value in (None, "")):
        text = "empty"
    else:
        text = "" if value is None else str(value)
        if status == "zero_explicit":
            text = text or "0"
        elif percent and text and "%" not in text:
            shown = _percent_amount(text)
            if shown is not None:
                text = f"{text} ({shown}%)"
            elif normalized and text and _differs(text, normalized):
                text = f"{text} ({normalized})"
        elif normalized and text and _differs(text, normalized):
            text = f"{text} ({normalized})"
    if addr:
        return f"{text} [{addr}]"
    return text


def _cell_addr(row: BlockRow, period: dict) -> str:
    """Graph and trace key for this period cell. The axis table is not a cell."""
    letter = _column_letter(period.get("col"))
    if not letter or not row.sheet or not row.row:
        return ""
    return f"{row.sheet}!{letter}{row.row}"


def _percent_amount(text: str) -> str | None:
    number = parse_number(text)
    if number is None:
        return None
    scaled = number * 100
    if not math.isfinite(scaled):
        return None
    return format_number(scaled)


def _differs(text: str, normalized: str) -> bool:
    """Show the base-unit amount only when scale changes it, not when formatting does."""
    value = parse_number(text)
    base = parse_number(normalized)
    if value is None or base is None:
        return normalized != text
    return abs(value - base) > 1e-9 * max(1.0, abs(value), abs(base))


def _at(items: list[str | None], index: int) -> str | None:
    if index >= len(items):
        return None
    return items[index]


def _relations_section(relations: list[dict]) -> list[str]:
    lines = [
        "Relations:",
        "",
        "| Kind | Source | Target | Members |",
        "| --- | --- | --- | --- |",
    ]
    for rel in relations:
        members = rel.get("member_row_keys") or []
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(rel.get("kind") or ""),
                    _cell(rel.get("source_row_key") or ""),
                    _cell(rel.get("target_row_key") or ""),
                    _cell(", ".join(str(key) for key in members)),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _period_label(header: dict) -> str:
    text = str(header.get("text") or "").strip()
    key = str(header.get("period_key") or "").strip()
    name = key if _prefers_period_key(text, key) else (text or key)
    letter = _column_letter(header.get("col"))
    if name and letter and name != letter:
        return f"{name} ({letter})"
    return name or letter


def _prefers_period_key(text: str, key: str) -> bool:
    if not key:
        return False
    if not text or text == key:
        return True
    if key[0] in "YQMP" and key[1:].isdigit():
        return True
    return key[:4].isdigit()


def _column_letter(col: object) -> str:
    if isinstance(col, int) and col > 0:
        return index_to_col(col)
    return ""


def _cell(value: object) -> str:
    return str(value).translate(_MD_ESCAPE).strip()
