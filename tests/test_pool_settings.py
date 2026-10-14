from __future__ import annotations

from finance_context.settings import Settings


def test_pool_settings_default_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("MAX_CONCURRENT_JOBS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.max_concurrent_jobs == 2


def test_blank_and_non_positive_pool_settings_use_defaults(monkeypatch) -> None:
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "0")
    settings = Settings(_env_file=None)
    assert settings.max_concurrent_jobs == 2

    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "")
    settings = Settings(_env_file=None)
    assert settings.max_concurrent_jobs == 2
