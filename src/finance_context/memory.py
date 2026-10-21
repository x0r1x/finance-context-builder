"""Per-job RSS budget. The parent kills a child that exceeds it; compile refuses first."""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from finance_context.errors import ContextError
from finance_context.observability import log_event
from finance_context.settings import Settings
from finance_context.store.fs import read_parquet

# Same logger as stage_done and the layout step timings, so one formatter and one stream.
_LOGGER = logging.getLogger("finance_context.pipeline")

# A quarter of the container stays with the API process. The rest is split
# across the concurrent job slots.
_HEADROOM_DENOMINATOR = 4
# Above this, cgroup v1 is reporting "no limit" (page_counter max).
_UNLIMITED_BYTES = 1 << 60

# Profiled after the light compile objects. About 4 KiB of formula overhead
# plus 3 KiB for each expanded cell. A book of 40k formulas and 169k cell
# edges peaks near 580 MB and estimates near 650 MB.
BYTES_PER_FORMULA = 4 * 1024
BYTES_PER_EXPANDED_CELL = 3 * 1024

# A spawned child sits near 80 MiB after imports. A lower cap kills every book
# during parse, before any workbook data is the cause.
JOB_MEMORY_FLOOR_BYTES = 128 * 1024 * 1024
# What a 4 GiB container derives with the default two slots:
# (limit - limit // 4) // 2. Used when the cgroup reports no limit and
# JOB_MEMORY_BUDGET_BYTES is unset.
_CONTAINER_LIMIT_BYTES = 4 * 1024**3
DEFAULT_JOB_BUDGET_BYTES = (_CONTAINER_LIMIT_BYTES - _CONTAINER_LIMIT_BYTES // 4) // 2

_budget_warnings: set[str] = set()

MEMORY_ERROR_DETAIL = "This workbook ran out of memory while it was being read."

_PROC_CGROUP = Path("/proc/self/cgroup")
_SYS_CGROUP = Path("/sys/fs/cgroup")
_OOM_SCORE = Path("/proc/self/oom_score_adj")
_OOM_SCORE_ADJ = "1000"

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
    estimate_bytes: int


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


def prefer_oom_kill(path: Path = _OOM_SCORE) -> None:
    """Ask the kernel to reclaim this process first. Best effort, unprivileged."""
    try:
        path.write_text(_OOM_SCORE_ADJ, encoding="utf-8")
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


def resolve_job_budget_bytes(
    *,
    slots: int,
    override: int | None = None,
    limit_bytes: int | None = None,
    read_cgroup: bool = True,
) -> int | None:
    """RSS cap for one job. An explicit override wins; otherwise derive from the cgroup."""
    if override is not None and override > 0:
        return override
    limit = limit_bytes
    if limit is None and read_cgroup:
        limit = cgroup_memory_limit_bytes()
    if limit is None or limit <= 0:
        return None
    usable = limit - (limit // _HEADROOM_DENOMINATOR)
    slots = max(1, int(slots))
    return max(1, usable // slots)


def budget_for_settings(settings: Settings) -> int:
    budget = resolve_job_budget_bytes(
        slots=settings.max_concurrent_jobs,
        override=settings.job_memory_budget_bytes,
    )
    if budget is None:
        if "default" not in _budget_warnings:
            _budget_warnings.add("default")
            log_event(
                _LOGGER,
                logging.WARNING,
                "memory_budget_default",
                (
                    "cgroup memory limit is unset and JOB_MEMORY_BUDGET_BYTES is unset; "
                    f"using the default {_mib(DEFAULT_JOB_BUDGET_BYTES)} MiB job budget"
                ),
                budget_bytes=DEFAULT_JOB_BUDGET_BYTES,
            )
        budget = DEFAULT_JOB_BUDGET_BYTES
    return raise_budget_floor(budget)


def raise_budget_floor(budget: int | None) -> int | None:
    """Lift a cap that sits under the child baseline. ``None`` stays unlimited."""
    if budget is None or budget >= JOB_MEMORY_FLOOR_BYTES:
        return budget
    if "floor" not in _budget_warnings:
        _budget_warnings.add("floor")
        log_event(
            _LOGGER,
            logging.WARNING,
            "memory_budget_floor",
            (
                "job memory budget is below the child baseline; "
                f"using the {_mib(JOB_MEMORY_FLOOR_BYTES)} MiB floor"
            ),
            budget_bytes=JOB_MEMORY_FLOOR_BYTES,
            configured=budget,
        )
    return JOB_MEMORY_FLOOR_BYTES


def estimate_bytes(*, formulas: int, expanded_cells: int) -> int:
    return formulas * BYTES_PER_FORMULA + expanded_cells * BYTES_PER_EXPANDED_CELL


def too_large_detail(estimate: int, budget: int) -> str:
    return (
        "This workbook is too large for one job "
        f"(about {_mib(estimate)} MiB, limit {_mib(budget)} MiB)."
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
        targets.append(f"{owner}!{re.sub(r'\\s+', '', body)}")
    return targets


def _canonical_node(sheet: object, addr: object) -> str | None:
    from finance_context.excel.a1 import format_addr, parse_addr

    if not sheet or not addr:
        return None
    try:
        col, row = parse_addr(str(addr))
    except ValueError:
        return None
    return f"{sheet}!{format_addr(col, row)}"


def usage_for_book(dest_dir: Path):
    """Present cells for range clipping: parsed cells plus styled blanks."""
    # Imported lazily: formulas/__init__ pulls compile, which imports this module.
    from finance_context.formulas.csr import sheet_usage

    nodes: list[str] = []
    for name in ("cells.parquet", "cell_presence.parquet"):
        path = dest_dir / "raw" / name
        if not path.is_file():
            continue
        for row in read_parquet(path):
            node = _canonical_node(row.get("sheet"), row.get("addr"))
            if node is not None:
                nodes.append(node)
    return sheet_usage(nodes)


def assess_raw(dest_dir: Path) -> WorkbookEstimate:
    """Cheap size check from the parsed cell table, before formulas are compiled."""
    # Imported lazily: formulas/__init__ pulls compile, which imports this module.
    from finance_context.formulas.csr import expansion_count

    path = dest_dir / "raw" / "cells.parquet"
    if not path.is_file():
        return WorkbookEstimate(0, 0, 0, 0)
    rows = read_parquet(path)
    usage = usage_for_book(dest_dir)
    formulas = 0
    expanded = 0
    for row in rows:
        formula = row.get("formula_raw")
        if not formula:
            continue
        formulas += 1
        sheet = str(row.get("sheet") or "")
        for target in formula_targets(str(formula), sheet):
            expanded += expansion_count(target, usage=usage)
    return WorkbookEstimate(
        cells=len(rows),
        formulas=formulas,
        expanded_cells=expanded,
        estimate_bytes=estimate_bytes(formulas=formulas, expanded_cells=expanded),
    )


def check_budget(
    *,
    formulas: int,
    expanded_cells: int,
    budget: int | None,
    source: str,
) -> int:
    """Log the estimate and refuse when it is already over the job budget."""
    started = time.monotonic()
    estimate = estimate_bytes(formulas=formulas, expanded_cells=expanded_cells)
    duration_ms = int((time.monotonic() - started) * 1000)
    log_event(
        _LOGGER,
        logging.DEBUG,
        "memory_budget",
        "workbook estimate",
        duration_ms=duration_ms,
        stage=source,
        formulas=formulas,
        cell_edges=expanded_cells,
        estimate_bytes=estimate,
        budget_bytes=budget,
    )
    if budget is not None and estimate > budget:
        detail = too_large_detail(estimate, budget)
        log_event(
            _LOGGER,
            logging.DEBUG,
            "memory_refused",
            detail,
            duration_ms=duration_ms,
            stage=source,
            reason=detail,
            error_code="workbook_too_large",
            formulas=formulas,
            cell_edges=expanded_cells,
            estimate_bytes=estimate,
            budget_bytes=budget,
        )
        raise ContextError("workbook_too_large", detail)
    return estimate
