"""SSE job progress: snapshot, resume, heartbeat, terminal close, and errors."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finance_context.api.app import create_app
from finance_context.jobqueue.progress import ProgressPublisher, read_events
from finance_context.jobqueue.queue import QueueUnavailable, redis_client
from finance_context.settings import Settings
from finance_context.store.paths import session_job_dir

_JOB = "ab" * 32


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path / "data",
        "job_events_heartbeat_sec": 0.2,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _meta(settings: Settings, job_id: str = _JOB, **fields: object) -> None:
    dest = session_job_dir(settings.data_dir, job_id, settings.session_id)
    dest.mkdir(parents=True, exist_ok=True)
    body = {"job_id": job_id, "status": "running", "stage": "parse", "generation": 1}
    body.update(fields)
    (dest / "meta.json").write_text(json.dumps(body), encoding="utf-8")


def _publish(settings: Settings, **fields: object) -> str | None:
    client = redis_client(settings, socket_connect_timeout=2, socket_timeout=2)
    try:
        body: dict[str, object] = {
            "session_id": settings.session_id,
            "job_id": _JOB,
            "event": "stage_start",
            "status": "running",
            "stage": "compile",
            "generation": 1,
        }
        body.update(fields)
        return ProgressPublisher(settings, client).publish_now(**body)
    finally:
        client.close()


def test_snapshot_of_a_finished_job_is_first_and_closes(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _meta(settings, status="succeeded", stage="done")
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/v1/context-jobs/{_JOB}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert body.startswith("event: snapshot\n")
    assert '"status":"succeeded"' in body
    assert ": heartbeat" not in body
    assert "\nid:" not in body


def test_last_event_id_resumes_after_that_entry(tmp_path: Path) -> None:
    settings = _settings(tmp_path, job_events_heartbeat_sec=2)
    _meta(settings, status="running", stage="compile")
    first = _publish(settings, event="stage_start", stage="compile")
    assert first is not None
    done = _publish(settings, event="succeeded", status="succeeded", stage="done")
    assert done is not None
    with TestClient(create_app(settings)) as client:
        response = client.get(
            f"/v1/context-jobs/{_JOB}/events",
            headers={"Last-Event-ID": first},
        )
    body = response.text
    assert response.status_code == 200
    assert body.startswith("event: snapshot\n")
    assert "event: succeeded" in body
    assert "event: stage_start" not in body
    assert ": heartbeat" not in body


def test_heartbeat_comment_while_the_job_is_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty read is a comment, then a terminal event ends the body.

    TestClient buffers the response until the generator returns, so the
    stream has to close inside this test.
    """
    settings = _settings(tmp_path)
    _meta(settings)
    calls = 0

    def _wrapped(client: object, key: str, last_id: str, block_ms: int):
        nonlocal calls
        del block_ms
        calls += 1
        if calls == 1:
            return []
        entry = _publish(settings, event="succeeded", status="succeeded", stage="done")
        assert entry is not None
        return read_events(client, key, "0-0", 1)  # type: ignore[arg-type]

    monkeypatch.setattr("finance_context.api.routes.events.read_events", _wrapped)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/v1/context-jobs/{_JOB}/events")
    assert response.status_code == 200
    assert response.text.startswith("event: snapshot\n")
    assert ": heartbeat" in response.text
    assert "event: succeeded" in response.text
    assert calls == 2


def test_stream_closes_after_a_terminal_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, job_events_heartbeat_sec=2)
    _meta(settings, status="running", stage="parse")
    entered = threading.Event()
    deadline = time.monotonic() + 3

    def _wrapped(client: object, key: str, last_id: str, block_ms: int):
        entered.set()
        if time.monotonic() > deadline:
            raise RuntimeError("terminal progress event did not arrive")
        return read_events(client, key, last_id, min(block_ms, 500))  # type: ignore[arg-type]

    monkeypatch.setattr("finance_context.api.routes.events.read_events", _wrapped)
    published: list[str | None] = []

    def _later() -> None:
        if not entered.wait(3):
            return
        time.sleep(0.1)
        published.append(
            _publish(
                settings,
                event="failed",
                status="failed",
                stage="failed",
                error="workbook_too_large",
            )
        )

    threading.Thread(target=_later, daemon=True).start()
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/v1/context-jobs/{_JOB}/events")
    assert response.status_code == 200
    body = response.text
    assert body.startswith("event: snapshot\n")
    assert "event: failed" in body
    assert "workbook_too_large" in body
    assert published and published[0] is not None


def test_unknown_job_is_not_found(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.get(f"/v1/context-jobs/{_JOB}/events")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_redis_down_is_queue_unavailable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _meta(settings, status="running", stage="parse")

    def _down() -> bool:
        raise QueueUnavailable("Redis is unavailable")

    with TestClient(create_app(settings)) as client:
        client.app.state.ctx.queue.health = _down  # type: ignore[method-assign]
        response = client.get(f"/v1/context-jobs/{_JOB}/events")
    assert response.status_code == 503
    assert response.json()["error"] == "queue_unavailable"


def test_events_stay_inside_the_session(tmp_path: Path) -> None:
    root = tmp_path / "data"
    dest = root / "sessions" / "alpha" / "jobs" / _JOB
    dest.mkdir(parents=True)
    (dest / "meta.json").write_text(
        json.dumps({"job_id": _JOB, "status": "succeeded", "stage": "done"}),
        encoding="utf-8",
    )
    beta = Settings(
        data_dir=root,
        session_id="beta",
        job_events_heartbeat_sec=0.2,
        _env_file=None,
    )
    with TestClient(create_app(beta)) as client:
        missing = client.get(f"/v1/context-jobs/{_JOB}/events")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"
    alpha = Settings(
        data_dir=root,
        session_id="alpha",
        job_events_heartbeat_sec=0.2,
        _env_file=None,
    )
    with TestClient(create_app(alpha)) as client:
        found = client.get(f"/v1/context-jobs/{_JOB}/events")
    assert found.status_code == 200
    assert found.text.startswith("event: snapshot\n")
    assert '"status":"succeeded"' in found.text
