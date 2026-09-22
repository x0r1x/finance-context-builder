from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx, write_zip

from finance_context.api.app import create_app
from finance_context.app.pipeline import Pipeline
from finance_context.settings import Settings
from finance_context.store.fs import write_json


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
        assert json_doc.json()["schema_version"] == "1.10.0"


def test_repeated_upload_rebuilds_compile_layout_mapping_and_reuses_parse(tmp_path: Path) -> None:
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
        ir_cells = job_dir / "ir" / "cells.parquet"
        ir_cells.write_bytes(b"stale")

        repeated = client.post("/v1/context-jobs", files=files)
        assert repeated.status_code == 202
        assert _wait_for_terminal(client, job_id)["status"] in {
            "succeeded",
            "degraded",
            "needs_input",
        }

        assert sentinel.read_text(encoding="utf-8") == "keep"
        assert (job_dir / "source.xlsx").is_file()
        assert (job_dir / "raw" / "workbook.json").is_file()
        assert ir_cells.is_file()
        assert ir_cells.read_bytes() != b"stale"
        layout = json.loads((job_dir / "layout.json").read_text(encoding="utf-8"))
        assert "sheets" in layout
        assert "rows" in json.loads(mapping_path.read_text(encoding="utf-8"))
        assert (job_dir / "context.json").is_file()
        assert (job_dir / "context.md").is_file()


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
    started = threading.Event()
    release = threading.Event()

    def fake_run(
        self,
        dest_dir,
        *,
        job_id,
        source_filename=None,
        content_sha256=None,
        progress=None,
    ):
        write_json(
            dest_dir / "meta.json",
            {
                "job_id": job_id,
                "status": "running",
                "stage": "mapping",
                "warnings": [],
                "questions": [],
            },
        )
        started.set()
        release.wait(5)
        raise RuntimeError("stop")

    monkeypatch.setattr(Pipeline, "run", fake_run)
    source = _xlsx(tmp_path / "model.xlsx")
    try:
        with _app(tmp_path) as client:
            created = _upload(client, source)
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            assert started.wait(5)
            body = client.get(f"/v1/context-jobs/{job_id}").json()
            assert body["status"] == "running"
            assert body["stage"] == "mapping"
    finally:
        release.set()


def test_get_job_follows_pipeline_progress(tmp_path: Path, monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()

    def fake_run(
        self,
        dest_dir,
        *,
        job_id,
        source_filename=None,
        content_sha256=None,
        progress=None,
    ):
        write_json(
            dest_dir / "meta.json",
            {
                "job_id": job_id,
                "status": "running",
                "stage": "parse",
                "warnings": [],
                "questions": [],
            },
        )
        assert progress is not None
        progress.progress("compile")
        started.set()
        release.wait(5)
        raise RuntimeError("stop")

    monkeypatch.setattr(Pipeline, "run", fake_run)
    source = _xlsx(tmp_path / "model.xlsx")
    try:
        with _app(tmp_path) as client:
            created = _upload(client, source)
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            assert started.wait(5)
            body = client.get(f"/v1/context-jobs/{job_id}").json()
            assert body["status"] == "running"
            assert body["stage"] == "compile"
    finally:
        release.set()


def test_job_timeout_marks_failed(tmp_path: Path, monkeypatch) -> None:
    release = threading.Event()

    def fake_run(
        self,
        dest_dir,
        *,
        job_id,
        source_filename=None,
        content_sha256=None,
        progress=None,
    ):
        if not release.is_set():
            release.wait(5)
        raise RuntimeError("stop")

    monkeypatch.setattr(Pipeline, "run", fake_run)
    source = _xlsx(tmp_path / "model.xlsx")
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=1024 * 1024,
        llm_base_url=None,
        embedding_base_url=None,
        embedding_model=None,
        job_timeout_sec=0.2,
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
            release.set()
            repeated = _upload(client, source)
            assert repeated.status_code == 202
            assert repeated.json()["status"] in {"queued", "running"}
    finally:
        release.set()
