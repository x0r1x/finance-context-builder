from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

_PACKAGE = Path(__file__).resolve().parents[1]
_STAMP: str | None = None
_STAMP_STAGES: dict[str, str] | None = None
_CACHE_KEY: tuple[tuple[str, int], ...] | None = None
_CACHE_STAGES: dict[str, str] | None = None
_CACHE_FINGERPRINT: str | None = None

STAGE_ORDER = ("compile", "layout", "graph", "publish")
# Package data that is part of a stage. Other JSON (notes, fixtures) is not.
_DATA_FILES = {"layout/lexicon.json"}
_STAGE_DIRS = {
    "compile": {"excel", "formulas"},
    "layout": {"layout"},
    "graph": {"graph"},
    "publish": {"context", "render"},
}


def _sources() -> list[Path]:
    found: list[Path] = []
    for path in _PACKAGE.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(_PACKAGE).as_posix()
        if path.suffix == ".py" or rel in _DATA_FILES:
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
    _include_layout_imports(buckets)
    stages = {name: _hash_files(buckets[name]) for name in STAGE_ORDER}
    overlay = _overlay_bytes()
    if overlay is not None:
        digest = hashlib.sha256()
        digest.update(stages["layout"].encode())
        digest.update(b"\0lexicon-overlay\0")
        digest.update(overlay)
        stages["layout"] = digest.hexdigest()
    return stages


def _include_layout_imports(buckets: dict[str, list[Path]]) -> None:
    """Hash every package module the layout stage imports into that stage.

    ``layout/units.py`` imports ``context/measure.py``. A directory bucket would
    leave that edit in the publish stage, so ``layout.json`` would stay fresh.
    """
    seen = {path.resolve() for path in buckets["layout"]}
    pending = [path for path in buckets["layout"] if path.suffix == ".py"]
    extra: list[Path] = []
    while pending:
        path = pending.pop()
        for imported in _imported_package_files(path):
            resolved = imported.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            extra.append(imported)
            if imported.suffix == ".py":
                pending.append(imported)
    if not extra:
        return
    buckets["layout"].extend(extra)
    buckets["layout"].sort(key=lambda item: item.relative_to(_PACKAGE).as_posix())


def _imported_package_files(path: Path) -> list[Path]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    found: list[Path] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.extend(_import_from_modules(path, node))
        for module in modules:
            candidate = _module_file(module)
            if candidate is not None:
                found.append(candidate)
    return found


def _import_from_modules(path: Path, node: ast.ImportFrom) -> list[str]:
    if node.level == 0:
        return [node.module] if node.module else []
    try:
        parts = list(path.relative_to(_PACKAGE).parts[:-1])
    except ValueError:
        return []
    drop = node.level - 1
    if drop > len(parts):
        return []
    if drop:
        parts = parts[:-drop]
    if node.module:
        parts.extend(node.module.split("."))
    if not parts:
        return ["finance_context"]
    return ["finance_context." + ".".join(parts)]


def _module_file(module: str) -> Path | None:
    prefix = "finance_context"
    if module != prefix and not module.startswith(prefix + "."):
        return None
    rel = module[len(prefix) :].lstrip(".").replace(".", "/")
    file = _PACKAGE / "__init__.py" if not rel else _PACKAGE / f"{rel}.py"
    if file.is_file():
        return file
    if not rel:
        return None
    init = _PACKAGE / rel / "__init__.py"
    return init if init.is_file() else None


def _overlay_bytes() -> bytes | None:
    from finance_context.layout.lexicon import overlay_path

    path = overlay_path()
    if path is None:
        return None
    return path.read_bytes()


def _live() -> tuple[dict[str, str], str]:
    """Stage hashes and the combined fingerprint. Cached until a source mtime changes."""
    global _CACHE_KEY, _CACHE_STAGES, _CACHE_FINGERPRINT
    files = _sources()
    key_parts = [(path.relative_to(_PACKAGE).as_posix(), path.stat().st_mtime_ns) for path in files]
    from finance_context.layout.lexicon import overlay_path

    overlay = overlay_path()
    if overlay is not None:
        key_parts.append((f"overlay:{overlay}", overlay.stat().st_mtime_ns))
    key = tuple(key_parts)
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
