from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import pytest
from redis import Redis
from redis.exceptions import RedisError

from finance_context.settings import Settings

_LOCAL = frozenset({"127.0.0.1", "localhost", "::1"})


def local_test_redis_url() -> str:
    """The caller's REDIS_URL, forced onto database 15.

    Unset means the throwaway server on port 6390. A non-local host is refused
    so the suite cannot flush the shared finance-stack instance. Database 0 is
    never selected.
    """
    raw = os.environ.get("REDIS_URL", "redis://127.0.0.1:6390/0")
    parts = urlsplit(raw)
    host = (parts.hostname or "").lower()
    if host not in _LOCAL:
        raise RuntimeError(f"refusing non-local Redis host {host!r}")
    if parts.scheme not in {"redis", "rediss", "unix"}:
        raise RuntimeError(f"refusing Redis URL scheme {parts.scheme!r}")
    return urlunsplit((parts.scheme, parts.netloc, "/15", "", ""))


@pytest.fixture
def redis_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    url = local_test_redis_url()
    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    try:
        try:
            client.ping()
        except RedisError as exc:
            pytest.fail(f"Redis is required for queue tests ({url}): {exc}")
        client.flushdb()
        monkeypatch.setenv("REDIS_URL", url)
        yield Settings(_env_file=None)
    finally:
        try:
            client.flushdb()
        except RedisError:
            pass
        client.close()
