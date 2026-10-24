"""Shared book lock, busy signal, and in-place invalidation."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import pytest

from finance_context.app.artifacts import clear_downstream_artifacts, clear_shared_artifacts
from finance_context.app.pipeline import Pipeline, execute_job
from finance_context.app.publisher import stamped_stages
from finance_context.app.shared_cache import (
    SharedCacheBusy,
    invalidate_for_run,
    record_rebuild_stage,
    session_stale_stage,
    shared_book_lock,
    shared_stale_stage,
    write_shared_stamp,
)
from finance_context.errors import ContextError
from finance_context.settings import Settings
from finance_context.store.fs import file_lock


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def _hold(path: Path, started: threading.Event, release: threading.Event) -> None:
    with file_lock(path):
        started.set()
        assert release.wait(timeout=5)


def test_busy_lock_returns_retry_signal_without_deleting(tmp_path: Path) -> None:
    book = tmp_path / "book"
    book.mkdir()
    layout = book / "layout" / "layout.json"
    layout.parent.mkdir(parents=True, exist_ok=True)
    layout.write_text('{"sheets": []}\n', encoding="utf-8")
    (book / "source" / "source.xlsx").parent.mkdir(parents=True, exist_ok=True)
    (book / "source" / "source.xlsx").write_bytes(b"PK\x03\x04")
    write_shared_stamp(book)
    stamp = json.loads((book / "stages.json").read_text(encoding="utf-8"))
    stamp["publisher"] = "stale"
    (book / "stages.json").write_text(json.dumps(stamp), encoding="utf-8")
    settings = _settings(shared_cache_busy_retry_sec=30, shared_cache_busy_max_retries=120)
    started = threading.Event()
    release = threading.Event()
    worker = threading.Thread(target=_hold, args=(book / ".lock", started, release))
    worker.start()
    assert started.wait(timeout=5)
    try:
        t0 = time.monotonic()
        with pytest.raises(SharedCacheBusy) as caught:
            Pipeline(settings).run(tmp_path / "job", job_id="job-1", shared_dir=book)
        assert time.monotonic() - t0 < 1
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    err = caught.value
    assert err.job_id == "job-1"
    assert err.retry_after_sec == 30
    assert err.max_retries == 120
    assert layout.read_text(encoding="utf-8") == '{"sheets": []}\n'
    assert (book / ".lock").is_file()


def test_concurrent_invalidation_leaves_the_readers_file(tmp_path: Path) -> None:
    book = tmp_path / "book"
    ir = book / "ir"
    ir.mkdir(parents=True)
    layout = book / "layout" / "layout.json"
    layout.parent.mkdir(parents=True, exist_ok=True)
    layout.write_text('{"sheets": [{"name": "P&L"}]}\n', encoding="utf-8")
    (book / "source" / "source.xlsx").parent.mkdir(parents=True, exist_ok=True)
    (book / "source" / "source.xlsx").write_bytes(b"PK\x03\x04")
    (ir / "cells.parquet").write_bytes(b"ir-bytes")
    write_shared_stamp(book)
    stamp = json.loads((book / "stages.json").read_text(encoding="utf-8"))
    stamp["publisher"] = "stale"
    (book / "stages.json").write_text(json.dumps(stamp), encoding="utf-8")
    settings = _settings(shared_cache_busy_retry_sec=1, shared_cache_busy_max_retries=0)
    started = threading.Event()
    release = threading.Event()
    seen: list[str] = []

    def reader() -> None:
        with file_lock(book / ".lock"):
            started.set()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                seen.append(layout.read_text(encoding="utf-8"))
                time.sleep(0.01)
            release.wait(timeout=5)

    worker = threading.Thread(target=reader)
    worker.start()
    assert started.wait(timeout=5)
    with pytest.raises(SharedCacheBusy):
        with shared_book_lock(book, job_id="job-2", retry_after_sec=1, max_retries=0):
            invalidate_for_run(tmp_path / "job", book, shared=True, job_id="job-2")
    release.set()
    worker.join(timeout=5)
    assert seen
    assert set(seen) == {'{"sheets": [{"name": "P&L"}]}\n'}
    assert layout.is_file()
    dest = tmp_path / "job"
    dest.mkdir()
    with shared_book_lock(
        book,
        job_id="job-2",
        retry_after_sec=settings.shared_cache_busy_retry_sec,
        max_retries=settings.shared_cache_busy_max_retries,
    ):
        invalidate_for_run(dest, book, shared=True, job_id="job-2")
    assert not layout.exists()
    assert (ir / "cells.parquet").read_bytes() == b"ir-bytes"
    assert (book / ".lock").is_file()
    assert clear_shared_artifacts(book, "layout") is False


def test_execute_job_retries_then_runs(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO, logger="finance_context.pipeline")

    class _Pipeline:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, *_args: object, **_kwargs: object) -> str:
            self.calls += 1
            if self.calls < 3:
                raise SharedCacheBusy("job-3", retry_after_sec=0.01, max_retries=2)
            return "done"

    pipeline = _Pipeline()
    assert execute_job(pipeline, tmp_path, job_id="job-3") == "done"  # type: ignore[arg-type]
    assert pipeline.calls == 3
    events = [
        record
        for record in caplog.records
        if record.__dict__.get("event") == "shared_cache_busy_requeue"
    ]
    assert [record.__dict__.get("attempt") for record in events] == [1, 2]
    assert events[0].__dict__.get("retry_after_sec") == 0.01
    assert events[0].__dict__.get("max_retries") == 2
    assert events[0].__dict__.get("job_id") == "job-3"


def test_execute_job_exhausted_is_shared_cache_busy(tmp_path: Path) -> None:
    class _Pipeline:
        def run(self, *_args: object, **_kwargs: object) -> None:
            raise SharedCacheBusy("job-4", retry_after_sec=0.01, max_retries=0)

    with pytest.raises(ContextError) as caught:
        execute_job(_Pipeline(), tmp_path, job_id="job-4")  # type: ignore[arg-type]
    assert caught.value.code == "shared_cache_busy"


def test_recorded_publish_is_not_widened_to_layout(tmp_path: Path) -> None:
    """A wiped stage map must not turn a publish rebuild into a layout rebuild.

    POST drops meta.json and then records rebuild_from. The replacement meta has
    no stages map, and a missing map otherwise means layout.
    """
    dest = tmp_path / "job"
    dest.mkdir()
    (dest / "graph" / "graph.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "graph" / "graph.json").write_text("keep-graph", encoding="utf-8")
    (dest / "context" / "context.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "context" / "context.json").write_text("drop-context", encoding="utf-8")
    record_rebuild_stage(dest, "publish")
    stored = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert session_stale_stage(stored) == "publish"
    book = tmp_path / "book"
    book.mkdir()
    (book / "layout" / "layout.json").parent.mkdir(parents=True, exist_ok=True)
    (book / "layout" / "layout.json").write_text('{"sheets": []}\n', encoding="utf-8")
    (book / "source" / "source.xlsx").parent.mkdir(parents=True, exist_ok=True)
    (book / "source" / "source.xlsx").write_bytes(b"PK\x03\x04")
    write_shared_stamp(book)
    with shared_book_lock(book, job_id="job-pub", retry_after_sec=1, max_retries=0):
        invalidate_for_run(dest, book, shared=True, job_id="job-pub")
    assert (dest / "graph" / "graph.json").read_text(encoding="utf-8") == "keep-graph"
    assert not (dest / "context" / "context.json").exists()
    assert (book / "layout" / "layout.json").is_file()


def _book_with_body(tmp_path: Path) -> Path:
    book = tmp_path / "book"
    ir = book / "ir"
    raw = book / "raw"
    ir.mkdir(parents=True)
    raw.mkdir()
    (book / "source" / "source.xlsx").parent.mkdir(parents=True, exist_ok=True)
    (book / "source" / "source.xlsx").write_bytes(b"PK\x03\x04")
    (book / "layout" / "layout.json").parent.mkdir(parents=True, exist_ok=True)
    (book / "layout" / "layout.json").write_text('{"sheets": []}\n', encoding="utf-8")
    (raw / "workbook.json").write_text("{}\n", encoding="utf-8")
    (ir / "cells.parquet").write_bytes(b"old-ir")
    (ir / "compile.json").write_text('{"schema_id":"old"}\n', encoding="utf-8")
    return book


def test_missing_sidecar_compile_rebuild_drops_formula_ir(tmp_path: Path) -> None:
    book = _book_with_body(tmp_path)
    dest = tmp_path / "job"
    dest.mkdir()
    record_rebuild_stage(dest, "compile")
    with shared_book_lock(book, job_id="job-c", retry_after_sec=1, max_retries=0):
        invalidate_for_run(dest, book, shared=True, job_id="job-c")
    assert not (book / "ir" / "cells.parquet").exists()
    assert not (book / "raw" / "workbook.json").exists()
    assert not (book / "layout" / "layout.json").exists()
    assert (book / "source" / "source.xlsx").is_file()


def test_stamp_leaves_an_unrebuilt_compile_unverified(tmp_path: Path) -> None:
    book = _book_with_body(tmp_path)
    write_shared_stamp(book, rebuilt_from="layout", trusted=None, adopt=False)
    assert shared_stale_stage(book, adopt_current=False, recorded=None) == "compile"
    stamp = json.loads((book / "stages.json").read_text(encoding="utf-8"))
    assert stamp["stages"]["compile"] == "unverified"
    assert stamp["publisher"] == "partial"


def test_stamp_keeps_a_compile_hash_that_already_matched(tmp_path: Path) -> None:
    book = _book_with_body(tmp_path)
    current = stamped_stages()
    write_shared_stamp(
        book,
        rebuilt_from="layout",
        trusted={"compile": current["compile"]},
        adopt=False,
    )
    assert shared_stale_stage(book, adopt_current=False) is None


def test_queued_meta_keeps_generation_and_stage_map(tmp_path: Path) -> None:
    dest = tmp_path / "job"
    dest.mkdir()
    (dest / "context" / "context.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "context" / "context.json").write_text("{}", encoding="utf-8")
    (dest / "graph" / "graph.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "graph" / "graph.json").write_text("keep", encoding="utf-8")
    (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "meta" / "meta.json").write_text(
        json.dumps(
            {
                "generation": 5,
                "status": "succeeded",
                "source_filename": "model.xlsx",
                "warnings": ["w"],
                "stages": {"compile": "abc", "layout": "d", "graph": "e", "publish": "f"},
                "publisher": "old",
                "error": "process_lost",
            }
        ),
        encoding="utf-8",
    )
    clear_downstream_artifacts(dest, "publish", drop_meta=False)
    record_rebuild_stage(dest, "publish", generation=6)
    meta = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert meta["generation"] == 6
    assert meta["rebuild_from"] == "publish"
    assert meta["status"] == "queued"
    assert "error" not in meta
    assert meta["stages"]["compile"] == "abc"
    assert meta["source_filename"] == "model.xlsx"
    assert meta["warnings"] == ["w"]
    assert not (dest / "context" / "context.json").exists()
    assert (dest / "graph" / "graph.json").read_text(encoding="utf-8") == "keep"
    assert session_stale_stage(meta) == "publish"


def test_execute_job_child_deadline_already_elapsed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "finance_context.app.pipeline._child_watchdog_deadline",
        time.monotonic() - 1,
    )

    class _Pipeline:
        def __init__(self) -> None:
            self.settings = _settings(job_timeout_sec=3600)
            self.calls = 0

        def run(self, *_args: object, **_kwargs: object) -> None:
            self.calls += 1
            raise SharedCacheBusy("job-d", retry_after_sec=30, max_retries=120)

    pipeline = _Pipeline()
    started = time.monotonic()
    with pytest.raises(ContextError) as caught:
        execute_job(pipeline, tmp_path, job_id="job-d")  # type: ignore[arg-type]
    assert caught.value.code == "shared_cache_busy"
    assert time.monotonic() - started < 1
    assert pipeline.calls == 1


def test_arm_child_watchdog_counts_from_process_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_context.app import pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "process_mono_start", time.monotonic() - 100)
    pipeline_module._arm_child_watchdog(3600)
    try:
        deadline = pipeline_module._child_watchdog_deadline
        assert deadline is not None
        remaining = deadline - time.monotonic()
        assert 3490 < remaining < 3510
    finally:
        pipeline_module._child_watchdog_deadline = None


def test_run_job_process_clears_the_child_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from finance_context.app import pipeline as pipeline_module

    seen: dict[str, float | None] = {}

    def _run(*_args: object, **_kwargs: object) -> None:
        seen["deadline"] = pipeline_module._child_watchdog_deadline
        raise ContextError("shared_cache_busy", "shared book cache stayed busy")

    monkeypatch.setattr(pipeline_module, "execute_job", _run)
    monkeypatch.setattr(pipeline_module, "prefer_oom_kill", lambda **_kwargs: None)
    monkeypatch.setattr(pipeline_module, "configure_logging", lambda **_kwargs: None)
    monkeypatch.setattr(pipeline_module, "_install_stage_pause", lambda: None)
    pipeline_module.run_job_process(str(tmp_path), "job-clear")
    assert seen["deadline"] is not None
    assert pipeline_module._child_watchdog_deadline is None


def test_execute_job_busy_fails_before_job_timeout(tmp_path: Path) -> None:
    class _Pipeline:
        def __init__(self) -> None:
            self.settings = _settings(job_timeout_sec=0.05)
            self.calls = 0

        def run(self, *_args: object, **_kwargs: object) -> None:
            self.calls += 1
            raise SharedCacheBusy("job-t", retry_after_sec=30, max_retries=120)

    pipeline = _Pipeline()
    started = time.monotonic()
    with pytest.raises(ContextError) as caught:
        execute_job(pipeline, tmp_path, job_id="job-t")  # type: ignore[arg-type]
    assert caught.value.code == "shared_cache_busy"
    assert time.monotonic() - started < 1
    assert pipeline.calls == 1


def test_busy_settings_reject_bad_values() -> None:
    settings = _settings()
    assert settings.shared_cache_busy_retry_sec == 30
    assert settings.shared_cache_busy_max_retries == 120
    with pytest.raises(Exception, match="SHARED_CACHE_BUSY_RETRY_SEC"):
        _settings(shared_cache_busy_retry_sec=0)
    with pytest.raises(Exception, match="SHARED_CACHE_BUSY_MAX_RETRIES"):
        _settings(shared_cache_busy_max_retries=-1)
    assert _settings(shared_cache_busy_max_retries=0).shared_cache_busy_max_retries == 0
