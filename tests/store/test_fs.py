from __future__ import annotations

import json
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import pytest

from finance_context.store.fs import (
    atomic_write_bytes,
    stream_json,
    update_json,
    write_json,
    write_parquet,
)


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


def _write_bytes(spec: tuple[str, bytes]) -> None:
    path, payload = spec
    atomic_write_bytes(Path(path), payload)


def test_concurrent_writers_leave_identical_bytes(tmp_path: Path) -> None:
    """Two writers of the same bytes must both succeed. The last replace wins."""
    target = tmp_path / "source.xlsx"
    payload = b"PK\x03\x04" + b"same-workbook" * 4096
    specs = [(str(target), payload)] * 8
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_write_bytes, specs))
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        list(pool.map(_write_bytes, specs[:4]))
    assert target.read_bytes() == payload
    stray = [path.name for path in tmp_path.iterdir() if path != target]
    assert stray == []


def test_atomic_write_removes_the_temp_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_src: str, _dst: str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_bytes(tmp_path / "source.xlsx", b"abc")
    assert list(tmp_path.iterdir()) == []


def test_stream_json_and_parquet_use_unique_temps(tmp_path: Path) -> None:
    document = tmp_path / "meta.json"
    table = tmp_path / "cells.parquet"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: stream_json(document, {"n": n}), range(4)))
        list(
            pool.map(
                lambda n: write_parquet(
                    table,
                    (("n", "INTEGER"),),
                    [(n,)],
                ),
                range(4),
            )
        )
    assert "n" in json.loads(document.read_text(encoding="utf-8"))
    assert set(tmp_path.iterdir()) == {document, table}


def test_update_json_creates_a_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "meta.json"

    def mutate(current: dict) -> dict:
        return current

    update_json(path, mutate)
    assert json.loads(path.read_text(encoding="utf-8")) == {}
