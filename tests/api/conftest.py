from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.api.app import create_app
from finance_context.settings import Settings


def _app(tmp_path: Path) -> TestClient:
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=1024 * 1024,
        llm_base_url=None,
        embedding_base_url=None,
        embedding_model=None,
        _env_file=None,
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


def _job_dir(tmp_path: Path, job_id: str) -> Path:
    return tmp_path / "data" / "sessions" / "local" / "jobs" / job_id


def _book_dir(tmp_path: Path, job_id: str) -> Path:
    return tmp_path / "data" / "shared" / "books" / job_id


def _disk_stage(tmp_path: Path, job_id: str) -> str | None:
    path = _job_dir(tmp_path, job_id) / "meta.json"
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


def _capture_finance_logs(caplog):
    caplog.set_level(logging.INFO, logger="finance_context")
    logging.getLogger("finance_context").addHandler(caplog.handler)


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


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with _app(tmp_path) as opened:
        yield opened
