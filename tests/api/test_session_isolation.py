"""Session startup must not fail another session's job, and disk status wins."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.api.conftest import (
    _app,
    _capture_finance_logs,
    _hold_stage,
    _job_dir,
    _upload,
    _wait_disk_stage,
    _xlsx,
)

from finance_context.api.app import create_app, reconcile_orphaned_jobs
from finance_context.app.ids import job_id_for, sha256_bytes
from finance_context.app.pipeline import mark_job_failed
from finance_context.app.publisher import publisher_fingerprint
from finance_context.settings import Settings


def _write_meta(dest: Path, **fields: object) -> dict:
    dest.mkdir(parents=True, exist_ok=True)
    payload = dict(fields)
    (dest / "meta.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _read_meta(dest: Path) -> dict:
    return json.loads((dest / "meta.json").read_text(encoding="utf-8"))


def _job(root: Path, session: str, job_id: str) -> Path:
    return root / "sessions" / session / "jobs" / job_id


def test_mark_job_failed_preserves_existing_meta_fields(tmp_path: Path) -> None:
    dest = tmp_path / "job"
    original = _write_meta(
        dest,
        job_id="job",
        status="running",
        stage="parse",
        source_filename="book.xlsx",
        content_sha256="a" * 64,
        warnings=["kept"],
        questions=[{"id": "q"}],
        generation=2,
        publisher="stamp",
        stages={"layout": "old"},
        schema_version="1.14.0",
    )
    mark_job_failed(dest, "job", "process_lost", 2)
    stored = _read_meta(dest)
    assert stored["status"] == "failed"
    assert stored["stage"] == "failed"
    assert stored["error"] == "process_lost"
    assert stored["generation"] == 2
    assert "publisher" not in stored
    assert "stages" not in stored
    for key in (
        "source_filename",
        "content_sha256",
        "warnings",
        "questions",
        "schema_version",
    ):
        assert stored[key] == original[key]


def test_reconcile_touches_only_this_session_and_keeps_filename(tmp_path: Path) -> None:
    root = tmp_path / "data"
    job_id = "ab" * 32
    alpha = _job(root, "alpha", job_id)
    beta = _job(root, "beta", job_id)
    alpha_meta = _write_meta(
        alpha,
        job_id=job_id,
        status="running",
        stage="parse",
        source_filename="alpha.xlsx",
        content_sha256="b" * 64,
        warnings=["alpha"],
        generation=3,
    )
    _write_meta(
        beta,
        job_id=job_id,
        status="running",
        stage="layout",
        source_filename="beta.xlsx",
        content_sha256="c" * 64,
        generation=1,
    )
    reconcile_orphaned_jobs(root, "beta")
    assert _read_meta(alpha) == alpha_meta
    stored = _read_meta(beta)
    assert stored["status"] == "failed"
    assert stored["error"] == "process_lost"
    assert stored["source_filename"] == "beta.xlsx"
    assert stored["content_sha256"] == "c" * 64
    assert stored["generation"] == 1


def test_aliased_jobs_root_does_not_fail_the_other_session(tmp_path: Path) -> None:
    root = tmp_path / "data"
    job_id = "ab" * 32
    alpha = _job(root, "alpha", job_id)
    original = _write_meta(
        alpha,
        job_id=job_id,
        status="running",
        stage="parse",
        source_filename="alpha.xlsx",
        generation=1,
    )
    beta = root / "sessions" / "beta"
    beta.mkdir(parents=True)
    (beta / "jobs").symlink_to(alpha.parent, target_is_directory=True)
    settings = Settings(
        data_dir=root,
        session_id="beta",
        max_upload_bytes=1024 * 1024,
        _env_file=None,
    )
    with pytest.raises(ValueError, match="DATA_DIR"):
        reconcile_orphaned_jobs(root, "beta")
    with pytest.raises(ValueError, match="DATA_DIR"):
        create_app(settings)
    assert _read_meta(alpha) == original


def test_reconcile_skips_a_job_symlink_outside_the_session(tmp_path: Path) -> None:
    root = tmp_path / "data"
    outside = tmp_path / "outside"
    original = _write_meta(
        outside,
        job_id="linked",
        status="running",
        stage="parse",
        source_filename="outside.xlsx",
        generation=1,
    )
    jobs = root / "sessions" / "beta" / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "linked").symlink_to(outside, target_is_directory=True)
    reconcile_orphaned_jobs(root, "beta")
    assert _read_meta(outside) == original


def test_other_session_startup_does_not_fail_a_foreign_job(tmp_path: Path) -> None:
    root = tmp_path / "data"
    job_id = "cd" * 32
    dest = _job(root, "alpha", job_id)
    original = _write_meta(
        dest,
        job_id=job_id,
        status="running",
        stage="parse",
        source_filename="book.xlsx",
        content_sha256="d" * 64,
        generation=4,
    )
    settings = Settings(
        data_dir=root,
        session_id="beta",
        max_upload_bytes=1024 * 1024,
        _env_file=None,
    )
    with TestClient(create_app(settings)):
        assert _read_meta(dest) == original


def test_own_startup_marks_orphan_and_keeps_filename(tmp_path: Path) -> None:
    job_id = "ef" * 32
    dest = tmp_path / "data" / "sessions" / "local" / "jobs" / job_id
    _write_meta(
        dest,
        job_id=job_id,
        status="queued",
        stage="queued",
        source_filename="book.xlsx",
        content_sha256="e" * 64,
        warnings=["kept"],
        generation=2,
        publisher="stamp",
        stages={"layout": "old"},
    )
    with _app(tmp_path) as client:
        stored = _read_meta(dest)
        assert stored["status"] == "failed"
        assert stored["error"] == "process_lost"
        assert stored["source_filename"] == "book.xlsx"
        assert stored["content_sha256"] == "e" * 64
        assert stored["warnings"] == ["kept"]
        assert "publisher" not in stored
        assert "stages" not in stored
        listed = client.get("/v1/context-jobs").json()
        assert listed[0]["status"] == "failed"
        assert listed[0]["source_filename"] == "book.xlsx"
        body = client.get(f"/v1/context-jobs/{job_id}").json()
        assert body["status"] == "failed"
        assert body["error"] == "process_lost"


def test_get_job_prefers_terminal_disk_status_over_queued_bus(tmp_path: Path) -> None:
    job_id = "aa" * 32
    dest = tmp_path / "data" / "sessions" / "local" / "jobs" / job_id
    _write_meta(
        dest,
        job_id=job_id,
        status="failed",
        stage="failed",
        error="process_lost",
        source_filename="book.xlsx",
        generation=1,
    )
    with _app(tmp_path) as client:
        assert client.app.state.ctx.bus.enqueue(job_id)
        body = client.get(f"/v1/context-jobs/{job_id}").json()
        assert body["status"] == "failed"
        assert body["stage"] == "failed"
        assert body["error"] == "process_lost"


def test_get_job_shows_a_newer_bus_run_over_an_old_failed_meta(tmp_path: Path) -> None:
    job_id = "ac" * 32
    dest = tmp_path / "data" / "sessions" / "local" / "jobs" / job_id
    _write_meta(
        dest,
        job_id=job_id,
        status="failed",
        stage="failed",
        error="process_lost",
        source_filename="book.xlsx",
        generation=1,
    )
    with _app(tmp_path) as client:
        bus = client.app.state.ctx.bus
        assert bus.enqueue(job_id)
        assert bus.abandon(job_id, 1, "process_lost")
        assert bus.enqueue(job_id)
        body = client.get(f"/v1/context-jobs/{job_id}").json()
        assert body["status"] == "queued"
        assert body["stage"] == "queued"


def test_get_job_keeps_failed_disk_when_bus_stage_is_ahead(tmp_path: Path) -> None:
    job_id = "bb" * 32
    dest = tmp_path / "data" / "sessions" / "local" / "jobs" / job_id
    _write_meta(
        dest,
        job_id=job_id,
        status="failed",
        stage="failed",
        error="process_lost",
        source_filename="book.xlsx",
        generation=1,
    )
    with _app(tmp_path) as client:
        bus = client.app.state.ctx.bus
        assert bus.enqueue(job_id)
        bus.set_progress(job_id, "layout", generation=bus.current_generation(job_id))
        body = client.get(f"/v1/context-jobs/{job_id}").json()
        assert body["status"] == "failed"
        assert body["stage"] == "failed"
        assert body["error"] == "process_lost"


def test_other_session_startup_leaves_a_live_job_running(tmp_path: Path, monkeypatch) -> None:
    pause = _hold_stage(tmp_path, monkeypatch, "parse")
    root = tmp_path / "data"
    source = _xlsx(tmp_path / "model.xlsx")
    settings_a = Settings(
        data_dir=root,
        session_id="alpha",
        max_upload_bytes=1024 * 1024,
        _env_file=None,
    )
    settings_b = Settings(
        data_dir=root,
        session_id="beta",
        max_upload_bytes=1024 * 1024,
        _env_file=None,
    )
    try:
        with TestClient(create_app(settings_a)) as client_a:
            created = _upload(client_a, source)
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            dest = _job(root, "alpha", job_id)
            deadline_stage = _wait_meta_stage(dest, "parse")
            assert deadline_stage, "live job did not reach parse"
            before = _read_meta(dest)
            assert before["status"] == "running"
            assert before["source_filename"]
            with TestClient(create_app(settings_b)) as client_b:
                assert client_b.get("/healthz").status_code == 200
                stored = _read_meta(dest)
                assert stored["status"] == "running"
                assert stored["stage"] == "parse"
                assert stored["source_filename"] == before["source_filename"]
                assert "error" not in stored or stored["error"] in {None, ""}
                assert client_a.app.state.ctx.processes.is_alive(job_id)
                body = client_a.get(f"/v1/context-jobs/{job_id}").json()
                assert body["status"] == "running"
                assert body["stage"] == "parse"
            stored = _read_meta(dest)
            assert stored["status"] == "running"
            assert stored["source_filename"] == before["source_filename"]
    finally:
        pause.unlink(missing_ok=True)


def test_repost_after_restart_seeds_generation_above_disk(tmp_path: Path, monkeypatch) -> None:
    """A fresh process must not hide a re-POST behind the reconciled failure."""
    pause = _hold_stage(tmp_path, monkeypatch, "parse")
    source = _xlsx(tmp_path / "model.xlsx")
    data = source.read_bytes()
    job_id = job_id_for(sha256_bytes(data))
    dest = _job_dir(tmp_path, job_id)
    _write_meta(
        dest,
        job_id=job_id,
        status="running",
        stage="parse",
        source_filename="model.xlsx",
        content_sha256=sha256_bytes(data),
        generation=1,
    )
    try:
        with _app(tmp_path) as client:
            failed = _read_meta(dest)
            assert failed["status"] == "failed"
            assert failed["error"] == "process_lost"
            assert failed["generation"] == 1
            created = _upload(client, source)
            assert created.status_code == 202
            assert created.json()["status"] in {"queued", "running"}
            assert client.app.state.ctx.processes.is_alive(job_id)
            body = client.get(f"/v1/context-jobs/{job_id}").json()
            assert body["status"] in {"queued", "running"}
            assert body.get("error") in {None, ""}
            generation = client.app.state.ctx.bus.current_generation(job_id)
            assert generation == 2
            assert _wait_disk_stage(tmp_path, job_id, "parse")
            stored = _read_meta(dest)
            assert stored["generation"] == generation
            assert stored["status"] == "running"
            assert stored["stage"] == "parse"
    finally:
        pause.unlink(missing_ok=True)


def test_generation_two_failure_with_artifacts_relaunches(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    """process_lost must not look like a finished snapshot at generation 2."""
    pause = _hold_stage(tmp_path, monkeypatch, "parse")
    source = _xlsx(tmp_path / "model.xlsx")
    data = source.read_bytes()
    digest = sha256_bytes(data)
    job_id = job_id_for(digest)
    dest = _job_dir(tmp_path, job_id)
    dest.mkdir(parents=True)
    for name in ("context.json", "context.md", "graph.json", "graph.md"):
        (dest / name).write_text("stale-artifact\n", encoding="utf-8")
    _write_meta(
        dest,
        job_id=job_id,
        status="running",
        stage="layout",
        source_filename="model.xlsx",
        content_sha256=digest,
        warnings=["kept"],
        generation=2,
        publisher=publisher_fingerprint(),
        stages={"layout": "old"},
    )
    try:
        with _app(tmp_path) as client:
            failed = _read_meta(dest)
            assert failed["status"] == "failed"
            assert failed["error"] == "process_lost"
            assert failed["generation"] == 2
            assert failed["source_filename"] == "model.xlsx"
            assert failed["content_sha256"] == digest
            assert failed["warnings"] == ["kept"]
            assert "publisher" not in failed
            assert "stages" not in failed
            _capture_finance_logs(caplog)
            created = _upload(client, source)
            assert created.status_code == 202
            assert created.json()["status"] in {"queued", "running"}
            assert not any(
                record.__dict__.get("event") == "job_reuse" for record in caplog.records
            )
            assert client.app.state.ctx.processes.is_alive(job_id)
            body = client.get(f"/v1/context-jobs/{job_id}").json()
            assert body["status"] in {"queued", "running"}
            assert body.get("error") in {None, ""}
            generation = client.app.state.ctx.bus.current_generation(job_id)
            assert generation == 3
            assert _wait_disk_stage(tmp_path, job_id, "parse")
            stored = _read_meta(dest)
            assert stored["generation"] == 3
            assert stored["status"] == "running"
            context = dest / "context.json"
            stale = context.read_text(encoding="utf-8") if context.is_file() else ""
            assert stale != "stale-artifact\n"
    finally:
        pause.unlink(missing_ok=True)


def _wait_meta_stage(dest: Path, stage: str) -> bool:
    import time

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        path = dest / "meta.json"
        if path.is_file():
            try:
                meta = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = None
            if isinstance(meta, dict) and meta.get("stage") == stage:
                return True
        time.sleep(0.05)
    return False
