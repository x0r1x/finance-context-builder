from __future__ import annotations

from finance_context.layout.axes import _PERIOD_ROLES, _STRUCTURAL_KEYS, _derived_axis
from finance_context.layout.cells import _cell_at, _text
from finance_context.layout.models import (
    AxisPeriod,
    Block,
    Layout,
    SheetLayout,
    TimeAxis,
)
from finance_context.layout.resolve import project_axis

_SHARE_GRAINS = frozenset({"model_year", "year"})


def link_workbook_timelines(layout: Layout, cells: list[dict], date1904: bool) -> None:
    """Same period-key sequence shares one published axis. Columns stay local."""
    by_addr = {
        (str(cell["sheet"]), int(cell["row"]), int(cell["col"])): cell for cell in cells
    }
    located: list[tuple[int, int, SheetLayout, TimeAxis]] = []
    for sheet_index, sheet in enumerate(layout.sheets):
        for axis_index, axis in enumerate(sheet.axes):
            located.append((sheet_index, axis_index, sheet, axis))
    groups: dict[tuple, list[_TimelineMember]] = {}
    for sheet_index, axis_index, sheet, axis in located:
        signature = _workbook_timeline_signature(axis)
        if signature is None:
            continue
        member = _TimelineMember(
            sheet_index=sheet_index,
            axis_index=axis_index,
            sheet=sheet,
            axis=axis,
            dates=_timeline_dates(axis),
            flags=_timeline_flags(sheet, axis, by_addr, date1904),
        )
        groups.setdefault(signature, []).append(member)
    alias: dict[str, str] = {}
    for members in groups.values():
        for bucket in _timeline_buckets(members):
            if len(bucket) < 2:
                continue
            canonical = max(bucket, key=_timeline_rank)
            for member in bucket:
                alias[member.axis.id] = canonical.axis.id
    for sheet in layout.sheets:
        for block in sheet.blocks:
            if getattr(block, "kind", "timeline") != "timeline" or not block.axis_ids:
                continue
            block.timeline_ids = [alias.get(axis_id, axis_id) for axis_id in block.axis_ids]


def _workbook_timeline_signature(axis: TimeAxis) -> tuple | None:
    if axis.grain not in _SHARE_GRAINS:
        return None
    periods = _timeline_periods(axis)
    if len(periods) < 3:
        return None
    return (
        axis.grain,
        tuple((period.period_key, period.role, period.group_key) for period in periods),
    )


def _timeline_periods(axis: TimeAxis) -> list[AxisPeriod]:
    return [
        period
        for period in axis.periods
        if period.role in _PERIOD_ROLES and period.period_key not in _STRUCTURAL_KEYS
    ]


def _timeline_dates(axis: TimeAxis) -> tuple[tuple[str | None, str | None], ...] | None:
    dates = tuple((period.start_date, period.end_date) for period in _timeline_periods(axis))
    if any(start or end for start, end in dates):
        return dates
    return None


def _timeline_flags(
    sheet: SheetLayout,
    axis: TimeAxis,
    by_addr: dict[tuple[str, int, int], dict],
    date1904: bool,
) -> tuple[tuple[str, tuple[bool, ...]], ...] | None:
    rows = [
        row
        for block in sheet.blocks
        if getattr(block, "kind", "timeline") == "timeline" and axis.id in block.axis_ids
        for row in block.rows
        if row.kind == "flag"
    ]
    if not rows:
        return None
    periods = _timeline_periods(axis)
    items: list[tuple[str, tuple[bool, ...]]] = []
    for row in rows:
        bits = tuple(
            _flag_bit(by_addr.get((sheet.name, row.row, period.col)), date1904)
            for period in periods
        )
        items.append((row.label, bits))
    return tuple(items)


def _timeline_buckets(members: list[_TimelineMember]) -> list[list[_TimelineMember]]:
    buckets: list[_TimelineBucket] = []
    for member in members:
        placed = False
        for bucket in buckets:
            if not _dates_compatible(bucket.dates, member.dates):
                continue
            if not _flags_compatible(bucket.flags or (), member.flags or ()):
                continue
            bucket.members.append(member)
            if bucket.dates is None:
                bucket.dates = member.dates
            if bucket.flags is None:
                bucket.flags = member.flags
            placed = True
            break
        if not placed:
            buckets.append(
                _TimelineBucket(dates=member.dates, flags=member.flags, members=[member])
            )
    return [bucket.members for bucket in buckets]


