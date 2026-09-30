"""Session job list. Reads meta.json only."""

from __future__ import annotations

import json
from pathlib import Path


def list_session_jobs(
    jobs_root: Path,
    *,
    status: str | None = None,
    q: str | None = None,
) -> list[dict[str, str | None]]:
    """Jobs of one session, sorted by job_id. Unreadable meta is skipped."""
    if not jobs_root.is_dir():
        return []
    needle = (q or "").strip().casefold()
    items: list[dict[str, str | None]] = []
    for path in jobs_root.iterdir():
        if not path.is_dir():
            continue
        meta = _read_meta(path / "meta.json")
        if meta is None:
            continue
        if status is not None and meta.get("status") != status:
            continue
        filename = meta.get("source_filename")
        if needle:
            if not isinstance(filename, str) or needle not in filename.casefold():
                continue
        items.append(
            {
                "job_id": str(meta.get("job_id") or path.name),
                "status": _text(meta.get("status")),
                "stage": _text(meta.get("stage")),
                "source_filename": filename if isinstance(filename, str) else None,
                "content_sha256": _text(meta.get("content_sha256")),
                "schema_version": _text(meta.get("schema_version")),
            }
        )
    items.sort(key=lambda item: item["job_id"] or "")
    return items


def _read_meta(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    return loaded


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None
