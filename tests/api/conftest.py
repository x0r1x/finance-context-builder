from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import RedisError
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx
from tests.jobqueue.conftest import local_test_redis_url

from finance_context.api.app import create_app
from finance_context.settings import Settings


def _spawn_worker(settings: Settings) -> subprocess.Popen[bytes]:
    """A real worker so fork, stop-job, and the pause env are the production path.

    Started on the first POST, after the test has set FINANCE_CONTEXT_PAUSE_*.
    """
    env = os.environ.copy()
    env["DATA_DIR"] = str(settings.data_dir)
    env["SESSION_ID"] = settings.session_id
    env["REDIS_URL"] = settings.redis_url
    env["QUEUE_NAME"] = settings.queue_name
    env["MAX_CONCURRENT_JOBS"] = str(settings.max_concurrent_jobs)
    env["JOB_TIMEOUT_SEC"] = str(settings.job_timeout_sec)
    env["JOB_MEMORY_WATCH_INTERVAL_SEC"] = str(settings.job_memory_watch_interval_sec)
    env["JOB_QUEUE_MAX_PER_SESSION"] = str(settings.job_queue_max_per_session)
    if settings.job_memory_budget_bytes:
        env["JOB_MEMORY_BUDGET_BYTES"] = str(settings.job_memory_budget_bytes)
    else:
        env.pop("JOB_MEMORY_BUDGET_BYTES", None)
    exe = Path(sys.executable).with_name("finance-context")
    return subprocess.Popen(
        [str(exe), "worker"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_workers(client: TestClient) -> None:
    workers = getattr(client.app.state, "test_workers", [])
    for proc in workers:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def _install_worker(settings: Settings) -> TestClient:
    app = create_app(settings)
    app.state.test_workers = []

    @app.middleware("http")
    async def _ensure_worker(request, call_next):
        if request.method == "POST" and request.url.path == "/v1/context-jobs":
            if not app.state.test_workers:
                app.state.test_workers.append(_spawn_worker(settings))
        return await call_next(request)

    return TestClient(app)


@contextmanager
def _serving(settings: Settings) -> Iterator[TestClient]:
    client = _install_worker(settings)
    try:
        with client:
            yield client
    finally:
        _stop_workers(client)


@contextmanager
def _app(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(
        data_dir=tmp_path / "data",
        max_upload_bytes=1024 * 1024,
        _env_file=None,
    )
    with _serving(settings) as client:
        yield client


@pytest.fixture(autouse=True)
def _queue_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """API tests share database 15. A missing Redis fails the test."""
    url = local_test_redis_url()
    monkeypatch.setenv("REDIS_URL", url)
    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        try:
            client.ping()
        except RedisError as exc:
            pytest.fail(f"Redis is required for API tests ({url}): {exc}")
        client.flushdb()
        yield
    finally:
        try:
            client.flushdb()
        except RedisError:
            pass
        client.close()


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
