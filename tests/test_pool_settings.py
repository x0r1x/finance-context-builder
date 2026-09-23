from __future__ import annotations

from finance_context.settings import Settings


def test_pool_settings_default_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("EMBEDDING_BATCH_SIZE", raising=False)
    monkeypatch.delenv("EMBEDDING_CONCURRENCY", raising=False)
    monkeypatch.delenv("LLM_CONCURRENCY", raising=False)
    monkeypatch.delenv("MAX_CONCURRENT_JOBS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.embedding_batch_size == 32
    assert settings.embedding_concurrency == 1
    assert settings.llm_concurrency == 1
    assert settings.max_concurrent_jobs == 2


def test_blank_and_non_positive_pool_settings_use_defaults(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "")
    monkeypatch.setenv("EMBEDDING_CONCURRENCY", "-2")
    monkeypatch.setenv("LLM_CONCURRENCY", "0")
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "0")
    settings = Settings(_env_file=None)
    assert settings.embedding_batch_size == 32
    assert settings.embedding_concurrency == 1
    assert settings.llm_concurrency == 1
    assert settings.max_concurrent_jobs == 2
