from __future__ import annotations

import pytest
from pydantic import ValidationError

from finance_context.settings import Settings
from finance_context.settings_env import ENV_SPECS


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for spec in ENV_SPECS:
        monkeypatch.delenv(spec.env_name, raising=False)


def test_queue_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    settings = Settings(_env_file=None)
    assert settings.redis_url == "redis://redis:6379/0"
    assert settings.redis_connect_timeout_sec == 2
    assert settings.queue_name == "finance-context"
    assert settings.job_queue_max_per_session == 3
    assert settings.queue_job_timeout_margin_sec == 120
    assert settings.queue_job_ttl_sec == 0
    assert settings.queue_result_ttl_sec == 3600
    assert settings.queue_failure_ttl_sec == 604800
    assert settings.job_retry_on_lost == 1
    assert settings.shared_cache_busy_retry_sec == 30
    assert settings.shared_cache_busy_max_retries == 120
    assert settings.job_admission_cgroup_check is True
    assert settings.job_admission_retry_sec == 30
    assert settings.job_parent_watch_interval_sec == 1
    assert settings.job_retry_after_sec == 30
    assert settings.job_terminate_grace_sec == 5
    assert settings.job_kill_grace_sec == 5
    # 3600 + 5 + 5 + 120
    assert settings.queue_job_timeout_sec() == 3730


@pytest.mark.parametrize(
    "value",
    ["", "  ", "http://127.0.0.1:6379/0", "localhost:6379"],
)
def test_redis_url_rejects_a_non_redis_address(value: str) -> None:
    with pytest.raises(ValidationError, match="REDIS_URL"):
        Settings(_env_file=None, redis_url=value)


def test_queue_name_is_one_token() -> None:
    with pytest.raises(ValidationError, match="QUEUE_NAME"):
        Settings(_env_file=None, queue_name="two names")
    with pytest.raises(ValidationError, match="QUEUE_NAME"):
        Settings(_env_file=None, queue_name="a/b")
    assert Settings(_env_file=None, queue_name=" finance-context ").queue_name == (
        "finance-context"
    )


def test_blank_queue_numbers_are_rejected() -> None:
    with pytest.raises(ValidationError, match="REDIS_CONNECT_TIMEOUT_SEC"):
        Settings(_env_file=None, redis_connect_timeout_sec="")
    with pytest.raises(ValidationError, match="JOB_QUEUE_MAX_PER_SESSION"):
        Settings(_env_file=None, job_queue_max_per_session=0)
    with pytest.raises(ValidationError, match="QUEUE_JOB_TTL_SEC"):
        Settings(_env_file=None, queue_job_ttl_sec=-1)
    with pytest.raises(ValidationError, match="JOB_RETRY_ON_LOST"):
        Settings(_env_file=None, job_retry_on_lost=-1)
