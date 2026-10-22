from __future__ import annotations

from pathlib import Path

from finance_context.jobqueue.pool import worker_check
from finance_context.settings import Settings


def test_worker_check_fails_when_redis_is_down(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/15")
    monkeypatch.setenv("REDIS_CONNECT_TIMEOUT_SEC", "0.2")
    settings = Settings(_env_file=None)
    assert worker_check(settings) == 1


def test_worker_check_pings_redis(redis_settings: Settings, tmp_path: Path, monkeypatch) -> None:
    del redis_settings
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = Settings(_env_file=None)
    assert settings.redis_url.endswith("/15")
    assert worker_check(settings) == 0
