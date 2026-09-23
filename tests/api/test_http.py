from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx, write_zip

from finance_context.api.app import create_app
from finance_context.app.ids import job_id_for, sha256_bytes
from finance_context.settings import Settings


def _app(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=1024 * 1024,
        llm_base_url=None,
        embedding_base_url=None,
        embedding_model=None,
    )
    return TestClient(create_app(settings))


def _xlsx(path: Path) -> Path:
    return build_xlsx(
        path,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2023", type="s"),
                    CellSpec(addr="C1", value="2024E", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="100"),
                    CellSpec(addr="C2", value="110"),
                ],
            )
        ],
        shared_strings=["Item", "2023", "2024E", "Revenue"],
    )


def _hold_stage(tmp_path: Path, monkeypatch, stage: str) -> Path:
    pause = tmp_path / f"pause-{stage}"
    pause.write_text("hold", encoding="utf-8")
    monkeypatch.setenv("FINANCE_CONTEXT_PAUSE_STAGE", stage)
    monkeypatch.setenv("FINANCE_CONTEXT_PAUSE_FILE", str(pause))
    return pause


def _disk_stage(tmp_path: Path, job_id: str) -> str | None:
    path = tmp_path / "data" / "jobs" / job_id / "meta.json"
    if not path.is_file():
        return None
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    stage = meta.get("stage")
    return str(stage) if stage else None


def _wait_disk_stage(tmp_path: Path, job_id: str, stage: str) -> bool:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _disk_stage(tmp_path, job_id) == stage:
            return True
        time.sleep(0.05)
    return False


def _wait_for_terminal(client: TestClient, job_id: str) -> dict:
    for _ in range(200):
        response = client.get(f"/v1/context-jobs/{job_id}")
        body = response.json()
        if body.get("status") not in {"queued", "running"}:
            return body
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not finish")


def test_healthz(tmp_path: Path) -> None:
    with _app(tmp_path) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def _capture_finance_logs(caplog):
    caplog.set_level(logging.INFO, logger="finance_context")
    logging.getLogger("finance_context").addHandler(caplog.handler)


def test_healthz_is_not_request_logged(tmp_path: Path, caplog) -> None:
    with _app(tmp_path) as client:
        _capture_finance_logs(caplog)
        assert client.get("/healthz").status_code == 200
    assert not any(record.__dict__.get("event") == "http_start" for record in caplog.records)


def test_readyz_reports_jobs_and_a_blocked_data_dir(tmp_path: Path) -> None:
    with _app(tmp_path) as client:
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


def test_readyz_logs_request_start_and_done(tmp_path: Path, caplog) -> None:
    with _app(tmp_path) as client:
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


def test_rejects_unsupported_extension(tmp_path: Path) -> None:
    with _app(tmp_path) as client:
        response = client.post(
            "/v1/context-jobs",
            files={"file": ("model.xls", b"not-excel", "application/vnd.ms-excel")},
        )
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_media_type"


