from __future__ import annotations

import hashlib
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1]
_STAMP: str | None = None


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


def _digest() -> str:
    digest = hashlib.sha256()
    for path in _sources():
        digest.update(path.relative_to(_PACKAGE).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def publisher_fingerprint() -> str:
    """Hash of the publisher code on disk at this call."""
    return _digest()


def publisher_stamp() -> str:
    """Hash this process started with. A later edit on disk does not change it."""
    global _STAMP
    if _STAMP is None:
        _STAMP = _digest()
    return _STAMP


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
