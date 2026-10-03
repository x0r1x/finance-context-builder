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
    _upload,
    _wait_disk_stage,
    _wait_for_terminal,
    _xlsx,
)
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.api.app import create_app
from finance_context.app.ids import job_id_for, sha256_bytes
from finance_context.app.publisher import publisher_fingerprint
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
    assert body["queue"] == "in_process"
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("x", encoding="utf-8")
    settings = Settings(
        data_dir=blocked,
        llm_base_url=None,
        embedding_base_url=None,
        embedding_model=None,
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

    job_dir = _job_dir(tmp_path, job_id)
    book_dir = _book_dir(tmp_path, job_id)
    sentinel = book_dir / "raw" / "keep-on-publisher-change"
    sentinel.write_text("keep", encoding="utf-8")
    mapping_path = job_dir / "mapping.json"
    mapping_path.write_text("not valid json", encoding="utf-8")
    (book_dir / "layout.json").write_text("{}", encoding="utf-8")
    meta_path = job_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["publisher"] = "stale"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    ir_bytes = {
        name: (book_dir / "ir" / name).read_bytes()
        for name in ("cells.parquet", "edges.parquet", "cell_edges.parquet")
    }

    (job_dir / "ir" / "graph_edges.parquet").write_bytes(b"stale-graph-edges")
    repeated = client.post("/v1/context-jobs", files=files)
    assert repeated.status_code == 202
    assert _wait_for_terminal(client, job_id)["status"] == "succeeded"

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert (book_dir / "source.xlsx").is_file()
    assert (book_dir / "raw" / "workbook.json").is_file()
    for name, payload in ir_bytes.items():
        assert (book_dir / "ir" / name).read_bytes() == payload
    layout = json.loads((book_dir / "layout.json").read_text(encoding="utf-8"))
    assert "sheets" in layout
    assert "rows" in json.loads(mapping_path.read_text(encoding="utf-8"))
    assert (job_dir / "context.json").is_file()
    assert (job_dir / "context.md").is_file()
    assert (job_dir / "ir" / "graph_edges.parquet").is_file()
    assert (job_dir / "ir" / "graph_edges.parquet").read_bytes() != b"stale-graph-edges"


def test_repeat_post_keeps_ready_snapshot(client: TestClient, tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    created = _upload(client, source)
    job_id = created.json()["job_id"]
    assert _wait_for_terminal(client, job_id)["status"] == "succeeded"
    job_dir = _job_dir(tmp_path, job_id)
    context = (job_dir / "context.json").read_bytes()
    sentinel = job_dir / "graph-edges.json"
    sentinel.write_text("keep", encoding="utf-8")
    repeated = _upload(client, source)
    assert repeated.status_code == 202
    assert repeated.json()["status"] not in {"queued", "running"}
    assert (job_dir / "context.json").read_bytes() == context
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_ready_post_logs_job_reuse_and_does_not_launch(tmp_path: Path, caplog) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    data = source.read_bytes()
    job_id = job_id_for(sha256_bytes(data))
    dest = _job_dir(tmp_path, job_id)
    dest.mkdir(parents=True)
    for name in ("context.json", "context.md", "graph.json", "graph.md"):
        (dest / name).write_text("{}\n", encoding="utf-8")
    (dest / "meta.json").write_text(
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
        processes = client.app.state.ctx.processes
        assert job_id not in processes._procs
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


def test_two_children_reach_mapping_together(tmp_path: Path, monkeypatch) -> None:
    pause = tmp_path / "pause"
    pause.write_text("hold", encoding="utf-8")
    monkeypatch.setenv("FINANCE_CONTEXT_PAUSE_STAGE", "mapping")
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
            processes = client.app.state.ctx.processes
            assert processes.is_alive(first_id)
            assert processes.is_alive(second_id)
    finally:
        pause.unlink(missing_ok=True)


def test_get_job_keeps_fresher_disk_stage(tmp_path: Path, monkeypatch) -> None:
    pause = _hold_stage(tmp_path, monkeypatch, "mapping")
    source = _xlsx(tmp_path / "model.xlsx")
    try:
        with _app(tmp_path) as client:
            created = _upload(client, source)
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            assert _wait_disk_stage(tmp_path, job_id, "mapping")
            body = client.get(f"/v1/context-jobs/{job_id}").json()
            assert body["status"] == "running"
            assert body["stage"] == "mapping"
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


def test_second_job_waits_behind_the_process_cap(tmp_path: Path, monkeypatch) -> None:
    pause = _hold_stage(tmp_path, monkeypatch, "mapping")
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
        llm_base_url=None,
        embedding_base_url=None,
        embedding_model=None,
        max_concurrent_jobs=1,
        _env_file=None,
    )
    try:
        with TestClient(create_app(settings)) as client:
            first = _upload(client, source)
            assert first.status_code == 202
            job_id = first.json()["job_id"]
            assert _wait_disk_stage(tmp_path, job_id, "mapping")
            repeated = _upload(client, source)
            assert repeated.status_code == 202
            blocked = _upload(client, other)
            assert blocked.status_code == 429
            assert blocked.json()["error"] == "too_many_jobs"
            pause.unlink()
            assert _wait_for_terminal(client, job_id)["status"] == "succeeded"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if client.app.state.ctx.processes.alive_count() == 0:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError("job process did not exit")
            accepted = _upload(client, other)
            assert accepted.status_code == 202
            assert accepted.json()["status"] in {"queued", "running"}
    finally:
        pause.unlink(missing_ok=True)


def test_dead_queued_job_relaunches(client: TestClient, tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    data = source.read_bytes()
    job_id = job_id_for(sha256_bytes(data))
    ctx = client.app.state.ctx
    assert ctx.bus.enqueue(job_id)
    assert ctx.bus.current_generation(job_id) == 1
    response = _upload(client, source)
    assert response.status_code == 202
    assert ctx.bus.current_generation(job_id) == 2
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] in {"queued", "running", "succeeded", "degraded", "needs_input"}


def test_job_timeout_marks_failed(tmp_path: Path, monkeypatch) -> None:
    pause = _hold_stage(tmp_path, monkeypatch, "mapping")
    source = _xlsx(tmp_path / "model.xlsx")
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=1024 * 1024,
        llm_base_url=None,
        embedding_base_url=None,
        embedding_model=None,
        job_timeout_sec=1,
        _env_file=None,
    )
    try:
        with TestClient(create_app(settings)) as client:
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


def test_startup_marks_orphaned_running_job(tmp_path: Path) -> None:
    job_id = "ab" * 32
    dest = _job_dir(tmp_path, job_id)
    dest.mkdir(parents=True)
    (dest / "meta.json").write_text(
        json.dumps(
            {"job_id": job_id, "status": "running", "stage": "parse", "generation": 2}
        ),
        encoding="utf-8",
    )
    with _app(tmp_path):
        stored = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert stored["status"] == "failed"
    assert stored["error"] == "process_lost"
    assert stored["generation"] == 2
    assert not (dest / "context.json").exists()


def test_get_marks_orphaned_running_job(client: TestClient, tmp_path: Path) -> None:
    job_id = "cd" * 32
    dest = _job_dir(tmp_path, job_id)
    dest.mkdir(parents=True)
    (dest / "meta.json").write_text(
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
    assert body["error"] == "process_lost"
    assert not (dest / "context.json").exists()
