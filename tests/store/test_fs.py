from __future__ import annotations

import json
import os
from pathlib import Path

from finance_context.store.fs import update_json, write_json


def test_update_json_keeps_a_write_that_lands_when_mutate_is_a_noop(tmp_path: Path) -> None:
    path = tmp_path / "meta.json"
    path.write_text(json.dumps({"status": "succeeded", "publisher": "current"}), encoding="utf-8")

    def mutate(current: dict) -> dict:
        path.write_text(
            json.dumps({"status": "succeeded", "publisher": "stale"}),
            encoding="utf-8",
        )
        return current

    update_json(path, mutate)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["publisher"] == "stale"


def test_update_json_writes_a_new_object(tmp_path: Path) -> None:
    path = tmp_path / "meta.json"
    path.write_text(json.dumps({"status": "running"}), encoding="utf-8")

    def mutate(current: dict) -> dict:
        return {**current, "status": "failed", "error": "TimeoutError"}

    update_json(path, mutate)
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["status"] == "failed"
    assert stored["error"] == "TimeoutError"


def test_update_json_replaces_a_non_object_document(tmp_path: Path) -> None:
    path = tmp_path / "meta.json"
    path.write_text("[]\n", encoding="utf-8")

    def mutate(current: dict) -> dict:
        return current

    update_json(path, mutate)
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_shared_publish_atomic(tmp_path: Path, monkeypatch) -> None:
    """A reader of the published path never observes a partial document."""
    path = tmp_path / "layout.json"
    write_json(path, {"version": 1, "ok": True})
    seen: list[str] = []
    real_replace = os.replace

    def spy(src: str, dst: str) -> None:
        published = path.read_text(encoding="utf-8")
        seen.append(published)
        assert json.loads(published) == {"version": 1, "ok": True}
        assert Path(src).read_bytes() != path.read_bytes()
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    write_json(path, {"version": 2, "ok": True, "pad": "x" * 128})
    assert len(seen) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 2


def test_update_json_creates_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "meta.json"

    def mutate(current: dict) -> dict:
        return current

    update_json(path, mutate)
    assert json.loads(path.read_text(encoding="utf-8")) == {}
