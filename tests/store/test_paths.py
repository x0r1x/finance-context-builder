from __future__ import annotations

from pathlib import Path

import pytest

from finance_context.store.paths import session_job_dir, shared_book_dir


def test_session_and_book_paths_stay_inside_data_dir(tmp_path: Path) -> None:
    job_id = "ab" * 32
    session = session_job_dir(tmp_path, job_id, "alpha")
    shared = shared_book_dir(tmp_path, job_id)
    root = tmp_path.resolve()
    assert session.resolve().is_relative_to(root)
    assert shared.resolve().is_relative_to(root)
    assert session == tmp_path / "sessions" / "alpha" / "jobs" / job_id
    assert shared == tmp_path / "shared" / "books" / job_id


@pytest.mark.parametrize(
    ("session_id", "job_id"),
    [
        ("../etc", "job"),
        ("..", "job"),
        (".", "job"),
        ("a/b", "job"),
        ("a\\b", "job"),
        ("alpha", "../job"),
        ("alpha", ".."),
        ("alpha", "a/b"),
        ("alpha", "a\\b"),
        ("alpha", ""),
    ],
)
def test_path_builder_rejects_escape(tmp_path: Path, session_id: str, job_id: str) -> None:
    with pytest.raises(ValueError, match="DATA_DIR"):
        session_job_dir(tmp_path, job_id, session_id)


@pytest.mark.parametrize("job_id", ["..", "../book", "a/b", "a\\b", "", "."])
def test_shared_book_dir_rejects_escape(tmp_path: Path, job_id: str) -> None:
    with pytest.raises(ValueError, match="DATA_DIR"):
        shared_book_dir(tmp_path, job_id)
