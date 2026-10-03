from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from tests.helpers.xlsx import write_zip

from finance_context.api.app import create_app
from finance_context.settings import Settings


def test_rejects_unsupported_extension(client: TestClient) -> None:
    response = client.post(
        "/v1/context-jobs",
        files={"file": ("model.xls", b"not-excel", "application/vnd.ms-excel")},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported_media_type"


def test_rejects_encrypted(client: TestClient, tmp_path: Path) -> None:
    source = tmp_path / "enc.xlsx"
    write_zip(source, {"EncryptionInfo": b"x", "EncryptedPackage": b"y"})
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
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=8,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/v1/context-jobs",
            files={"file": ("model.xlsx", b"0123456789", "application/octet-stream")},
        )
    assert response.status_code == 413


def test_job_id_must_be_a_content_hash(client: TestClient) -> None:
    for path in (
        "/v1/context-jobs/not-a-hash",
        "/v1/context-jobs/not-a-hash/context.json",
        "/v1/context-jobs/" + ("A" * 64),
    ):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"
