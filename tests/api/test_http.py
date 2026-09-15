from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx, write_zip

from finance_context.api.app import create_app
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
        assert md_doc.status_code == 200
        assert "Financial context" in md_doc.text


def test_repeated_upload_rebuilds_mapping_and_reuses_parsed_artifacts(tmp_path: Path) -> None:
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

        repeated = client.post("/v1/context-jobs", files=files)
        assert repeated.status_code == 202
        assert _wait_for_terminal(client, job_id)["status"] in {
            "succeeded",
            "degraded",
            "needs_input",
        }

        assert sentinel.read_text(encoding="utf-8") == "keep"
        assert (job_dir / "source.xlsx").is_file()
        assert (job_dir / "ir" / "cells.parquet").is_file()
        assert (job_dir / "layout.json").is_file()
        assert "rows" in json.loads(mapping_path.read_text(encoding="utf-8"))
        assert (job_dir / "context.json").is_file()
        assert (job_dir / "context.md").is_file()
