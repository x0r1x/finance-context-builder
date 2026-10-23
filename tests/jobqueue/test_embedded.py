"""The RQ pool `finance-context serve` starts as its own child."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from redis import Redis
from redis.exceptions import RedisError
from tests.helpers.xlsx import CellSpec, SheetSpec, build_xlsx
from tests.jobqueue.conftest import local_test_redis_url

from finance_context.api.app import create_app
from finance_context.jobqueue.embedded import (
    _child_env,
    embedded_worker_ready,
    start_for_serve,
    stop_for_serve,
)
from finance_context.settings import Settings


@pytest.fixture(autouse=True)
def _flush_redis_and_clear_supervisor() -> Iterator[None]:
    url = local_test_redis_url()
    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        try:
            client.ping()
        except RedisError as exc:
            pytest.fail(f"Redis is required for embedded worker tests ({url}): {exc}")
        client.flushdb()
        yield
    finally:
        stop_for_serve(_running_supervisor())
        try:
            client.flushdb()
        except RedisError:
            pass
        client.close()


def _running_supervisor():
    from finance_context.jobqueue import embedded

    return embedded._supervisor


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "redis_url": local_test_redis_url(),
        "session_id": "embed",
        "max_concurrent_jobs": 1,
        "log_json": False,
        "embedded_worker_restart_backoff_sec": 1.0,
        "job_terminate_grace_sec": 0.4,
        "job_kill_grace_sec": 0.4,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _wait_until(predicate, timeout: float = 20) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _xlsx(path: Path) -> Path:
    return build_xlsx(
        path,
        sheets=[
            SheetSpec(
                name="P&L",
                cells=[
                    CellSpec(addr="A1", value="Item", type="s"),
                    CellSpec(addr="B1", value="2024", type="s"),
                    CellSpec(addr="A2", value="Revenue", type="s"),
                    CellSpec(addr="B2", value="100"),
                ],
            )
        ],
        shared_strings=["Item", "2024", "Revenue"],
    )


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


def test_disabled_arena_max_stays_disabled_in_the_child_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JOB_MALLOC_ARENA_MAX", "4")
    monkeypatch.setenv("JOB_MEMORY_BUDGET_BYTES", "999")
    settings = _settings(tmp_path, job_malloc_arena_max=None, job_memory_budget_bytes=None)
    env = _child_env(settings)
    assert env["JOB_MALLOC_ARENA_MAX"] == "0"
    assert "JOB_MEMORY_BUDGET_BYTES" not in env
    dotenv = tmp_path / "child.env"
    dotenv.write_text("JOB_MALLOC_ARENA_MAX=2\n", encoding="utf-8")
    monkeypatch.setenv("JOB_MALLOC_ARENA_MAX", env["JOB_MALLOC_ARENA_MAX"])
    loaded = Settings(_env_file=dotenv)
    assert loaded.job_malloc_arena_max is None

    kept = _child_env(_settings(tmp_path, job_malloc_arena_max=4))
    assert kept["JOB_MALLOC_ARENA_MAX"] == "4"


def test_embedded_worker_rejects_a_non_positive_backoff() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, embedded_worker_restart_backoff_sec=0)


def test_disabled_embedded_worker_does_not_spawn(tmp_path: Path) -> None:
    settings = _settings(tmp_path, embedded_worker=False)
    assert start_for_serve(settings) is None
    assert embedded_worker_ready() is None
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_embedded_worker_starts_and_stops(tmp_path: Path) -> None:
    settings = _settings(tmp_path, job_terminate_grace_sec=5, job_kill_grace_sec=5)
    worker = start_for_serve(settings)
    assert worker is not None
    assert _wait_until(worker.healthy)
    pid = worker.pid()
    assert pid is not None and _alive(pid)
    with TestClient(create_app(settings)) as client:
        assert client.get("/readyz").status_code == 200
    started = time.monotonic()
    stop_for_serve(worker)
    elapsed = time.monotonic() - started
    assert elapsed < 3
    assert not _alive(pid)
    assert embedded_worker_ready() is None
    time.sleep(0.3)
    assert not _alive(pid)


def test_dead_embedded_worker_is_not_ready_and_restarts(tmp_path: Path) -> None:
    settings = _settings(tmp_path, embedded_worker_restart_backoff_sec=1.0, readyz_cache_ttl_sec=30)
    worker = start_for_serve(settings)
    assert worker is not None
    assert _wait_until(worker.healthy)
    pid = worker.pid()
    assert pid is not None
    with TestClient(create_app(settings)) as client:
        assert client.get("/readyz").status_code == 200
        os.kill(pid, signal.SIGKILL)
        assert _wait_until(lambda: not worker.healthy(), timeout=5)
        # The disk and Redis probe stays cached. The dead child is still not ready.
        down = client.get("/readyz")
        assert down.status_code == 503
        assert down.json()["status"] == "unavailable"
        assert _wait_until(lambda: worker.healthy() and worker.pid() not in {None, pid}, timeout=8)
        up = client.get("/readyz")
    assert up.status_code == 200
    assert up.json()["status"] == "ready"


def test_stop_during_a_job_waits_out_the_grace_then_reports_lost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pause = tmp_path / "pause-parse"
    pause.write_text("hold", encoding="utf-8")
    monkeypatch.setenv("FINANCE_CONTEXT_PAUSE_STAGE", "parse")
    monkeypatch.setenv("FINANCE_CONTEXT_PAUSE_FILE", str(pause))
    settings = _settings(tmp_path, job_terminate_grace_sec=0.4, job_kill_grace_sec=0.4)
    worker = start_for_serve(settings)
    assert worker is not None
    assert _wait_until(worker.healthy)
    source = _xlsx(tmp_path / "model.xlsx")
    try:
        with TestClient(create_app(settings)) as client:
            created = _upload(client, source)
            assert created.status_code == 202
            job_id = created.json()["job_id"]
            meta = tmp_path / "data" / "sessions" / "embed" / "jobs" / job_id / "meta.json"
            assert _wait_until(lambda: _stage(meta) == "parse", timeout=30)
            started = time.monotonic()
            stop_for_serve(worker)
            elapsed = time.monotonic() - started
            assert elapsed >= 0.7
            assert _wait_until(lambda: _lost(client, job_id), timeout=15)
    finally:
        pause.unlink(missing_ok=True)


def test_serve_starts_the_worker_before_uvicorn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    monkeypatch.setenv("EMBEDDED_WORKER", "true")
    monkeypatch.setenv("REDIS_URL", local_test_redis_url())
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SESSION_ID", "embed")
    monkeypatch.setenv("LOG_JSON", "false")
    monkeypatch.setenv("JOB_TERMINATE_GRACE_SEC", "0.4")
    monkeypatch.setenv("JOB_KILL_GRACE_SEC", "0.4")
    seen: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object) -> None:
        del args
        seen["workers"] = kwargs.get("workers")
        seen["ready"] = embedded_worker_ready()

    monkeypatch.setattr(uvicorn, "run", fake_run)
    from finance_context.cli import serve

    serve()
    assert seen == {"workers": 1, "ready": True}
    assert embedded_worker_ready() is None


def test_serve_with_embedded_worker_disabled_does_not_supervise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import uvicorn

    monkeypatch.setenv("EMBEDDED_WORKER", "false")
    monkeypatch.setenv("REDIS_URL", local_test_redis_url())
    monkeypatch.setenv("LOG_JSON", "false")

    def fake_run(*args: object, **kwargs: object) -> None:
        del args, kwargs
        assert embedded_worker_ready() is None

    monkeypatch.setattr(uvicorn, "run", fake_run)
    from finance_context.cli import serve

    serve()
    assert embedded_worker_ready() is None


def test_serve_alone_completes_a_job(tmp_path: Path) -> None:
    port = _free_port()
    data_dir = tmp_path / "data"
    env = os.environ.copy()
    env["REDIS_URL"] = local_test_redis_url()
    env["DATA_DIR"] = str(data_dir)
    env["SESSION_ID"] = "embed"
    env["EMBEDDED_WORKER"] = "true"
    env["LOG_JSON"] = "false"
    env["LOG_LEVEL"] = "INFO"
    env["MAX_CONCURRENT_JOBS"] = "1"
    exe = Path(sys.executable).with_name("finance-context")
    proc = subprocess.Popen(
        [str(exe), "serve", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    source = _xlsx(tmp_path / "model.xlsx")
    try:
        assert _wait_until(lambda: _http_ready(base), timeout=30)
        body = _post_workbook(base, source)
        job_id = body["job_id"]
        assert _wait_until(lambda: _job_status(base, job_id) == "succeeded", timeout=60)
    finally:
        if proc.poll() is None:
            os.kill(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.kill(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _pids_using(data_dir):
            time.sleep(0.05)
        assert _pids_using(data_dir) == []


def _stage(meta: Path) -> str | None:
    try:
        payload = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    stage = payload.get("stage")
    return str(stage) if stage else None


def _lost(client: TestClient, job_id: str) -> bool:
    response = client.get(f"/v1/context-jobs/{job_id}")
    return response.json().get("error") in {"queue_lost", "process_lost"}


def _pids_using(data_dir: Path) -> list[int]:
    needle = str(data_dir).encode()
    found: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            env = (entry / "environ").read_bytes()
            command = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if needle in env and b"finance-context" in command:
            found.append(int(entry.name))
    return found


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_ready(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/readyz", timeout=1) as response:
            payload = json.loads(response.read().decode())
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False
    return payload.get("status") == "ready"


def _post_workbook(base: str, source: Path) -> dict:
    boundary = "----embedded-worker"
    payload = source.read_bytes()
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="model.xlsx"\r\n'
        "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n"
        "\r\n"
    ).encode()
    body = head + payload + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{base}/v1/context-jobs",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def _job_status(base: str, job_id: str) -> str | None:
    try:
        with urllib.request.urlopen(f"{base}/v1/context-jobs/{job_id}", timeout=5) as response:
            payload = json.loads(response.read().decode())
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    status = payload.get("status")
    return str(status) if status else None
