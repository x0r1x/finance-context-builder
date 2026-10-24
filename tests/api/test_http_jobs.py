from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from fastapi.testclient import TestClient
from tests.api.conftest import (
    _app,
    _book_dir,
    _capture_finance_logs,
    _disk_stage,
    _hold_stage,
    _job_dir,
    _serving,
    _upload,
    _wait_disk_stage,
    _wait_for_terminal,
    _xlsx,
)
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.api.app import create_app
from finance_context.app.ids import job_id_for, sha256_bytes
from finance_context.app.publisher import publisher_fingerprint
from finance_context.jobqueue.spawn import run_lock_busy
from finance_context.settings import Settings


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_healthz_is_not_request_logged(client: TestClient, caplog) -> None:
    _capture_finance_logs(caplog)
    assert client.get("/healthz").status_code == 200
    assert not any(record.__dict__.get("event") == "http_start" for record in caplog.records)


def test_readyz_reports_jobs_and_a_blocked_data_dir(client: TestClient, tmp_path: Path) -> None:
    body = client.get("/readyz").json()
    assert body["status"] == "ready"
    assert body["jobs"] == 0
    assert body["queue"] == "finance-context"
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    settings = Settings(
        data_dir=blocked,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["status"] == "unavailable"
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}


def test_readyz_logs_request_start_and_done(client: TestClient, caplog) -> None:
    _capture_finance_logs(caplog)
    response = client.get("/readyz?probe=1")
    assert response.status_code == 200
    assert any(
        record.__dict__.get("event") == "http_start"
        and record.__dict__.get("method") == "GET"
        and record.__dict__.get("path") == "/readyz?probe=1"
        for record in caplog.records
    )
    assert any(
        record.__dict__.get("event") == "http_done"
        and record.__dict__.get("method") == "GET"
        and record.__dict__.get("path") == "/readyz?probe=1"
        and record.__dict__.get("http_code") == 200
        and isinstance(record.__dict__.get("duration_ms"), int)
        for record in caplog.records
    )


