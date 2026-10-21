from __future__ import annotations

import io
import json
import logging
import multiprocessing
import os
from pathlib import Path

from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context import memory
from finance_context.app.pipeline import _timed, run_job_process
from finance_context.errors import ContextError
from finance_context.excel import parse_workbook
from finance_context.formulas.csr import RANGE_EXPAND_CAP, expand_range, expansion_count
from finance_context.memory import (
    BYTES_PER_EXPANDED_CELL,
    BYTES_PER_FORMULA,
    DEFAULT_JOB_BUDGET_BYTES,
    ESTIMATE_BASE_BYTES,
    JOB_MEMORY_FLOOR_BYTES,
    assess_raw,
    cgroup_memory_limit_bytes,
    check_budget,
    estimate_bytes,
    formula_targets,
    prefer_oom_kill,
    resolve_job_budget_bytes,
    unusable_cap_detail,
)
from finance_context.observability import JsonFormatter
from finance_context.settings import Settings
from finance_context.store.paths import session_job_dir, shared_book_dir


def test_arrow_pool_defaults_to_system() -> None:
    assert os.environ.get("ARROW_DEFAULT_MEMORY_POOL") == "system"


def test_expansion_count_matches_expand_range_including_the_cap() -> None:
    small = "Sheet1!A1:B2"
    assert expansion_count(small) == len(expand_range(small)[0]) == 4
    wide = "Sheet1!B1:B2500"
    cells, truncated = expand_range(wide)
    assert truncated is True
    assert len(cells) == RANGE_EXPAND_CAP
    assert expansion_count(wide) == RANGE_EXPAND_CAP
    assert expansion_count("Data!$A:$A") == RANGE_EXPAND_CAP
    assert expansion_count("Data!1:1") == RANGE_EXPAND_CAP


def test_formula_targets_count_whole_columns_and_skip_strings() -> None:
    formula = '=SUMIF(Data!$A:$A,$A2,Data!C:C)&"A1:A9"'
    targets = formula_targets(formula, "Report")
    assert targets == ["Data!$A:$A", "Report!$A2", "Data!C:C"]
    assert sum(expansion_count(target) for target in targets) == RANGE_EXPAND_CAP * 2 + 1
    assert formula_targets("=SUM(A1 : A5000)", "Report") == ["Report!A1:A5000"]


