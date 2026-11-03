"""Per-job RSS budget. The parent kills a child that exceeds it."""

from __future__ import annotations

import ctypes
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from finance_context.observability import log_event
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet_columns

# Same logger as stage_done and the layout step timings, so one formatter and one stream.
_LOGGER = logging.getLogger("finance_context.pipeline")

# Above this, cgroup v1 is reporting "no limit" (page_counter max).
_UNLIMITED_BYTES = 1 << 60

_budget_warnings: set[str] = set()
_logged_effective = False

MEMORY_ERROR_DETAIL = "This workbook ran out of memory while it was being read."


def out_of_memory(exc: BaseException) -> bool:
    """``MemoryError``, or DuckDB refusing an allocation over its ``memory_limit``.

    DuckDB raises its own ``OutOfMemoryException``, not ``MemoryError``.
    """
    if isinstance(exc, MemoryError):
        return True
    import duckdb

    return isinstance(exc, duckdb.OutOfMemoryException)

_PROC_CGROUP = Path("/proc/self/cgroup")
_SYS_CGROUP = Path("/sys/fs/cgroup")
_OOM_SCORE = Path("/proc/self/oom_score_adj")

_STRING = re.compile(r'"(?:[^"]|"")*"')
_FORMULA_REF = re.compile(
    r"""
    (?<![\w.])
    (?:
        '(?P<qsheet>(?:[^']|'')*)'!
        |
        (?P<sheet>[A-Za-z_][\w.]*)!
    )?
    (?:
        (?P<rect>\$?[A-Za-z]{1,3}\$?\d+\s*:\s*\$?[A-Za-z]{1,3}\$?\d+)
        |
        (?P<cols>\$?[A-Za-z]{1,3}\s*:\s*\$?[A-Za-z]{1,3})
        |
        (?P<rows>\$?\d+\s*:\s*\$?\d+)
        |
        (?P<cell>\$?[A-Za-z]{1,3}\$?\d+)
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class WorkbookEstimate:
    cells: int
    formulas: int
    expanded_cells: int
    present_cells: int


_trim_fn: object | None = None
_trim_resolved = False


def trim_process_heap() -> bool:
    """Ask glibc to return free arenas to the OS. Missing ``malloc_trim`` is a no-op."""
    global _trim_fn, _trim_resolved
    if not _trim_resolved:
        _trim_resolved = True
        try:
            libc = ctypes.CDLL(None)
            fn = getattr(libc, "malloc_trim", None)
        except (OSError, AttributeError):
            fn = None
        if fn is not None:
            fn.argtypes = [ctypes.c_size_t]
            fn.restype = ctypes.c_int
        _trim_fn = fn
    fn = _trim_fn
    if fn is None:
        return False
    try:
        return bool(fn(0))  # type: ignore[operator]
    except (OSError, AttributeError):
        return False


def rss_bytes() -> int:
    """Resident set of this process, from /proc/self/statm."""
    return _rss_from_statm(Path("/proc/self/statm")) or 0


def process_rss_bytes(pid: int) -> int | None:
    """Resident set of another process. None when it has already exited."""
    return _rss_from_statm(Path(f"/proc/{pid}/statm"))


def _rss_from_statm(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    parts = text.split()
    if len(parts) < 2:
        return None
    try:
        pages = int(parts[1])
    except ValueError:
        return None
    return pages * os.sysconf("SC_PAGE_SIZE")


def prefer_oom_kill(path: Path = _OOM_SCORE, *, score: int | None = None) -> None:
    """Ask the kernel to reclaim this process first. Best effort, unprivileged."""
    if score is None:
        score = Settings().job_oom_score_adj
    try:
        path.write_text(str(int(score)), encoding="utf-8")
    except OSError:
        return


def cgroup_memory_limit_bytes(
    proc_cgroup: Path = _PROC_CGROUP,
    sysfs: Path = _SYS_CGROUP,
) -> int | None:
    """Tightest finite memory limit on this process, or None when the cgroup is unlimited.

    cgroup v2 reports `memory.max` (`max` means unlimited). cgroup v1 reports
    `memory.limit_in_bytes`. A parent limit counts: the container dies at the
    smallest cap on the path.
    """
    try:
        text = proc_cgroup.read_text(encoding="utf-8")
    except OSError:
        return None
    found: list[int] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if line.startswith("0::"):
            rel = line.split("::", 1)[1].strip().strip("/")
            start = sysfs.joinpath(*rel.split("/")) if rel else sysfs
            found.extend(_limits_up(start, sysfs, "memory.max"))
            continue
        parts = line.split(":", 2)
        if len(parts) != 3 or "memory" not in parts[1].split(","):
            continue
        rel = parts[2].strip().strip("/")
        for base in (sysfs / "memory", sysfs):
            start = base.joinpath(*rel.split("/")) if rel else base
            stop = base if base == sysfs / "memory" else sysfs
            found.extend(_limits_up(start, stop, "memory.limit_in_bytes"))
    return min(found) if found else None


def _limits_up(start: Path, stop: Path, name: str) -> list[int]:
    found: list[int] = []
    cur = start
    while True:
        limit = _finite_limit(cur / name)
        if limit is not None:
            found.append(limit)
        if cur == stop:
            break
        parent = cur.parent
        if parent == cur:
            break
        # A start path outside `stop` is a single file, not a walk to the root.
        if stop not in cur.parents and cur != stop:
            break
        cur = parent
    return found


def _finite_limit(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text or text == "max":
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    if value <= 0 or value >= _UNLIMITED_BYTES:
        return None
    return value


def cgroup_leaf_dir(
    proc_cgroup: Path = _PROC_CGROUP,
    sysfs: Path = _SYS_CGROUP,
) -> Path | None:
    """Directory of this process's own cgroup, without walking parents."""
    try:
        text = proc_cgroup.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("0::"):
            rel = line.split("::", 1)[1].strip().strip("/")
            return sysfs.joinpath(*rel.split("/")) if rel else sysfs
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or "memory" not in parts[1].split(","):
            continue
        rel = parts[2].strip().strip("/")
        base = sysfs / "memory"
        return base.joinpath(*rel.split("/")) if rel else base
    return None


def cgroup_memory_anon_bytes(
    proc_cgroup: Path = _PROC_CGROUP,
    sysfs: Path = _SYS_CGROUP,
) -> int | None:
    """Anonymous memory charged to this cgroup, from memory.stat."""
    leaf = cgroup_leaf_dir(proc_cgroup, sysfs)
    if leaf is None:
        return None
    return _named_counter(leaf / "memory.stat", "anon")


def cgroup_oom_kill_count(
    proc_cgroup: Path = _PROC_CGROUP,
    sysfs: Path = _SYS_CGROUP,
) -> int | None:
    """How many times this cgroup's OOM killer has fired. None when unreadable."""
    leaf = cgroup_leaf_dir(proc_cgroup, sysfs)
    if leaf is None:
        return None
    events = _named_counter(leaf / "memory.events", "oom_kill")
    if events is not None:
        return events
    return _named_counter(leaf / "memory.oom_control", "oom_kill")


def _named_counter(path: Path, key: str) -> int | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == key:
            try:
                return int(parts[1])
            except ValueError:
                return None
    return None


def resolve_job_budget_bytes(
    *,
    slots: int,
    reserve_fraction: float,
    override: int | None = None,
    limit_bytes: int | None = None,
    read_cgroup: bool = True,
    ceiling: int | None = None,
) -> int | None:
    """RSS cap for one job.

    An explicit override wins and is not clipped by ``ceiling``. When the cgroup
    limit is known, the cap is that limit minus the API reserve. A positive
    ``ceiling`` then keeps the lesser of that remainder and ``ceiling``.
    ``slots`` does not divide the remainder.
    """
    del slots
    if override is not None and override > 0:
        return override
    limit = limit_bytes
    if limit is None and read_cgroup:
        limit = cgroup_memory_limit_bytes()
    if limit is None or limit <= 0:
        return None
    reserve = int(limit * reserve_fraction)
    usable = max(1, limit - reserve)
    if ceiling is not None and ceiling > 0:
        return min(usable, ceiling)
    return usable


def admission_blocked(
    *,
    enabled: bool,
    limit_bytes: int | None,
    anon_bytes: int | None,
    budget_bytes: int | None,
) -> bool:
    """True when the cgroup has no room for another job of this budget.

    Missing numbers, or a disabled check, mean the horse runs the job.
    macOS has no memory.stat, so the check stays quiet there.
    """
    if not enabled or budget_bytes is None or budget_bytes <= 0:
        return False
    if limit_bytes is None or anon_bytes is None:
        return False
    return limit_bytes - anon_bytes < budget_bytes


def budget_for_settings(settings: Settings) -> int:
    budget = resolve_job_budget_bytes(
        slots=settings.max_concurrent_jobs,
        reserve_fraction=settings.job_memory_api_reserve_fraction,
        override=settings.job_memory_budget_bytes,
        ceiling=settings.job_memory_default_budget_bytes,
    )
    if budget is None:
        budget = settings.job_memory_default_budget_bytes
        if "default" not in _budget_warnings:
            _budget_warnings.add("default")
            log_event(
                _LOGGER,
                logging.WARNING,
                "memory_budget_default",
                (
                    "cgroup memory limit is unset and JOB_MEMORY_BUDGET_BYTES is unset; "
                    f"using the default {_mib(budget)} MiB job budget"
                ),
                budget_bytes=budget,
            )
    _log_effective_memory(settings, budget)
    return budget


def _log_effective_memory(settings: Settings, budget: int) -> None:
    """One DEBUG memory_budget line with the knobs this process is using."""
    global _logged_effective
    if _logged_effective:
        return
    _logged_effective = True
    # 0 is the disabled arena. JsonFormatter drops None, which would hide the knob.
    arena = settings.job_malloc_arena_max if settings.job_malloc_arena_max is not None else 0
    log_event(
        _LOGGER,
        logging.DEBUG,
        "memory_budget",
        "effective memory settings",
        api_reserve_fraction=settings.job_memory_api_reserve_fraction,
        default_budget_bytes=settings.job_memory_default_budget_bytes,
        watch_interval_sec=settings.job_memory_watch_interval_sec,
        range_expand_cap=settings.range_expand_cap,
        oom_score_adj=settings.job_oom_score_adj,
        budget_bytes=budget,
        parse_streaming=settings.parse_streaming,
        json_stream=settings.json_stream,
        parquet_row_group_rows=settings.parquet_row_group_rows,
        duckdb_memory_limit=settings.duckdb_memory_limit,
        duckdb_max_temp_directory_size=settings.duckdb_max_temp_directory_size,
        compile_string_pool=settings.compile_string_pool,
        compile_ast_zlib=settings.compile_ast_zlib,
        graph_stream=settings.graph_stream,
        layout_columnar_facts=settings.layout_columnar_facts,
        malloc_arena_max=arena,
        malloc_trim=settings.job_malloc_trim,
        retry_after_sec=settings.shared_cache_busy_retry_sec,
        max_retries=settings.shared_cache_busy_max_retries,
    )


def over_rss_detail(rss: int, budget: int) -> str:
    return (
        "This workbook used more memory than one job is allowed "
        f"({_mib(rss)} MiB resident, limit {_mib(budget)} MiB)."
    )


def _mib(value: int) -> int:
    unit = 1024 * 1024
    return (value + unit - 1) // unit


def formula_targets(formula: str, sheet: str) -> list[str]:
    """A1 targets named by a formula, without parsing it. Sheet-local refs use `sheet`."""
    text = _STRING.sub("", formula)
    if text.startswith("="):
        text = text[1:]
    targets: list[str] = []
    for match in _FORMULA_REF.finditer(text):
        if match.group("qsheet") is not None:
            owner = match.group("qsheet").replace("''", "'")
        elif match.group("sheet"):
            owner = match.group("sheet")
        else:
            owner = sheet
        body = (
            match.group("rect")
            or match.group("cols")
            or match.group("rows")
            or match.group("cell")
        )
        if not body or not owner:
            continue
        targets.append(f"{owner}!{re.sub(r'\s+', '', body)}")
    return targets


class PresenceIndex:
    """Column runs for value cells and for styled blanks. One index for both."""

    def __init__(self) -> None:
        self.populated: dict[str, dict[int, tuple[tuple[int, int], ...]]] = {}
        self.blanks: dict[str, dict[int, tuple[tuple[int, int], ...]]] = {}

    def __bool__(self) -> bool:
        return bool(self.populated) or bool(self.blanks)

    def get(self, target: str, default: str | None = None) -> str | None:
        try:
            sheet, col, row = _target_cell(str(target))
        except ValueError:
            return default
        from finance_context.formulas.csr import _span_has

        if _span_has(self.populated.get(sheet, {}).get(col, ()), row):
            return "populated"
        if _span_has(self.blanks.get(sheet, {}).get(col, ()), row):
            return "styled_blank"
        return default

    def spans(self):
        """``(sheet, col, row_start, row_end)`` runs. Not one tuple per cell."""
        for kind in (self.populated, self.blanks):
            for sheet, columns in kind.items():
                for col, runs in columns.items():
                    for start, end in runs:
                        yield sheet, col, start, end


def _target_cell(target: str) -> tuple[str, int, int]:
    """Sheet, column and row of a formula target.

    ``'Data!B1`` is a sheet whose name starts with a quote, not an Excel quote.
    """
    from finance_context.excel.a1 import parse_addr
    from finance_context.formulas.csr import split_sheet_ref

    try:
        sheet, body = split_sheet_ref(target)
    except ValueError:
        if "!" not in target:
            raise
        sheet, body = target.rsplit("!", 1)
    col, row = parse_addr(body)
    return sheet, col, row


def _touch_run(
    open_runs: dict[tuple[str, int], list[int]],
    closed: dict[str, dict[int, list[tuple[int, int]]]],
    sheet: str,
    col: int,
    start: int,
    end: int,
) -> None:
    if start > end:
        start, end = end, start
    key = (sheet, col)
    run = open_runs.get(key)
    if run is not None and start <= run[1] + 1 and end + 1 >= run[0]:
        run[0] = min(run[0], start)
        run[1] = max(run[1], end)
        return
    if run is not None:
        closed.setdefault(sheet, {}).setdefault(col, []).append((run[0], run[1]))
    open_runs[key] = [start, end]


def _close_runs(
    open_runs: dict[tuple[str, int], list[int]],
    closed: dict[str, dict[int, list[tuple[int, int]]]],
) -> dict[str, dict[int, tuple[tuple[int, int], ...]]]:
    from finance_context.formulas.csr import _merge_spans

    for (sheet, col), run in open_runs.items():
        closed.setdefault(sheet, {}).setdefault(col, []).append((run[0], run[1]))
    return {
        sheet: {col: _merge_spans(items) for col, items in columns.items()}
        for sheet, columns in closed.items()
    }


def load_presence(path: Path, *, pool: dict[str, str] | None = None) -> PresenceIndex:
    """Read presence by row group. A file without ``row_end`` is one cell per row."""
    del pool
    index = PresenceIndex()
    if not path.is_file():
        return index
    import pyarrow.parquet as pq

    from finance_context.excel.a1 import parse_addr

    schema = pq.read_schema(path)
    names = ["sheet", "addr", "presence"]
    has_end = "row_end" in schema.names
    if has_end:
        names.append("row_end")
    values: dict[tuple[str, int], list[int]] = {}
    blanks: dict[tuple[str, int], list[int]] = {}
    closed_values: dict[str, dict[int, list[tuple[int, int]]]] = {}
    closed_blanks: dict[str, dict[int, list[tuple[int, int]]]] = {}
    for batch in pq.ParquetFile(path).iter_batches(columns=names, batch_size=65536):
        sheets = batch.column(0).to_pylist()
        addrs = batch.column(1).to_pylist()
        kinds = batch.column(2).to_pylist()
        ends = batch.column(3).to_pylist() if has_end else None
        for index_row, sheet in enumerate(sheets):
            try:
                col, row = parse_addr(str(addrs[index_row]))
            except ValueError:
                continue
            end = row
            if ends is not None and ends[index_row] is not None:
                end = int(ends[index_row])
            sheet_name = "" if sheet is None else str(sheet)
            if str(kinds[index_row]) == "populated":
                _touch_run(values, closed_values, sheet_name, col, row, end)
            else:
                _touch_run(blanks, closed_blanks, sheet_name, col, row, end)
    index.populated = _close_runs(values, closed_values)
    index.blanks = _close_runs(blanks, closed_blanks)
    return index


def usage_from_presence(presence: PresenceIndex):
    """Sheet index from a presence load. Does not read the file again."""
    from finance_context.formulas.csr import sheet_usage_spans

    return sheet_usage_spans(presence.spans())


def usage_for_book(dest_dir: Path):
    """Present cells for range clipping: parsed cells plus styled blanks.

    Presence already includes populated cells, so cells.parquet is not read again.
    """
    path = dest_dir / "raw" / "cell_presence.parquet"
    if not path.is_file():
        return usage_from_presence(PresenceIndex())
    return usage_from_presence(load_presence(path))


def assess_raw(dest_dir: Path, usage: object | None = None) -> WorkbookEstimate:
    """Edge counts from the same parsed edges compile expands, before that expansion."""
    # Imported lazily: formulas/__init__ pulls compile, which imports this module.
    from finance_context.formulas.csr import (
        SheetUsage,
        expanded_edge_count,
        present_cell_count,
    )
    from finance_context.formulas.engine import FormulaEngine

    path = dest_dir / "raw" / "cells.parquet"
    if not path.is_file():
        return WorkbookEstimate(0, 0, 0, 0)
    rows = read_parquet_columns(path, ("sheet", "addr", "formula_raw"))
    if usage is None:
        usage = usage_for_book(dest_dir)
    locale = None
    meta_path = dest_dir / "raw" / "workbook.json"
    if meta_path.is_file():
        locale = json.loads(meta_path.read_text(encoding="utf-8")).get("locale_hint")
    engine = FormulaEngine(locale_hint=locale if isinstance(locale, str) else None)
    formulas = 0
    expanded = 0
    for sheet, addr, formula in rows:
        if not formula:
            continue
        formulas += 1
        parsed = engine.parse(str(formula), sheet=str(sheet or ""), addr=str(addr or "A1"))
        expanded += expanded_edge_count(parsed.edges, usage=usage)  # type: ignore[arg-type]
    present = present_cell_count(usage) if isinstance(usage, SheetUsage) else 0
    return WorkbookEstimate(
        cells=len(rows),
        formulas=formulas,
        expanded_cells=expanded,
        present_cells=present,
    )