def test_repeated_upload_reuses_parse_and_compile(client: TestClient, tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    files = {
        "file": (
            "model.xlsx",
            source.read_bytes(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    created = client.post("/v1/context-jobs", files=files)
    assert created.status_code == 202
    job_id = created.json()["job_id"]
    assert _wait_for_terminal(client, job_id)["status"] == "succeeded"
    # Terminal meta is visible before the child writes stages.json. Wait until
    # that process has released the run lock so the stale stamp is not overwritten.
    job_dir = _job_dir(tmp_path, job_id)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not run_lock_busy(job_dir):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("job process did not exit")
    book_dir = _book_dir(tmp_path, job_id)
    sentinel = book_dir / "raw" / "keep-on-publisher-change"
    sentinel.write_text("keep", encoding="utf-8")
    (book_dir / "layout" / "layout.json").parent.mkdir(parents=True, exist_ok=True)
    (book_dir / "layout" / "layout.json").write_text("{}", encoding="utf-8")
    meta_path = job_dir / "meta" / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["publisher"] = "stale"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    stages_path = book_dir / "stages.json"
    stages = json.loads(stages_path.read_text(encoding="utf-8"))
    stages["publisher"] = "stale"
    stages_path.write_text(json.dumps(stages), encoding="utf-8")
    assert json.loads(stages_path.read_text(encoding="utf-8"))["publisher"] == "stale"
    ir_bytes = {
        name: (book_dir / "ir" / name).read_bytes()
        for name in ("cells.parquet", "edges.parquet", "cell_edges.parquet")
    }

    (job_dir / "graph" / "edges.parquet").parent.mkdir(parents=True, exist_ok=True)
    (job_dir / "graph" / "edges.parquet").write_bytes(b"stale-graph-edges")
    repeated = client.post("/v1/context-jobs", files=files)
    assert repeated.status_code == 202
    assert _wait_for_terminal(client, job_id)["status"] == "succeeded"

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert (book_dir / "source" / "source.xlsx").is_file()
    assert (book_dir / "raw" / "workbook.json").is_file()
    for name, payload in ir_bytes.items():
        assert (book_dir / "ir" / name).read_bytes() == payload
    layout = json.loads((book_dir / "layout" / "layout.json").read_text(encoding="utf-8"))
    assert "sheets" in layout
    assert (job_dir / "context" / "context.json").is_file()
    assert (job_dir / "context" / "context.md").is_file()
    assert (job_dir / "graph" / "edges.parquet").is_file()
    assert (job_dir / "graph" / "edges.parquet").read_bytes() != b"stale-graph-edges"


def test_repeat_post_keeps_ready_snapshot(client: TestClient, tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    created = _upload(client, source)
    job_id = created.json()["job_id"]
    assert _wait_for_terminal(client, job_id)["status"] == "succeeded"
    job_dir = _job_dir(tmp_path, job_id)
    context = (job_dir / "context" / "context.json").read_bytes()
    sentinel = job_dir / "graph-edges.json"
    sentinel.write_text("keep", encoding="utf-8")
    repeated = _upload(client, source)
    assert repeated.status_code == 202
    assert repeated.json()["status"] not in {"queued", "running"}
    assert (job_dir / "context" / "context.json").read_bytes() == context
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_ready_post_logs_job_reuse_and_does_not_launch(tmp_path: Path, caplog) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    data = source.read_bytes()
    job_id = job_id_for(sha256_bytes(data))
    dest = _job_dir(tmp_path, job_id)
    dest.mkdir(parents=True)
    for name in (
        "context/context.json",
        "context/context.md",
        "graph/graph.json",
        "graph/graph.md",
    ):
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "meta" / "meta.json").write_text(
        json.dumps(
            {
                "status": "succeeded",
                "stage": "done",
                "publisher": publisher_fingerprint(),
            }
        ),
        encoding="utf-8",
    )
    caplog.set_level(logging.INFO, logger="finance_context")
    with _app(tmp_path) as client:
        logging.getLogger("finance_context").addHandler(caplog.handler)
        response = _upload(client, source)
        assert client.app.state.ctx.queue.status(f"local--{job_id}") is None
    assert response.status_code == 202
    assert response.json()["status"] == "succeeded"
    assert response.json()["stage"] == "done"
    assert any(
        record.__dict__.get("event") == "job_reuse"
        and record.__dict__.get("job_id") == job_id
        and record.__dict__.get("status") == "succeeded"
        and record.__dict__.get("stage") == "done"
        for record in caplog.records
    )
    assert any(
        record.__dict__.get("event") == "http_start"
        and record.__dict__.get("method") == "POST"
        and record.__dict__.get("path") == "/v1/context-jobs"
        for record in caplog.records
    )
    assert any(
        record.__dict__.get("event") == "http_done"
        and record.__dict__.get("method") == "POST"
        and record.__dict__.get("path") == "/v1/context-jobs"
        and record.__dict__.get("http_code") == 202
        for record in caplog.records
    )


def test_two_children_reach_layout_together(tmp_path: Path, monkeypatch) -> None:
    pause = tmp_path / "pause"
    pause.write_text("hold", encoding="utf-8")
    monkeypatch.setenv("FINANCE_CONTEXT_PAUSE_STAGE", "layout")
    monkeypatch.setenv("FINANCE_CONTEXT_PAUSE_FILE", str(pause))
    other = build_xlsx(
        tmp_path / "other.xlsx",
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="A2", value="Costs", type="s"),
                    CellSpec(addr="B2", value="50"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "Costs"],
    )
    try:
        with _app(tmp_path) as client:
            first = _upload(client, _xlsx(tmp_path / "model.xlsx"))
            second = _upload(client, other)
            assert first.status_code == 202
            assert second.status_code == 202
            first_id = first.json()["job_id"]
            second_id = second.json()["job_id"]
            assert first_id != second_id
            deadline = time.monotonic() + 60
            stages = {}
            while time.monotonic() < deadline:
                stages = {
                    job_id: _disk_stage(tmp_path, job_id) for job_id in (first_id, second_id)
                }
                if all(stage not in {None, "", "queued"} for stage in stages.values()):
                    break
                time.sleep(0.05)
            assert stages[first_id] not in {None, "", "queued"}
            assert stages[second_id] not in {None, "", "queued"}
            from finance_context.jobqueue.spawn import run_lock_busy

            assert run_lock_busy(_job_dir(tmp_path, first_id))
            assert run_lock_busy(_job_dir(tmp_path, second_id))
    finally:
        pause.unlink(missing_ok=True)


def test_get_job_keeps_fresher_disk_stage(tmp_path: Path, monkeypatch) -> None:
    pause = _hold_stage(tmp_path, monkeypatch, "layout")
    source = _xlsx(tmp_path / "model.xlsx")
    try:
        with _app(tmp_path) as client:
            created = _upload(client, source)
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            assert _wait_disk_stage(tmp_path, job_id, "layout")
            body = client.get(f"/v1/context-jobs/{job_id}").json()
            assert body["status"] == "running"
            assert body["stage"] == "layout"
    finally:
        pause.unlink(missing_ok=True)


def test_get_job_reads_compile_stage_from_disk(tmp_path: Path, monkeypatch) -> None:
    pause = _hold_stage(tmp_path, monkeypatch, "compile")
    source = _xlsx(tmp_path / "model.xlsx")
    try:
        with _app(tmp_path) as client:
            created = _upload(client, source)
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            assert _wait_disk_stage(tmp_path, job_id, "compile")
            body = client.get(f"/v1/context-jobs/{job_id}").json()
            assert body["status"] == "running"
            assert body["stage"] == "compile"
    finally:
        pause.unlink(missing_ok=True)


def test_get_job_vanished_meta_uses_the_live_run(tmp_path: Path, monkeypatch) -> None:
    job_id = "a" * 64
    real = Path.read_text

    def _read(self: Path, *args: object, **kwargs: object) -> str:
        if self.name == "meta.json":
            raise FileNotFoundError(self)
        return real(self, *args, **kwargs)

    with _app(tmp_path) as client:
        dest = client.app.state.ctx.store.dest_dir(job_id)
        dest.mkdir(parents=True)
        (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
        (dest / "meta" / "meta.json").write_text(
            '{"status":"running","stage":"layout"}\n', encoding="utf-8"
        )
        ctx = client.app.state.ctx
        ctx.queue.enqueue(
            {
                "session_id": ctx.settings.session_id,
                "job_id": job_id,
                "data_dir": str(ctx.settings.data_dir),
                "generation": 1,
            }
        )
        monkeypatch.setattr(Path, "read_text", _read)
        response = client.get(f"/v1/context-jobs/{job_id}")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["stage"] == "queued"


def test_second_job_waits_behind_the_process_cap(tmp_path: Path, monkeypatch) -> None:
    pause = _hold_stage(tmp_path, monkeypatch, "layout")
    other = build_xlsx(
        tmp_path / "other.xlsx",
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="A2", value="Costs", type="s"),
                    CellSpec(addr="B2", value="50"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "Costs"],
    )
    source = _xlsx(tmp_path / "model.xlsx")
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=1024 * 1024,
        max_concurrent_jobs=1,
        _env_file=None,
    )
    try:
        with _serving(settings) as client:
            first = _upload(client, source)
            assert first.status_code == 202
            job_id = first.json()["job_id"]
            assert _wait_disk_stage(tmp_path, job_id, "layout")
            queued = _upload(client, other)
            assert queued.status_code == 202
            second_id = queued.json()["job_id"]
            assert queued.json()["status"] == "queued"
            # A same-book POST would block on the child's directory lock. This
            # book is a different directory, so the only worker slot is what
            # keeps it queued.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                assert _disk_stage(tmp_path, second_id) in {None, "queued"}
                time.sleep(0.05)
            pause.unlink()
            assert _wait_for_terminal(client, job_id)["status"] == "succeeded"
            assert _wait_for_terminal(client, second_id)["status"] == "succeeded"
    finally:
        pause.unlink(missing_ok=True)


def test_dead_queued_job_relaunches(client: TestClient, tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    data = source.read_bytes()
    job_id = job_id_for(sha256_bytes(data))
    dest = _job_dir(tmp_path, job_id)
    dest.mkdir(parents=True)
    (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "meta" / "meta.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "status": "failed",
                "stage": "failed",
                "error": "process_lost",
                "generation": 1,
            }
        ),
        encoding="utf-8",
    )
    response = _upload(client, source)
    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] == "queued"
    stored = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert stored["generation"] == 2