def test_budget_reserves_a_quarter_and_splits_the_rest() -> None:
    limit = 4_000_000_000
    budget = resolve_job_budget_bytes(slots=2, limit_bytes=limit, read_cgroup=False)
    assert budget == (limit - limit // 4) // 2


def test_budget_override_is_the_per_job_cap() -> None:
    budget = resolve_job_budget_bytes(
        slots=2,
        override=1234,
        limit_bytes=4_000_000_000,
        read_cgroup=False,
    )
    assert budget == 1234


def test_unlimited_cgroup_has_no_budget() -> None:
    assert resolve_job_budget_bytes(slots=2, limit_bytes=None, read_cgroup=False) is None


def test_budget_below_the_child_baseline_is_not_raised(monkeypatch, caplog) -> None:
    memory._budget_warnings.clear()
    caplog.set_level(logging.WARNING, logger="finance_context.pipeline")
    tiny = 64 * 1024 * 1024
    monkeypatch.delenv("JOB_MEMORY_BUDGET_BYTES", raising=False)
    settings = Settings(_env_file=None, max_concurrent_jobs=2, job_memory_budget_bytes=tiny)
    assert memory.budget_for_settings(settings) == tiny
    assert memory.budget_for_settings(settings) == tiny
    assert unusable_cap_detail(settings) is not None
    warnings = [record for record in caplog.records if record.event == "memory_budget_floor"]
    assert len(warnings) == 1
    assert warnings[0].configured == tiny
    assert warnings[0].budget_bytes == tiny

    memory._budget_warnings.clear()
    monkeypatch.setattr(memory, "cgroup_memory_limit_bytes", lambda *_a, **_k: 256 * 1024 * 1024)
    derived = Settings(_env_file=None, max_concurrent_jobs=2, job_memory_budget_bytes=None)
    share = (256 * 1024 * 1024 - (256 * 1024 * 1024) // 4) // 2
    assert share < JOB_MEMORY_FLOOR_BYTES
    assert memory.budget_for_settings(derived) == share
    assert unusable_cap_detail(derived) is None


def test_unlimited_cgroup_uses_the_default_budget(monkeypatch, caplog) -> None:
    memory._budget_warnings.clear()
    monkeypatch.delenv("JOB_MEMORY_BUDGET_BYTES", raising=False)
    monkeypatch.setattr(memory, "cgroup_memory_limit_bytes", lambda *_args, **_kwargs: None)
    caplog.set_level(logging.WARNING, logger="finance_context.pipeline")
    settings = Settings(_env_file=None, max_concurrent_jobs=2)
    assert memory.budget_for_settings(settings) == DEFAULT_JOB_BUDGET_BYTES
    assert memory.budget_for_settings(settings) == DEFAULT_JOB_BUDGET_BYTES
    warnings = [record for record in caplog.records if record.event == "memory_budget_default"]
    assert len(warnings) == 1
    assert warnings[0].budget_bytes == DEFAULT_JOB_BUDGET_BYTES
    assert DEFAULT_JOB_BUDGET_BYTES == (4 * 1024**3 - (4 * 1024**3) // 4) // 2


def test_settings_blank_budget_derives(monkeypatch) -> None:
    monkeypatch.delenv("JOB_MEMORY_BUDGET_BYTES", raising=False)
    settings = Settings(_env_file=None, job_memory_budget_bytes="")
    assert settings.job_memory_budget_bytes is None


def test_cgroup_v2_uses_the_tightest_memory_max(tmp_path: Path) -> None:
    sysfs = tmp_path / "sys"
    pod = sysfs / "kubepods" / "pod"
    pod.mkdir(parents=True)
    (sysfs / "memory.max").write_text("max\n", encoding="utf-8")
    (sysfs / "kubepods" / "memory.max").write_text("9000\n", encoding="utf-8")
    (pod / "memory.max").write_text("max\n", encoding="utf-8")
    proc = tmp_path / "cgroup"
    proc.write_text("0::/kubepods/pod\n", encoding="utf-8")
    assert cgroup_memory_limit_bytes(proc, sysfs) == 9000


def test_cgroup_v1_ignores_the_unlimited_sentinel(tmp_path: Path) -> None:
    sysfs = tmp_path / "sys"
    slot = sysfs / "memory" / "docker" / "abc"
    slot.mkdir(parents=True)
    (slot / "memory.limit_in_bytes").write_text(str(1 << 62), encoding="utf-8")
    (sysfs / "memory" / "memory.limit_in_bytes").write_text("4000\n", encoding="utf-8")
    proc = tmp_path / "cgroup"
    proc.write_text("4:memory:/docker/abc\n", encoding="utf-8")
    assert cgroup_memory_limit_bytes(proc, sysfs) == 4000


def test_estimate_is_linear_in_formulas_and_expanded_cells() -> None:
    assert estimate_bytes(formulas=2, expanded_cells=3) == (
        ESTIMATE_BASE_BYTES + 2 * BYTES_PER_FORMULA + 3 * BYTES_PER_EXPANDED_CELL
    )


def test_stage_done_carries_rss_counts_and_budget() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("finance_context.pipeline")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        _timed(
            "compile",
            lambda: None,
            {"cells": 4, "formulas": 2, "estimate_bytes": 10, "budget_bytes": 20},
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    payloads = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    assert [row["event"] for row in payloads] == ["stage_done"]
    row = payloads[0]
    assert row["msg"] == "compile done"
    assert row["logger"] == "finance_context.pipeline"
    assert isinstance(row["duration_ms"], int)
    assert row["rss_in_mb"] >= 0
    assert row["rss_out_mb"] >= 0
    assert row["rss_peak_mb"] >= 0
    assert row["cells"] == 4
    assert row["formulas"] == 2
    assert row["estimate_bytes"] == 10
    assert row["budget_bytes"] == 20


def test_check_budget_refuses_with_a_human_message() -> None:
    try:
        check_budget(formulas=10, expanded_cells=10, budget=1, source="preflight")
    except ContextError as exc:
        assert exc.code == "workbook_too_large"
        assert "too large" in exc.detail
        assert "MiB" in exc.detail
    else:
        raise AssertionError("expected workbook_too_large")


def test_prefer_oom_kill_writes_the_score(tmp_path: Path) -> None:
    path = tmp_path / "oom_score_adj"
    prefer_oom_kill(path)
    assert path.read_text(encoding="utf-8") == "1000"


def _print_oom_score() -> None:
    prefer_oom_kill()
    score = Path("/proc/self/oom_score_adj").read_text(encoding="utf-8").strip()
    if score != "1000":
        raise SystemExit(score or "empty")


def test_oom_score_adj_is_writable_unprivileged() -> None:
    proc = multiprocessing.get_context("spawn").Process(target=_print_oom_score)
    proc.start()
    proc.join(10)
    assert proc.exitcode == 0


def _book(path: Path) -> None:
    build_xlsx(
        path,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item"),
                    CellSpec(addr="B1", value="2024"),
                    CellSpec(addr="A2", value="Revenue"),
                    CellSpec(addr="B2", value="10", formula="=B1"),
                ],
            )
        ],
        shared_strings=["Item", "2024", "Revenue"],
    )


def test_run_job_process_refuses_over_budget_without_a_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # The floor lifts a 1-byte cap, so the refusal has to sit above that floor.
    monkeypatch.setenv("JOB_MEMORY_BUDGET_BYTES", str(JOB_MEMORY_FLOOR_BYTES))
    monkeypatch.setattr(memory, "BYTES_PER_FORMULA", JOB_MEMORY_FLOOR_BYTES)
    monkeypatch.setattr(memory, "BYTES_PER_EXPANDED_CELL", JOB_MEMORY_FLOOR_BYTES)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_JSON", "true")
    job_id = "job"
    shared = shared_book_dir(tmp_path, job_id)
    shared.mkdir(parents=True)
    _book(shared / "source.xlsx")
    # run_job_process is in-process here, so put the agent's score back afterwards.
    previous = Path("/proc/self/oom_score_adj").read_text(encoding="utf-8")
    try:
        run_job_process(str(tmp_path), job_id)
    finally:
        Path("/proc/self/oom_score_adj").write_text(previous, encoding="utf-8")
    captured = capsys.readouterr()
    text = captured.out + captured.err
    meta = json.loads((session_job_dir(tmp_path, job_id) / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "failed"
    assert meta["error"] == "workbook_too_large"
    assert "too large" in meta["detail"]
    assert "Traceback" not in text
    assert "memory_budget" in text
    assert "memory_refused" in text
    assert "stage_profile" not in text


def test_memory_error_in_the_child_is_workbook_too_large(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("JOB_MEMORY_BUDGET_BYTES", str(8 * 1024**3))
    monkeypatch.setenv("LOG_JSON", "true")

    def boom(*_args, **_kwargs):
        raise MemoryError

    monkeypatch.setattr("finance_context.app.pipeline.compile_workbook", boom)
    job_id = "job"
    shared = shared_book_dir(tmp_path, job_id)
    shared.mkdir(parents=True)
    _book(shared / "source.xlsx")
    previous = Path("/proc/self/oom_score_adj").read_text(encoding="utf-8")
    try:
        run_job_process(str(tmp_path), job_id)
    finally:
        Path("/proc/self/oom_score_adj").write_text(previous, encoding="utf-8")
    captured = capsys.readouterr()
    text = captured.out + captured.err
    meta = json.loads((session_job_dir(tmp_path, job_id) / "meta.json").read_text(encoding="utf-8"))
    assert meta["error"] == "workbook_too_large"
    assert "ran out of memory" in meta["detail"]
    assert "Traceback" not in text


def test_job_body_returns_only_the_workbook_too_large_detail() -> None:
    from finance_context.api.routes.jobs import _job_body

    hidden = _job_body(
        "job",
        {
            "status": "failed",
            "error": "conversion_failed",
            "detail": "soffice exited: /tmp/secret/profile",
        },
    )
    assert "detail" not in hidden
    shown = _job_body(
        "job",
        {"status": "failed", "error": "workbook_too_large", "detail": "about 10 MiB"},
    )
    assert shown["detail"] == "about 10 MiB"


def test_configured_cap_below_the_baseline_refuses_before_parse(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("JOB_MEMORY_BUDGET_BYTES", str(64 * 1024 * 1024))
    monkeypatch.setenv("LOG_JSON", "true")
    job_id = "job"
    shared = shared_book_dir(tmp_path, job_id)
    shared.mkdir(parents=True)
    _book(shared / "source.xlsx")
    previous = Path("/proc/self/oom_score_adj").read_text(encoding="utf-8")
    try:
        run_job_process(str(tmp_path), job_id)
    finally:
        Path("/proc/self/oom_score_adj").write_text(previous, encoding="utf-8")
    meta = json.loads((session_job_dir(tmp_path, job_id) / "meta.json").read_text(encoding="utf-8"))
    assert meta["error"] == "workbook_too_large"
    assert "below" in meta["detail"]
    assert not (shared / "raw" / "workbook.json").is_file()


def test_preflight_follows_parsed_edges(tmp_path: Path, dest: Path) -> None:
    data = [CellSpec(addr=f"A{row}", value="1") for row in range(1, 41)]
    other = [CellSpec(addr=f"A{row}", value="1") for row in range(1, 4)]
    report = [
        CellSpec(addr="A1", value="1"),
        CellSpec(addr="B2", value="1", formula="SUM([1]Data!A:A)"),
        CellSpec(addr="B3", value="1", formula="SUM(Лист!A:A)"),
        CellSpec(addr="B4", value="1", formula="DEC2HEX(A1)"),
        CellSpec(addr="B5", value="1", formula="LOG10(A1)"),
        CellSpec(addr="B6", value="1", formula="SUM(1:10)"),
        CellSpec(addr="B7", value="1", formula="SUM(A1 : A5000)"),
    ]
    source = tmp_path / "grammar.xlsx"
    build_xlsx(
        source,
        sheets=[
            SheetSpec(name="Data", cells=data),
            SheetSpec(name="Лист", cells=other),
            SheetSpec(name="Report", cells=report),
        ],
    )
    parse_workbook(source, dest)
    estimate = assess_raw(dest)
    # External column is one edge. The Cyrillic column is its 3 present cells.
    # DEC2HEX and LOG10 are the real A1 only. SUM(1:10) and the spaced range
    # do not parse, so they add no cells.
    assert estimate.formulas == 6
    assert estimate.expanded_cells == 6


def test_assess_raw_counts_formulas(tmp_path: Path, dest: Path) -> None:
    source = tmp_path / "model.xlsx"
    _book(source)
    parse_workbook(source, dest)
    estimate = assess_raw(dest)
    assert estimate.formulas == 1
    assert estimate.expanded_cells >= 1
    assert estimate.estimate_bytes == estimate_bytes(
        formulas=estimate.formulas, expanded_cells=estimate.expanded_cells
    )
