"""REDIS_USERNAME and REDIS_PASSWORD, separate from REDIS_URL."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from redis import Redis
from redis.exceptions import RedisError

from finance_context.api.app import create_app
from finance_context.jobqueue.pool import _log_queue_config, serve_workers, worker_check
from finance_context.jobqueue.queue import redis_client
from finance_context.settings import Settings, write_redis_env

_PORT = 6391
_SECRET = "queue-secret"
_URL = f"redis://127.0.0.1:{_PORT}/0"


def _argv() -> list[str]:
    return [
        "redis-server",
        "--port",
        str(_PORT),
        "--bind",
        "127.0.0.1",
        "--save",
        "",
        "--appendonly",
        "no",
        "--daemonize",
        "yes",
        "--protected-mode",
        "yes",
        "--dir",
        "/tmp",
        "--dbfilename",
        "dump-6391.rdb",
        "--user",
        "default",
        "on",
        f">{_SECRET}",
        "~*",
        "&*",
        "+@all",
        "--user",
        "app",
        "on",
        f">{_SECRET}",
        "~*",
        "&*",
        "+@all",
    ]


def _shutdown() -> None:
    subprocess.run(
        ["redis-cli", "-p", str(_PORT), "--no-auth-warning", "-a", _SECRET, "SHUTDOWN", "NOSAVE"],
        check=False,
        capture_output=True,
    )


@pytest.fixture(scope="module")
def acl_redis() -> str:
    _shutdown()
    subprocess.run(_argv(), check=True)
    deadline = time.monotonic() + 5
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            client = Redis.from_url(_URL, password=_SECRET, socket_connect_timeout=0.5)
            client.ping()
            client.close()
            break
        except RedisError as exc:
            last = exc
            time.sleep(0.05)
    else:
        pytest.fail(f"ACL Redis on {_PORT} did not start: {last}")
    yield _URL
    _shutdown()


def _settings(url: str, **overrides: object) -> Settings:
    return Settings(redis_url=url, _env_file=None, **overrides)  # type: ignore[arg-type]


def test_unset_credentials_pass_no_auth_kwargs() -> None:
    settings = _settings("redis://127.0.0.1:6390/15")
    url, kwargs = settings.redis_from_url_args()
    assert url == "redis://127.0.0.1:6390/15"
    assert kwargs == {}


def test_credentialless_url_sends_no_auth() -> None:
    url = "redis://127.0.0.1:6390/15"
    commands: list[str] = []
    started = threading.Event()
    watcher = Redis.from_url(url, socket_timeout=1)

    def _watch() -> None:
        try:
            with watcher.monitor() as monitor:
                started.set()
                while True:
                    item = monitor.next_command()
                    if item is not None:
                        commands.append(item["command"])
        except (RedisError, OSError, ValueError):
            return

    thread = threading.Thread(target=_watch, daemon=True)
    thread.start()
    assert started.wait(2)
    client = redis_client(_settings(url), socket_connect_timeout=2, socket_timeout=2)
    assert client.ping() is True
    client.close()
    time.sleep(0.2)
    watcher.close()
    thread.join(timeout=2)
    assert commands
    assert not any(command == "AUTH" or command.startswith("AUTH ") for command in commands)


def test_password_is_passed_and_overrides_url_userinfo(acl_redis: str) -> None:
    settings = _settings(
        "redis://:wrong-pass@127.0.0.1:6391/0",
        redis_password=SecretStr(_SECRET),
    )
    url, kwargs = settings.redis_from_url_args()
    assert "wrong-pass" not in url
    assert kwargs == {"password": _SECRET}
    client = redis_client(settings, socket_connect_timeout=2, socket_timeout=2)
    assert client.ping() is True
    client.close()


def test_username_overrides_url_userinfo(acl_redis: str) -> None:
    settings = _settings(
        "redis://other:wrong-pass@127.0.0.1:6391/0",
        redis_username="app",
        redis_password=SecretStr(_SECRET),
    )
    url, kwargs = settings.redis_from_url_args()
    assert "other" not in url
    assert "wrong-pass" not in url
    assert kwargs == {"username": "app", "password": _SECRET}
    client = redis_client(settings, socket_connect_timeout=2, socket_timeout=2)
    assert client.ping() is True
    client.close()


def test_url_password_still_authenticates_when_env_is_unset(acl_redis: str) -> None:
    settings = _settings(f"redis://:{_SECRET}@127.0.0.1:6391/0")
    url, kwargs = settings.redis_from_url_args()
    assert kwargs == {}
    assert url.startswith("redis://:")
    client = redis_client(settings, socket_connect_timeout=2, socket_timeout=2)
    assert client.ping() is True
    client.close()


def test_username_without_password_is_rejected(acl_redis: str) -> None:
    with pytest.raises(ValidationError, match="REDIS_USERNAME requires REDIS_PASSWORD") as caught:
        _settings(acl_redis, redis_username="app")
    assert _SECRET not in str(caught.value)
    with pytest.raises(ValidationError, match="REDIS_USERNAME requires REDIS_PASSWORD"):
        _settings(acl_redis, redis_username="app", redis_password=SecretStr(""))
    with pytest.raises(ValidationError, match="REDIS_USERNAME requires REDIS_PASSWORD"):
        _settings("redis://app@127.0.0.1:6391/0")


def test_secret_is_absent_from_logs_repr_and_readyz(
    acl_redis: str, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        redis_url="redis://:wrong-pass@127.0.0.1:6391/0",
        redis_password=SecretStr(_SECRET),
        _env_file=None,
    )
    caplog.set_level(logging.INFO)
    _log_queue_config(settings)
    assert _SECRET not in caplog.text
    assert "wrong-pass" not in caplog.text
    assert _SECRET not in repr(settings)
    assert _SECRET not in str(settings)
    with TestClient(create_app(settings)) as client:
        response = client.get("/readyz")
    assert response.status_code == 200
    assert _SECRET not in response.text
    assert "wrong-pass" not in response.text


def test_worker_check_requires_the_password(acl_redis: str, tmp_path: Path) -> None:
    authed = Settings(
        data_dir=tmp_path,
        redis_url=acl_redis,
        redis_password=SecretStr(_SECRET),
        _env_file=None,
    )
    assert worker_check(authed) == 0
    bare = Settings(data_dir=tmp_path, redis_url=acl_redis, _env_file=None)
    assert worker_check(bare) == 1


def test_worker_pool_receives_the_password(acl_redis: str, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class _Pool:
        def __init__(self, queues: object, *, connection: object, **kwargs: object) -> None:
            del queues, kwargs
            seen["connection"] = connection

        def start(self, **kwargs: object) -> None:
            del kwargs
            seen["started"] = True

    monkeypatch.setattr("finance_context.jobqueue.pool.WorkerPool", _Pool)
    settings = _settings(acl_redis, redis_username="app", redis_password=SecretStr(_SECRET))
    serve_workers(settings)
    connection = seen["connection"]
    assert seen["started"] is True
    pool = connection.connection_pool  # type: ignore[attr-defined]
    assert pool.connection_kwargs["username"] == "app"
    assert pool.connection_kwargs["password"] == _SECRET
    assert _SECRET not in repr(pool)


def test_child_env_carries_credentials_and_drops_them_when_unset() -> None:
    env = {"REDIS_USERNAME": "stale", "REDIS_PASSWORD": "stale-secret"}
    write_redis_env(env, _settings("redis://127.0.0.1:6390/15"))
    assert env["REDIS_URL"] == "redis://127.0.0.1:6390/15"
    assert "REDIS_USERNAME" not in env
    assert "REDIS_PASSWORD" not in env
    write_redis_env(
        env,
        _settings(
            "redis://127.0.0.1:6391/0",
            redis_username="app",
            redis_password=SecretStr(_SECRET),
        ),
    )
    assert env["REDIS_USERNAME"] == "app"
    assert env["REDIS_PASSWORD"] == _SECRET
