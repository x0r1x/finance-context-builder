"""Event-loop isolation for the API: locks, readiness, and HEAD."""

from __future__ import annotations

import fcntl
import json
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.helpers.slice_book import REVENUE, write_slice_job

from finance_context.api.app import create_app
from finance_context.api.routes.jobs import _load_meta
from finance_context.models.context import ContextDocument
from finance_context.observability import JsonFormatter
from finance_context.settings import Settings
from finance_context.store.fs import file_lock
from finance_context.store.paths import session_job_dir

_JOB = "ab" * 32
_LOCK_HOLD_SEC = 8
_HEALTHZ_MAX_SEC = 0.1


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        _env_file=None,
        **overrides,  # type: ignore[arg-type]
    )


def test_lifespan_sets_thread_limit_and_logs_it(tmp_path: Path, caplog) -> None:
    settings = _settings(tmp_path, api_thread_limit=7, readyz_cache_ttl_sec=3)
    app = create_app(settings)
    caplog.set_level(logging.INFO, logger="finance_context")
    logging.getLogger("finance_context").addHandler(caplog.handler)
    with TestClient(app) as client:
        limiter = client.app.state.ctx.thread_limiter
        assert limiter.total_tokens == 7
    rows = [record for record in caplog.records if record.__dict__.get("event") == "api_config"]
    assert len(rows) == 1
    payload = json.loads(JsonFormatter().format(rows[0]))
    assert payload["thread_limit"] == 7
    assert payload["readyz_ttl_sec"] == 3


def test_lifespan_shutdown_nonblocking(tmp_path: Path) -> None:
    client = TestClient(create_app(_settings(tmp_path)))
    client.__enter__()
    started = time.monotonic()
    # Shutdown must not join a worker or a horse.
    client.__exit__(None, None, None)
    assert time.monotonic() - started < 1


def test_load_meta_does_not_flock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dest = session_job_dir(tmp_path / "data", _JOB, "local")
    dest.mkdir(parents=True)
    (dest / "meta.json").write_text(
        json.dumps({"job_id": _JOB, "status": "succeeded", "stage": "done"}),
        encoding="utf-8",
    )

    def _refuse(*_args: object, **_kwargs: object):
        raise AssertionError("meta.json was read under flock")

    monkeypatch.setattr("finance_context.api.routes.jobs.file_lock", _refuse)
    monkeypatch.setattr("finance_context.store.fs.file_lock", _refuse)
    loaded = _load_meta(dest)
    assert loaded is not None
    assert loaded["status"] == "succeeded"


def test_get_job_does_not_block_loop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A blocking meta.json.lock held for 8s must not stall /healthz.

    GET takes that lock only when it is about to write queue_lost, and the
    wait runs on a worker thread. /healthz stays on the event loop.
    """
    dest = session_job_dir(tmp_path / "data", _JOB, "local")
    dest.mkdir(parents=True)
    (dest / "meta.json").write_text(
        json.dumps({"job_id": _JOB, "status": "queued", "stage": "queued", "generation": 1}),
        encoding="utf-8",
    )
    lock_path = dest / "meta.json.lock"
    handle = lock_path.open("a+")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    release = threading.Event()

    def _hold() -> None:
        release.wait(timeout=_LOCK_HOLD_SEC)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    threading.Thread(target=_hold, daemon=True).start()
    reached = threading.Event()
    original = file_lock

    @contextmanager
    def _watching(path: Path, *, blocking: bool = True):
        if Path(path).name == "meta.json.lock" and blocking:
            reached.set()
        with original(path, blocking=blocking) as locked:
            yield locked

    monkeypatch.setattr("finance_context.store.fs.file_lock", _watching)
    outcome: list[object] = []

    def _get(client: TestClient) -> None:
        try:
            response = client.get(f"/v1/context-jobs/{_JOB}")
            outcome.append(response.status_code)
            outcome.append(response.json())
        except Exception as exc:
            outcome.append(exc)

    try:
        with TestClient(create_app(_settings(tmp_path))) as client:
            getter = threading.Thread(target=_get, args=(client,), daemon=True)
            getter.start()
            assert reached.wait(timeout=5), "GET did not reach the meta lock"
            started = time.monotonic()
            health = client.get("/healthz")
            elapsed = time.monotonic() - started
            assert health.status_code == 200
            assert health.json() == {"status": "ok"}
            assert elapsed < _HEALTHZ_MAX_SEC
            assert getter.is_alive()
            release.set()
            getter.join(timeout=_LOCK_HOLD_SEC + 4)
            assert not getter.is_alive()
    finally:
        release.set()
    assert outcome[0] == 200
    assert isinstance(outcome[1], dict)
    assert outcome[1]["error"] == "queue_lost"


def test_readyz_caches_the_probe_and_creates_no_file(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    marker = data / "keep"
    marker.write_text("x", encoding="utf-8")
    calls = {"n": 0}
    settings = _settings(tmp_path, readyz_cache_ttl_sec=10)
    with TestClient(create_app(settings)) as client:
        queue = client.app.state.ctx.queue
        health = queue.health

        def _count() -> bool:
            calls["n"] += 1
            return health()

        queue.health = _count
        before = sorted(path.name for path in data.iterdir())
        first = client.get("/readyz")
        second = client.get("/readyz")
        after = sorted(path.name for path in data.iterdir())
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "ready"
    assert calls["n"] == 1
    assert after == before
    assert list(data.glob(".ready-*")) == []


def test_readyz_refreshes_after_the_ttl(tmp_path: Path) -> None:
    calls = {"n": 0}
    settings = _settings(tmp_path, readyz_cache_ttl_sec=10)
    with TestClient(create_app(settings)) as client:
        queue = client.app.state.ctx.queue
        health = queue.health

        def _count() -> bool:
            calls["n"] += 1
            return health()

        queue.health = _count
        assert client.get("/readyz").status_code == 200
        cached = client.app.state.ready_cache
        cached.at = time.monotonic() - settings.readyz_cache_ttl_sec
        assert client.get("/readyz").status_code == 200
    assert calls["n"] == 2


def test_head_uses_stat_and_does_not_build_a_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_slice_job(tmp_path / "data", _JOB)
    real_read = Path.read_text

    def _read(self: Path, *args: object, **kwargs: object) -> str:
        if self.name in {"context.json", "graph.json"}:
            raise AssertionError(f"{self.name} was read for HEAD")
        return real_read(self, *args, **kwargs)

    def _refuse(*_args: object, **_kwargs: object):
        raise AssertionError("projection was built")

    monkeypatch.setattr(Path, "read_text", _read)
    monkeypatch.setattr(ContextDocument, "model_validate", _refuse)
    monkeypatch.setattr("finance_context.api.routes.slices.build_catalog", _refuse)
    monkeypatch.setattr("finance_context.api.routes.slices.build_summary", _refuse)
    monkeypatch.setattr("finance_context.api.routes.slices.build_observations", _refuse)
    with TestClient(create_app(_settings(tmp_path))) as client:
        catalog = client.head(f"/v1/context-jobs/{_JOB}/catalog")
        summary = client.head(f"/v1/context-jobs/{_JOB}/summary")
        observations = client.head(
            f"/v1/context-jobs/{_JOB}/observations",
            params={"row_key": REVENUE},
        )
    for response in (catalog, summary, observations):
        assert response.status_code == 200
        assert response.content == b""
        assert response.headers["etag"].startswith('"')
        assert "no-cache" in response.headers["cache-control"]