def test_job_timeout_marks_failed(tmp_path: Path, monkeypatch) -> None:
    pause = _hold_stage(tmp_path, monkeypatch, "layout")
    source = _xlsx(tmp_path / "model.xlsx")
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=1024 * 1024,
        job_timeout_sec=1,
        _env_file=None,
    )
    try:
        with _serving(settings) as client:
            created = _upload(client, source)
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            body = _wait_for_terminal(client, job_id)
            assert body["status"] == "failed"
            assert body["stage"] == "failed"
            assert body["error"] == "TimeoutError"
            repeated = _upload(client, source)
            assert repeated.status_code == 202
            assert repeated.json()["status"] in {"queued", "running"}
    finally:
        pause.unlink(missing_ok=True)


def test_startup_leaves_a_running_job_on_disk(tmp_path: Path) -> None:
    job_id = "ab" * 32
    dest = _job_dir(tmp_path, job_id)
    dest.mkdir(parents=True)
    (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "meta" / "meta.json").write_text(
        json.dumps(
            {"job_id": job_id, "status": "running", "stage": "parse", "generation": 2}
        ),
        encoding="utf-8",
    )
    with _app(tmp_path):
        stored = json.loads((dest / "meta" / "meta.json").read_text(encoding="utf-8"))
    assert stored["status"] == "running"
    assert stored["stage"] == "parse"
    assert "error" not in stored
    assert stored["generation"] == 2
    assert not (dest / "context" / "context.json").exists()


def test_get_marks_orphaned_running_job(client: TestClient, tmp_path: Path) -> None:
    job_id = "cd" * 32
    dest = _job_dir(tmp_path, job_id)
    dest.mkdir(parents=True)
    (dest / "meta" / "meta.json").parent.mkdir(parents=True, exist_ok=True)
    (dest / "meta" / "meta.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "status": "queued",
                "stage": "queued",
                "generation": 1,
            }
        ),
        encoding="utf-8",
    )
    response = client.get(f"/v1/context-jobs/{job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert body["error"] == "queue_lost"
    assert not (dest / "context" / "context.json").exists()
