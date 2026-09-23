from __future__ import annotations

import fcntl
import threading
from pathlib import Path

from tests.helpers.ports import FakeEmbed

from finance_context.mapping.models import Concept
from finance_context.mapping.vectors import load_concept_vectors

TAXONOMY = [Concept(id="pnl.revenue", labels=["Revenue"])]


def test_second_load_reuses_npz(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy_embeddings.npz"
    first = FakeEmbed({"revenue": [1.0, 0.0, 0.0]})
    index = load_concept_vectors(first, TAXONOMY, cache_path=path, model="m")
    assert first.calls == 1
    assert index["pnl.revenue"] == [1.0, 0.0, 0.0]
    assert path.is_file()

    second = FakeEmbed({"revenue": [0.0, 1.0, 0.0]})
    reused = load_concept_vectors(second, TAXONOMY, cache_path=path, model="m")
    assert second.calls == 0
    assert reused["pnl.revenue"] == [1.0, 0.0, 0.0]


def test_npz_invalidated_when_model_changes(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy_embeddings.npz"
    first = FakeEmbed({"revenue": [1.0, 0.0, 0.0]})
    load_concept_vectors(first, TAXONOMY, cache_path=path, model="m1")
    second = FakeEmbed({"revenue": [0.0, 1.0, 0.0]})
    index = load_concept_vectors(second, TAXONOMY, cache_path=path, model="m2")
    assert second.calls == 1
    assert index["pnl.revenue"] == [0.0, 1.0, 0.0]


def test_npz_invalidated_when_taxonomy_changes(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy_embeddings.npz"
    first = FakeEmbed({"revenue": [1.0, 0.0, 0.0], "gmv": [0.0, 1.0, 0.0]})
    load_concept_vectors(first, TAXONOMY, cache_path=path, model="m")
    other = [Concept(id="pnl.gmv", labels=["GMV"])]
    second = FakeEmbed({"revenue": [1.0, 0.0, 0.0], "gmv": [0.0, 1.0, 0.0]})
    index = load_concept_vectors(second, other, cache_path=path, model="m")
    assert second.calls == 1
    assert "pnl.gmv" in index
    assert "pnl.revenue" not in index


def test_embed_does_not_hold_the_cache_lock(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy_embeddings.npz"
    started = threading.Event()
    release = threading.Event()

    class SlowEmbed(FakeEmbed):
        def embed(self, texts: list[str]) -> list[list[float]]:
            started.set()
            assert release.wait(5)
            return super().embed(texts)

    errors: list[BaseException] = []

    def run() -> None:
        try:
            load_concept_vectors(
                SlowEmbed({"revenue": [1.0, 0.0, 0.0]}),
                TAXONOMY,
                cache_path=path,
                model="m",
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(5)
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert errors == []


def test_no_cache_path_always_embeds() -> None:
    embed = FakeEmbed({"revenue": [1.0, 0.0, 0.0]})
    load_concept_vectors(embed, TAXONOMY)
    load_concept_vectors(embed, TAXONOMY)
    assert embed.calls == 2
