from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from tests.api.conftest import (
    _app,
    _capture_finance_logs,
    _hold_stage,
    _serving,
    _upload,
    _xlsx,
)
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx

from finance_context.api.app import create_app
from finance_context.settings import Settings


def _other(tmp_path: Path) -> Path:
    return build_xlsx(
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


def test_same_session_duplicate_post_is_deduped(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    pause = _hold_stage(tmp_path, monkeypatch, "parse")
    source = _xlsx(tmp_path / "model.xlsx")
    try:
        with _app(tmp_path) as client:
            _capture_finance_logs(caplog)
            first = _upload(client, source)
            assert first.status_code == 202
            second = _upload(client, source)
            assert second.status_code == 202
            assert second.json()["job_id"] == first.json()["job_id"]
            assert client.app.state.ctx.queue.session_inflight("local") == 1
        assert any(record.__dict__.get("event") == "job_dedup" for record in caplog.records)
        assert any(record.__dict__.get("event") == "job_enqueued" for record in caplog.records)
    finally:
        pause.unlink(missing_ok=True)


def test_session_queue_cap_is_429_with_retry_after(tmp_path: Path, monkeypatch) -> None:
    pause = _hold_stage(tmp_path, monkeypatch, "parse")
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=1024 * 1024,
        max_concurrent_jobs=1,
        job_queue_max_per_session=1,
        _env_file=None,
    )
    try:
        with _serving(settings) as client:
            first = _upload(client, _xlsx(tmp_path / "model.xlsx"))
            assert first.status_code == 202
            blocked = _upload(client, _other(tmp_path))
            assert blocked.status_code == 429
            assert blocked.json()["error"] == "too_many_jobs"
            assert blocked.headers["retry-after"] == "30"
    finally:
        pause.unlink(missing_ok=True)


def test_redis_down_post_is_503_with_retry_after(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=1024 * 1024,
        redis_url="redis://127.0.0.1:1/15",
        redis_connect_timeout_sec=0.2,
        _env_file=None,
    )
    source = _xlsx(tmp_path / "model.xlsx")
    with TestClient(create_app(settings)) as client:
        response = _upload(client, source)
    assert response.status_code == 503
    assert response.json()["error"] == "queue_unavailable"
    assert response.headers["retry-after"] == "30"


def test_readyz_stays_ready_when_redis_is_down(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        redis_url="redis://127.0.0.1:1/15",
        redis_connect_timeout_sec=0.2,
        _env_file=None,
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["jobs"] == 0
    assert body["queue"] == "finance-context"
