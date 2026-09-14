from pathlib import Path

import pytest


@pytest.fixture
def dest(tmp_path: Path) -> Path:
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    return job_dir
