from pathlib import Path

import pytest
from tests.helpers.policy import isolated_settings as _isolated_settings

from finance_context.settings import Settings
from finance_context.settings_env import ENV_SPECS


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    return job_dir


@pytest.fixture
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    for spec in ENV_SPECS:
        monkeypatch.delenv(spec.env_name, raising=False)
    return _isolated_settings()
