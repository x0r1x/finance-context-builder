"""Shared book lock, busy signal, and in-place invalidation."""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import pytest

from finance_context.app.artifacts import clear_shared_artifacts
from finance_context.app.pipeline import Pipeline, execute_job
from finance_context.app.shared_cache import (
    SharedCacheBusy,
    invalidate_for_run,
    shared_book_lock,
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
    layout = book / "layout.json"
    layout.write_text('{"sheets": []}\n', encoding="utf-8")
    (book / "source.xlsx").write_bytes(b"PK\x03\x04")
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
    layout = book / "layout.json"
    layout.write_text('{"sheets": [{"name": "P&L"}]}\n', encoding="utf-8")
    (book / "source.xlsx").write_bytes(b"PK\x03\x04")
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


def test_busy_settings_reject_bad_values() -> None:
    settings = _settings()
    assert settings.shared_cache_busy_retry_sec == 30
    assert settings.shared_cache_busy_max_retries == 120
    with pytest.raises(Exception, match="SHARED_CACHE_BUSY_RETRY_SEC"):
        _settings(shared_cache_busy_retry_sec=0)
    with pytest.raises(Exception, match="SHARED_CACHE_BUSY_MAX_RETRIES"):
        _settings(shared_cache_busy_max_retries=-1)
    assert _settings(shared_cache_busy_max_retries=0).shared_cache_busy_max_retries == 0
