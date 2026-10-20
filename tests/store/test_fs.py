from __future__ import annotations

import json
from pathlib import Path

from finance_context.store.fs import update_json


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


def test_update_json_creates_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "meta.json"

    def mutate(current: dict) -> dict:
        return current

    update_json(path, mutate)
    assert json.loads(path.read_text(encoding="utf-8")) == {}
