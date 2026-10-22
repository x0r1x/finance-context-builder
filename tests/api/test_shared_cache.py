"""POST must not delete another session's shared book cache."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.api.conftest import _serving, _stop_workers, _upload, _wait_for_terminal, _xlsx

from finance_context.jobqueue.spawn import run_lock_busy
from finance_context.settings import Settings
from finance_context.store.fs import file_lock


@contextmanager
def _session(root: Path, session_id: str) -> Iterator[TestClient]:
    settings = Settings(
        data_dir=root,
        session_id=session_id,
        max_upload_bytes=1024 * 1024,
        _env_file=None,
    )
    with _serving(settings) as client:
        yield client


def _wait_child_exit(dest: Path) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not run_lock_busy(dest):
            return
        time.sleep(0.05)
    raise AssertionError("job child did not release the run lock")


def test_post_under_held_shared_lock_keeps_layout(tmp_path: Path) -> None:
    """Beta's POST must not unlink a layout another session already published.

    The audit held ``shared/books/{job_id}/.lock`` and watched beta's POST delete
    ``layout.json``. The file and alpha's finished context stay in place.
    """
    root = tmp_path / "data"
    source = _xlsx(tmp_path / "model.xlsx")
    with _session(root, "alpha") as alpha:
        created = _upload(alpha, source)
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        assert _wait_for_terminal(alpha, job_id)["status"] == "succeeded"
        book = root / "shared" / "books" / job_id
        layout = book / "layout.json"
        layout_bytes = layout.read_bytes()
        alpha_context = (
            root / "sessions" / "alpha" / "jobs" / job_id / "context.json"
        ).read_bytes()
        held = threading.Event()
        release = threading.Event()

        def _hold_shared_lock() -> None:
            with file_lock(book / ".lock"):
                held.set()
                assert release.wait(timeout=30)

        worker = threading.Thread(target=_hold_shared_lock, name="shared-lock")
        worker.start()
        assert held.wait(timeout=5)
        try:
            with _session(root, "beta") as beta:
                posted = _upload(beta, source)
                assert posted.status_code == 202
                assert layout.is_file()
                assert layout.read_bytes() == layout_bytes
        finally:
            release.set()
            worker.join(timeout=5)
        assert not worker.is_alive()
        assert layout.read_bytes() == layout_bytes
        assert (
            root / "sessions" / "alpha" / "jobs" / job_id / "context.json"
        ).read_bytes() == alpha_context
        body = alpha.get(f"/v1/context-jobs/{job_id}").json()
        assert body["status"] == "succeeded"
        assert json.loads(layout.read_bytes())


def _wait_stage(dest: Path, stage: str) -> bool:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        path = dest / "meta.json"
        if path.is_file():
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = None
            running = (
                isinstance(meta, dict)
                and meta.get("stage") == stage
                and meta.get("status") == "running"
            )
            if running:
                return True
        time.sleep(0.05)
    return False


def test_post_other_session_keeps_shared_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Alpha is inside the layout-already-exists read. Beta's POST must not delete it."""
    monkeypatch.setenv("SHARED_CACHE_BUSY_RETRY_SEC", "0.05")
    monkeypatch.setenv("SHARED_CACHE_BUSY_MAX_RETRIES", "400")
    root = tmp_path / "data"
    source = _xlsx(tmp_path / "reuse.xlsx")
    pause = tmp_path / "pause-layout"
    with _session(root, "alpha") as alpha, _session(root, "beta") as beta:
        created = _upload(alpha, source)
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        assert _wait_for_terminal(alpha, job_id)["status"] == "succeeded"
        # Succeeded is visible before the child releases the book lock. A second
        # POST while that process is alive is treated as the same run.
        dest = root / "sessions" / "alpha" / "jobs" / job_id
        _wait_child_exit(dest)
        # The first worker copied its environ at start. Stop it so the reuse
        # POST starts a worker that can see the layout pause.
        _stop_workers(alpha)
        alpha.app.state.test_workers.clear()
        book = root / "shared" / "books" / job_id
        layout = book / "layout.json"
        layout_bytes = layout.read_bytes()
        for name in ("context.json", "context.md", "graph.json", "graph.md"):
            (dest / name).unlink()
        pause.write_text("hold", encoding="utf-8")
        monkeypatch.setenv("FINANCE_CONTEXT_PAUSE_STAGE", "layout_reuse")
        monkeypatch.setenv("FINANCE_CONTEXT_PAUSE_FILE", str(pause))
        try:
            again = _upload(alpha, source)
            assert again.status_code == 202
            assert _wait_stage(dest, "layout")
            posted = _upload(beta, source)
            assert posted.status_code == 202
            assert layout.read_bytes() == layout_bytes
        finally:
            pause.unlink(missing_ok=True)
        assert _wait_for_terminal(alpha, job_id)["status"] == "succeeded"
        assert _wait_for_terminal(beta, job_id)["status"] == "succeeded"
        assert layout.read_bytes() == layout_bytes
        assert (dest / "context.json").is_file()
