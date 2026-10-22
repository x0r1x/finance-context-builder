"""REDIS_USERNAME and REDIS_PASSWORD, separate from REDIS_URL.

ACL changes go through redis-py on the suite Redis (database 15). CI has that
server and no redis-cli or redis-server binary. Each test restores the default
user to nopass and deletes the ``app`` user before it returns.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from redis import Redis
from redis.exceptions import AuthenticationError, RedisError
from tests.jobqueue.conftest import local_test_redis_url

from finance_context.api.app import create_app
from finance_context.jobqueue.pool import _log_queue_config, serve_workers, worker_check
from finance_context.jobqueue.queue import redis_client
from finance_context.settings import Settings, write_redis_env

_SECRET = "queue-secret"


def _settings(
    url: str,
    *,
    redis_username: str = "",
    redis_password: SecretStr | None = None,
) -> Settings:
    return Settings(
        redis_url=url,
        redis_username=redis_username,
        redis_password=redis_password,
        _env_file=None,
    )


def _userinfo(url: str, userinfo: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parts.port}" if parts.port else ""
    return urlunsplit((parts.scheme, f"{userinfo}@{host}{port}", parts.path, "", ""))


def _grant(client: Redis, username: str, *, password: str | None) -> None:
    common: dict[str, object] = {
        "enabled": True,
        "reset": True,
        "categories": ["+@all"],
        "keys": ["*"],
        "channels": ["*"],
    }
    if password is None:
        client.acl_setuser(username, nopass=True, **common)  # type: ignore[arg-type]
        return
    client.acl_setuser(username, passwords=[f"+{password}"], **common)  # type: ignore[arg-type]


def _restore(client: Redis) -> None:
    _grant(client, "default", password=None)
    client.acl_deluser("app")


def _open_admin(url: str) -> Redis:
    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    except AuthenticationError:
        client.close()
        client = Redis.from_url(
            url,
            password=_SECRET,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        client.ping()
        _restore(client)
    return client


@pytest.fixture
def admin() -> Iterator[Redis]:
    url = local_test_redis_url()
    try:
        client = _open_admin(url)
    except RedisError as exc:
        pytest.fail(f"Redis is required for auth tests ({url}): {exc}")
    try:
        yield client
    finally:
        try:
            _restore(client)
        finally:
            client.close()


@pytest.fixture
def password_redis(admin: Redis) -> str:
    _grant(admin, "default", password=_SECRET)
    return local_test_redis_url()


@pytest.fixture
def app_user_redis(admin: Redis) -> str:
    _grant(admin, "app", password=_SECRET)
    return local_test_redis_url()


def _command_has_auth(command: str) -> bool:
    return "AUTH" in command.split()


def _saw_ping(commands: list[str]) -> bool:
    return any("PING" in command.split() for command in commands)


def test_unset_credentials_pass_no_auth_kwargs() -> None:
    url = "redis://host:6379/0"
    settings = _settings(url)
    resolved, kwargs = settings.redis_from_url_args()
    assert resolved == url
    assert kwargs == {}
    assert "username" not in kwargs
    assert "password" not in kwargs


def test_credentialless_url_sends_no_auth() -> None:
    url = local_test_redis_url()
    commands: list[str] = []
    started = threading.Event()
    failed: list[BaseException] = []
    watcher = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=5)

    def _watch() -> None:
        try:
            with watcher.monitor() as monitor:
                started.set()
                while True:
                    item = monitor.next_command()
                    if item is not None:
                        commands.append(str(item["command"]))
        except (RedisError, OSError, ValueError) as exc:
            failed.append(exc)

    thread = threading.Thread(target=_watch, daemon=True)
    thread.start()
    assert started.wait(5), failed
    client = redis_client(_settings(url), socket_connect_timeout=2, socket_timeout=2)
    assert client.ping() is True
    client.close()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not _saw_ping(commands):
        time.sleep(0.05)
    watcher.close()
    thread.join(timeout=2)
    assert _saw_ping(commands), (commands, failed)
    assert not any(_command_has_auth(command) for command in commands)


def test_password_is_passed_and_overrides_url_userinfo(password_redis: str) -> None:
    settings = _settings(
        _userinfo(password_redis, ":wrong-pass"),
        redis_password=SecretStr(_SECRET),
    )
    url, kwargs = settings.redis_from_url_args()
    assert "wrong-pass" not in url
    assert kwargs == {"password": _SECRET}
    client = redis_client(settings, socket_connect_timeout=2, socket_timeout=2)
    assert client.ping() is True
    client.close()


def test_username_overrides_url_userinfo(app_user_redis: str) -> None:
    settings = _settings(
        _userinfo(app_user_redis, "other:wrong-pass"),
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


def test_url_password_still_authenticates_when_env_is_unset(password_redis: str) -> None:
    settings = _settings(_userinfo(password_redis, f":{_SECRET}"))
    url, kwargs = settings.redis_from_url_args()
    assert kwargs == {}
    assert url.startswith("redis://:")
    client = redis_client(settings, socket_connect_timeout=2, socket_timeout=2)
    assert client.ping() is True
    client.close()


def test_username_without_password_is_rejected() -> None:
    url = local_test_redis_url()
    with pytest.raises(ValidationError, match="REDIS_USERNAME requires REDIS_PASSWORD") as caught:
        _settings(url, redis_username="app")
    assert _SECRET not in str(caught.value)
    with pytest.raises(ValidationError, match="REDIS_USERNAME requires REDIS_PASSWORD"):
        _settings(url, redis_username="app", redis_password=SecretStr(""))
    with pytest.raises(ValidationError, match="REDIS_USERNAME requires REDIS_PASSWORD"):
        _settings(_userinfo(url, "app"))


def test_secret_is_absent_from_logs_repr_and_readyz(
    password_redis: str, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        redis_url=_userinfo(password_redis, ":wrong-pass"),
        redis_username="",
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


def test_worker_check_requires_the_password(password_redis: str, tmp_path: Path) -> None:
    authed = Settings(
        data_dir=tmp_path,
        redis_url=password_redis,
        redis_username="",
        redis_password=SecretStr(_SECRET),
        _env_file=None,
    )
    assert worker_check(authed) == 0
    bare = Settings(
        data_dir=tmp_path,
        redis_url=password_redis,
        redis_username="",
        redis_password=None,
        _env_file=None,
    )
    assert worker_check(bare) == 1


def test_worker_pool_receives_the_password(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class _Pool:
        def __init__(self, queues: object, *, connection: object, **kwargs: object) -> None:
            del queues, kwargs
            seen["connection"] = connection

        def start(self, **kwargs: object) -> None:
            del kwargs
            seen["started"] = True

    monkeypatch.setattr("finance_context.jobqueue.pool.WorkerPool", _Pool)
    settings = _settings(
        local_test_redis_url(),
        redis_username="app",
        redis_password=SecretStr(_SECRET),
    )
    serve_workers(settings)
    connection = seen["connection"]
    assert seen["started"] is True
    pool = connection.connection_pool  # type: ignore[attr-defined]
    assert pool.connection_kwargs["username"] == "app"
    assert pool.connection_kwargs["password"] == _SECRET
    assert _SECRET not in repr(pool)


def test_child_env_carries_credentials_and_drops_them_when_unset() -> None:
    url = local_test_redis_url()
    env = {"REDIS_USERNAME": "stale", "REDIS_PASSWORD": "stale-secret"}
    write_redis_env(env, _settings(url))
    assert env["REDIS_URL"] == url
    assert "REDIS_USERNAME" not in env
    assert "REDIS_PASSWORD" not in env
    write_redis_env(
        env,
        _settings(url, redis_username="app", redis_password=SecretStr(_SECRET)),
    )
    assert env["REDIS_USERNAME"] == "app"
    assert env["REDIS_PASSWORD"] == _SECRET