def test_rejects_encrypted(tmp_path: Path) -> None:
    source = tmp_path / "enc.xlsx"
    write_zip(source, {"EncryptionInfo": b"x", "EncryptedPackage": b"y"})
    with _app(tmp_path) as client:
        response = client.post(
            "/v1/context-jobs",
            files={
                "file": (
                    "enc.xlsx",
                    source.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
    assert response.status_code == 422
    assert response.json()["error"] == "encrypted_workbook"


def test_rejects_oversize(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", max_upload_bytes=8)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/context-jobs",
            files={"file": ("model.xlsx", b"0123456789", "application/octet-stream")},
        )
    assert response.status_code == 413


def test_upload_and_download(tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    with _app(tmp_path) as client:
        created = client.post(
            "/v1/context-jobs",
            files={
                "file": (
                    "model.xlsx",
                    source.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        status = None
        for _ in range(200):
            status = client.get(f"/v1/context-jobs/{job_id}")
            if status.json().get("status") not in {"queued", "running"}:
                break
            time.sleep(0.1)
        assert status is not None
        body = status.json()
        assert body["status"] in {"succeeded", "degraded", "needs_input"}
        json_doc = client.get(f"/v1/context-jobs/{job_id}/context.json")
        md_doc = client.get(f"/v1/context-jobs/{job_id}/context.md")
        assert json_doc.status_code == 200
        assert "schema_version" in json_doc.json()
        graph_doc = client.get(f"/v1/context-jobs/{job_id}/graph.json")
        assert graph_doc.status_code == 200
        assert "nodes" in graph_doc.json()
        assert body.get("context_json_url") == f"/v1/context-jobs/{job_id}/context.json"
        assert body.get("context_md_url") == f"/v1/context-jobs/{job_id}/context.md"
        assert body.get("graph_json_url") == f"/v1/context-jobs/{job_id}/graph.json"
        assert body.get("graph_md_url") == f"/v1/context-jobs/{job_id}/graph.md"
        assert "graph_edges_url" not in body
        assert "formulas_json_url" not in body
        graph_md = client.get(f"/v1/context-jobs/{job_id}/graph.md")
        assert graph_md.status_code == 200
        assert "Formula graph" in graph_md.text
        assert "links" in graph_doc.json()
        traced = client.get(f"/v1/context-jobs/{job_id}/graph/trace", params={"from": "P&L!C2"})
        assert traced.status_code == 200
        assert traced.json()["origin"] == "P&L!C2"
        assert "formula_ast" not in traced.text
        traced_md = client.get(
            f"/v1/context-jobs/{job_id}/graph/trace.md", params={"from": "P&L!C2"}
        )
        assert traced_md.status_code == 200
        assert "P&L!C2" in traced_md.text
        assert md_doc.status_code == 200
        assert "Financial context" in md_doc.text
        assert json_doc.json()["schema_version"] == "1.13.0"


def test_repeated_upload_reuses_parse_and_compile(tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    files = {
        "file": (
            "model.xlsx",
            source.read_bytes(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    with _app(tmp_path) as client:
        created = client.post("/v1/context-jobs", files=files)
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        assert _wait_for_terminal(client, job_id)["status"] in {
            "succeeded",
            "degraded",
            "needs_input",
        }

        job_dir = tmp_path / "data" / "jobs" / job_id
        sentinel = job_dir / "raw" / "keep-on-remap"
        sentinel.write_text("keep", encoding="utf-8")
        mapping_path = job_dir / "mapping.json"
        mapping_path.write_text("not valid json", encoding="utf-8")
        (job_dir / "layout.json").write_text("{}", encoding="utf-8")
        ir_bytes = {
            name: (job_dir / "ir" / name).read_bytes()
            for name in ("cells.parquet", "edges.parquet", "cell_edges.parquet")
        }

        (job_dir / "ir" / "graph_edges.parquet").write_bytes(b"stale-graph-edges")
        repeated = client.post("/v1/context-jobs?remap=1", files=files)
        assert repeated.status_code == 202
        assert _wait_for_terminal(client, job_id)["status"] in {
            "succeeded",
            "degraded",
            "needs_input",
        }

        assert sentinel.read_text(encoding="utf-8") == "keep"
        assert (job_dir / "source.xlsx").is_file()
        assert (job_dir / "raw" / "workbook.json").is_file()
        for name, payload in ir_bytes.items():
            assert (job_dir / "ir" / name).read_bytes() == payload
        layout = json.loads((job_dir / "layout.json").read_text(encoding="utf-8"))
        assert "sheets" in layout
        assert "rows" in json.loads(mapping_path.read_text(encoding="utf-8"))
        assert (job_dir / "context.json").is_file()
        assert (job_dir / "context.md").is_file()
        assert (job_dir / "ir" / "graph_edges.parquet").is_file()
        assert (job_dir / "ir" / "graph_edges.parquet").read_bytes() != b"stale-graph-edges"


def test_repeat_post_keeps_ready_snapshot(tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    with _app(tmp_path) as client:
        created = _upload(client, source)
        job_id = created.json()["job_id"]
        assert _wait_for_terminal(client, job_id)["status"] in {
            "succeeded",
            "degraded",
            "needs_input",
        }
        job_dir = tmp_path / "data" / "jobs" / job_id
        context = (job_dir / "context.json").read_bytes()
        sentinel = job_dir / "graph-edges.json"
        sentinel.write_text("keep", encoding="utf-8")
        repeated = _upload(client, source)
        assert repeated.status_code == 202
        assert repeated.json()["status"] not in {"queued", "running"}
        assert (job_dir / "context.json").read_bytes() == context
        assert sentinel.read_text(encoding="utf-8") == "keep"


def test_job_id_must_be_a_content_hash(tmp_path: Path) -> None:
    from finance_context.api.errors import ApiError
    from finance_context.api.routes import _check_job_id

    with pytest.raises(ApiError) as caught:
        _check_job_id("..")
    assert caught.value.http_status == 404
    assert caught.value.code == "not_found"
    with _app(tmp_path) as client:
        for path in (
            "/v1/context-jobs/not-a-hash",
            "/v1/context-jobs/not-a-hash/context.json",
            "/v1/context-jobs/" + ("A" * 64),
        ):
            response = client.get(path)
            assert response.status_code == 404
            assert response.json()["error"] == "not_found"


def test_get_context_md_rewrites_a_finished_job(tmp_path: Path) -> None:
    job_id = "a" * 64
    dest = tmp_path / "data" / "jobs" / job_id
    dest.mkdir(parents=True)
    context = {
        "meta": {"job_id": job_id, "status": "succeeded", "stage": "done"},
        "workbook": {},
        "blocks": [
            {
                "block_id": "PF Model!r7",
                "sheet": "PF Model",
                "label_col": 1,
                "periods": [{"col": 27, "period_key": "2038", "text": "2038", "role": "forecast"}],
                "rows": [
                    {
                        "row_key": "PF Model|172|PF Model!r7",
                        "sheet": "PF Model",
                        "row": 172,
                        "kind": "fact",
                        "label": "CPI",
                        "values": ["1.3458683383241299"],
                        "value_statuses": ["cached"],
                    }
                ],
            }
        ],
    }
    (dest / "context.json").write_text(json.dumps(context), encoding="utf-8")
    (dest / "context.md").write_text("1.3458683383241299\n", encoding="utf-8")
    (dest / "graph.json").write_text("{}\n", encoding="utf-8")
    (dest / "graph.md").write_text("PF Model!AA172 | Z172*(1+AA165)\n", encoding="utf-8")
    with _app(tmp_path) as client:
        response = client.get(f"/v1/context-jobs/{job_id}/context.md")
    assert response.status_code == 200
    assert "1.3458683383241299 [PF Model!AA172]" in response.text
    assert "Z172*(1+AA165)" not in response.text
    stored = json.loads((dest / "context.json").read_text(encoding="utf-8"))
    assert stored["blocks"][0]["rows"][0]["values"] == ["1.3458683383241299"]
    assert (dest / "graph.md").read_text(encoding="utf-8") == "PF Model!AA172 | Z172*(1+AA165)\n"


def test_ready_post_logs_job_reuse_and_does_not_launch(tmp_path: Path, caplog) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    data = source.read_bytes()
    job_id = job_id_for(sha256_bytes(data))
    dest = tmp_path / "data" / "jobs" / job_id
    dest.mkdir(parents=True)
    for name in ("context.json", "context.md", "graph.json", "graph.md"):
        (dest / name).write_text("{}\n", encoding="utf-8")
    (dest / "meta.json").write_text(
        json.dumps({"status": "succeeded", "stage": "done"}),
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


def _upload(client: TestClient, source: Path):
    return client.post(
        "/v1/context-jobs",
        files={
            "file": (
                "model.xlsx",
                source.read_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )


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
            assert _wait_for_terminal(client, job_id)["status"] in {
                "succeeded",
                "degraded",
                "needs_input",
            }
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


def test_dead_queued_job_relaunches(tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "model.xlsx")
    data = source.read_bytes()
    job_id = job_id_for(sha256_bytes(data))
    with _app(tmp_path) as client:
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
