"""Local data split: shared book cache and one directory per session."""

from __future__ import annotations

from pathlib import Path

LOCAL_SESSION = "local"


def session_job_dir(root: Path, job_id: str, session_id: str = LOCAL_SESSION) -> Path:
    return root / "sessions" / (session_id or LOCAL_SESSION) / "jobs" / job_id


def shared_book_dir(root: Path, job_id: str) -> Path:
    return root / "shared" / "books" / job_id


def glossary_file(root: Path, session_id: str = LOCAL_SESSION) -> Path:
    return root / "sessions" / (session_id or LOCAL_SESSION) / "glossary.json"


def embedding_cache_file(root: Path, *, model: str, taxonomy_digest: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (model or "default"))
    safe = safe[:80] or "default"
    return root / "shared" / "embeddings" / f"{safe}-{taxonomy_digest[:32]}.npz"
