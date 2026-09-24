from __future__ import annotations

import hashlib
import json
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1]
_STAMP: str | None = None
_STAMP_STAGES: dict[str, str] | None = None
_CACHE_KEY: tuple[tuple[str, int], ...] | None = None
_CACHE_STAGES: dict[str, str] | None = None
_CACHE_FINGERPRINT: str | None = None

STAGE_ORDER = ("compile", "layout", "mapping", "graph", "publish")
_STAGE_DIRS = {
    "compile": {"excel", "formulas"},
    "layout": {"layout"},
    "mapping": {"mapping", "ontology"},
    "graph": {"graph"},
    "publish": {"context", "render"},
}


def _sources() -> list[Path]:
    ontology = _PACKAGE / "ontology"
    found: list[Path] = []
    for path in _PACKAGE.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix == ".py":
            found.append(path)
        elif path.suffix in {".yaml", ".yml"} and ontology in path.parents:
            found.append(path)
    found.sort(key=lambda item: item.relative_to(_PACKAGE).as_posix())
    return found


def _stage_of(rel: str) -> str | None:
    if rel == "models/context.py":
        return "publish"
    top = rel.split("/", 1)[0]
    for stage, dirs in _STAGE_DIRS.items():
        if top in dirs:
            return stage
    return None


def _hash_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(_PACKAGE).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _hash_stages(stages: dict[str, str]) -> str:
    payload = json.dumps([stages[name] for name in STAGE_ORDER], separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _compute_stages() -> dict[str, str]:
    buckets: dict[str, list[Path]] = {name: [] for name in STAGE_ORDER}
    for path in _sources():
        stage = _stage_of(path.relative_to(_PACKAGE).as_posix())
        if stage is not None:
            buckets[stage].append(path)
    return {name: _hash_files(buckets[name]) for name in STAGE_ORDER}


def _live() -> tuple[dict[str, str], str]:
    """Stage hashes and the combined fingerprint. Cached until a source mtime changes."""
    global _CACHE_KEY, _CACHE_STAGES, _CACHE_FINGERPRINT
    files = _sources()
    key = tuple(
        (path.relative_to(_PACKAGE).as_posix(), path.stat().st_mtime_ns) for path in files
    )
    if key == _CACHE_KEY and _CACHE_STAGES is not None and _CACHE_FINGERPRINT is not None:
        return _CACHE_STAGES, _CACHE_FINGERPRINT
    stages = _compute_stages()
    fingerprint = _hash_stages(stages)
    _CACHE_KEY = key
    _CACHE_STAGES = stages
    _CACHE_FINGERPRINT = fingerprint
    return stages, fingerprint


def stage_fingerprints() -> dict[str, str]:
    """Hash of each publisher stage from the code currently on disk."""
    return dict(_live()[0])


def publisher_fingerprint() -> str:
    """Hash of the stage fingerprints on disk at this call."""
    return _live()[1]


def publisher_stamp() -> str:
    """Combined hash this process started with. A later edit on disk does not change it."""
    global _STAMP, _STAMP_STAGES
    if _STAMP is None:
        stages, fingerprint = _live()
        _STAMP_STAGES = dict(stages)
        _STAMP = fingerprint
    return _STAMP


def stamped_stages() -> dict[str, str]:
    """Stage hashes captured with ``publisher_stamp`` for this process."""
    publisher_stamp()
    assert _STAMP_STAGES is not None
    return dict(_STAMP_STAGES)


def publisher_matches(meta: dict | None) -> bool:
    if not isinstance(meta, dict):
        return False
    return meta.get("publisher") == publisher_fingerprint()


def publisher_changed(meta: dict | None) -> bool:
    """A stored stamp differs from the code on disk. A missing stamp does not."""
    if not isinstance(meta, dict):
        return False
    stored = meta.get("publisher")
    if not isinstance(stored, str) or not stored:
        return False
    return stored != publisher_fingerprint()


def stale_from_meta(meta: dict | None) -> str | None:
    """First stage whose stored hash differs.

    A missing stage map, or a stage map whose combined stamp was replaced, keeps the
    legacy layout tail. ``None`` means the stored stamp still matches this code.
    """
    if not isinstance(meta, dict) or meta.get("publisher") != publisher_fingerprint():
        stored = meta.get("stages") if isinstance(meta, dict) else None
        if not isinstance(stored, dict) or not stored:
            return "layout"
        current = stage_fingerprints()
        for name in STAGE_ORDER:
            if stored.get(name) != current[name]:
                return name
        return "layout"
    return None
