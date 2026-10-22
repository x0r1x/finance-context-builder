from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

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


def test_unset_job_timeout_and_session_use_field_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear(monkeypatch)
    settings = Settings(_env_file=None)
    assert settings.job_timeout_sec == 3600
    assert settings.session_id == "local"
    assert settings.session_id_pattern == r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    assert settings.session_id_max_length == 64
    assert settings.data_dir_allow_network_fs is False


@pytest.mark.parametrize(
    "session_id",
    ["../etc", "..", ".", "a/b", "a\\b", "", " ", "-lead", "_lead", "a" * 65],
)
def test_session_id_rejects_traversal_and_separators(session_id: str) -> None:
    with pytest.raises(ValidationError, match="SESSION_ID"):
        Settings(session_id=session_id, _env_file=None)


def test_session_id_accepts_a_single_directory_name() -> None:
    settings = Settings(session_id="Job_1-a", _env_file=None)
    assert settings.session_id == "Job_1-a"


def test_session_id_pattern_and_length_come_from_settings() -> None:
    Settings(
        session_id="ab",
        session_id_pattern=r"^[a-z]{2}$",
        session_id_max_length=4,
        _env_file=None,
    )
    with pytest.raises(ValidationError, match="SESSION_ID"):
        Settings(
            session_id="abc",
            session_id_pattern=r"^[a-z]{2}$",
            session_id_max_length=4,
            _env_file=None,
        )
    with pytest.raises(ValidationError, match="SESSION_ID"):
        Settings(session_id="abcd", session_id_max_length=3, _env_file=None)


def test_session_id_pattern_must_compile() -> None:
    with pytest.raises(ValidationError, match="SESSION_ID_PATTERN"):
        Settings(session_id="local", session_id_pattern="(", _env_file=None)