def _dates_compatible(
    left: tuple[tuple[str | None, str | None], ...] | None,
    right: tuple[tuple[str | None, str | None], ...] | None,
) -> bool:
    if left is None or right is None:
        return True
    return left == right


def _timeline_rank(member: _TimelineMember) -> tuple[bool, int, int, int]:
    flag_count = 0 if member.flags is None else len(member.flags)
    return (member.dates is not None, flag_count, -member.sheet_index, -member.axis_index)


class _TimelineMember:
    def __init__(
        self,
        *,
        sheet_index: int,
        axis_index: int,
        sheet: SheetLayout,
        axis: TimeAxis,
        dates: tuple[tuple[str | None, str | None], ...] | None,
        flags: tuple[tuple[str, tuple[bool, ...]], ...] | None,
    ) -> None:
        self.sheet_index = sheet_index
        self.axis_index = axis_index
        self.sheet = sheet
        self.axis = axis
        self.dates = dates
        self.flags = flags


class _TimelineBucket:
    def __init__(
        self,
        *,
        dates: tuple[tuple[str | None, str | None], ...] | None,
        flags: tuple[tuple[str, tuple[bool, ...]], ...] | None,
        members: list[_TimelineMember],
    ) -> None:
        self.dates = dates
        self.flags = flags
        self.members = members


def _axis_signature(axis: TimeAxis) -> tuple:
    return (
        axis.grain,
        tuple(
            (period.col, period.period_key, period.role, period.group_key)
            for period in axis.periods
        ),
    )


def _flag_bit(cell: dict | None, date1904: bool) -> bool:
    if cell is None:
        return False
    text = _text(cell, date1904)
    blob = (text or str(cell.get("cached_value") or "")).strip().replace(",", ".")
    if blob.lower() in {"1", "1.0", "true", "yes"}:
        return True
    try:
        return abs(float(blob) - 1.0) < 1e-9
    except ValueError:
        return False


def _flag_fingerprint(
    block: Block | None,
    axis: TimeAxis,
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> tuple:
    if block is None:
        return ()
    rows = [row for row in block.rows if row.kind == "flag"]
    if not rows:
        return ()
    items: list[tuple[str, tuple[bool, ...]]] = []
    for row in rows:
        bits = tuple(
            _flag_bit(_cell_at(by_row.get(row.row, []), period.col), date1904)
            for period in axis.periods
        )
        items.append((row.label, bits))
    return tuple(items)


def _flags_compatible(left: tuple, right: tuple) -> bool:
    if not left or not right:
        return True
    return left == right


def _share_repeated_axes(
    blocks: list[Block],
    axes: list[TimeAxis],
    by_row: dict[int, list[dict]],
    date1904: bool,
) -> tuple[list[Block], list[TimeAxis]]:
    """Later section headers that repeat an axis reference the first one."""
    owner: dict[str, Block] = {}
    for block in blocks:
        for axis_id in block.axis_ids:
            owner.setdefault(axis_id, block)
    buckets: dict[tuple, list[tuple[TimeAxis, tuple]]] = {}
    alias: dict[str, str] = {}
    kept: list[TimeAxis] = []
    for axis in axes:
        derived = next((other for other in kept if _derived_axis(axis, other, by_row)), None)
        if derived is not None:
            alias[axis.id] = derived.id
            continue
        signature = _axis_signature(axis)
        fingerprint = _flag_fingerprint(owner.get(axis.id), axis, by_row, date1904)
        match: TimeAxis | None = None
        for previous, previous_flags in buckets.get(signature, []):
            if _flags_compatible(previous_flags, fingerprint):
                match = previous
                if not previous_flags and fingerprint:
                    buckets[signature] = [
                        (item, fingerprint if item.id == previous.id else flags)
                        for item, flags in buckets[signature]
                    ]
                break
        if match is not None:
            alias[axis.id] = match.id
            continue
        buckets.setdefault(signature, []).append((axis, fingerprint))
        kept.append(axis)
    if not alias:
        return blocks, axes
    by_id = {axis.id: axis for axis in kept}
    for block in blocks:
        seen: list[str] = []
        for axis_id in block.axis_ids:
            mapped = alias.get(axis_id, axis_id)
            if mapped not in seen:
                seen.append(mapped)
        block.axis_ids = seen
        if len(seen) == 1 and seen[0] in by_id:
            block.axis = project_axis(by_id[seen[0]])
        elif len(seen) != 1:
            block.axis = None
    return blocks, kept
