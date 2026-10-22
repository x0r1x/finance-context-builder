"""Local data split: shared book cache and one directory per session."""

from __future__ import annotations

from pathlib import Path

LOCAL_SESSION = "local"


def session_job_dir(root: Path, job_id: str, session_id: str = LOCAL_SESSION) -> Path:
    session = _segment(session_id or LOCAL_SESSION, "SESSION_ID")
    job = _segment(job_id, "job_id")
    return _contained(root, "sessions", session, "jobs", job)


def session_jobs_root(root: Path, session_id: str = LOCAL_SESSION) -> Path:
    session = _segment(session_id or LOCAL_SESSION, "SESSION_ID")
    return _contained(root, "sessions", session, "jobs", reject_alias=True)


def shared_book_dir(root: Path, job_id: str) -> Path:
    job = _segment(job_id, "job_id")
    return _contained(root, "shared", "books", job)


def _segment(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError(f"{label} must stay inside DATA_DIR")
    if "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"{label} must stay inside DATA_DIR")
    return value


def _contained(root: Path, *parts: str, reject_alias: bool = False) -> Path:
    candidate = root.joinpath(*parts)
    try:
        root_resolved = root.resolve()
        resolved = candidate.resolve()
    except OSError as exc:
        raise ValueError(f"{candidate} must stay inside DATA_DIR") from exc
    expected = root_resolved.joinpath(*parts)
    escaped = not resolved.is_relative_to(root_resolved)
    aliased = reject_alias and resolved != expected
    if escaped or aliased:
        raise ValueError(f"{candidate} must stay inside DATA_DIR")
    return candidate
