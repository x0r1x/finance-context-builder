from __future__ import annotations

import re
from pathlib import Path

import pytest

from finance_context.settings import Settings
from finance_context.settings_env import ENV_SPECS

_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"
_KEY = re.compile(r"^(?:#\s*)?([A-Z][A-Z0-9_]*)=")


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for spec in ENV_SPECS:
        monkeypatch.delenv(spec.env_name, raising=False)


def _example_keys() -> set[str]:
    keys: set[str] = set()
    for line in _EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = _KEY.match(line.strip())
        if match:
            keys.add(match.group(1))
    return keys


def test_env_names_match_settings_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    Settings(_env_file=None)
    assert [spec.field_name for spec in ENV_SPECS] == list(Settings.model_fields)
    assert [spec.env_name for spec in ENV_SPECS] == [
        name.upper() for name in Settings.model_fields
    ]


def test_env_example_lists_every_settings_name() -> None:
    assert _example_keys() == {spec.env_name for spec in ENV_SPECS}


def test_blank_llm_url_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("LLM_BASE_URL", "  ")
    settings = Settings(_env_file=None)
    assert settings.llm_base_url is None
    assert settings.llm_configured() is False


def test_blank_embedding_model_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "  ")
    settings = Settings(_env_file=None)
    assert settings.embedding_model is None
    assert settings.embed_configured() is False


def test_unset_job_timeout_and_session_use_field_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    settings = Settings(_env_file=None)
    assert settings.job_timeout_sec == 3600
    assert settings.session_id == "local"
