from __future__ import annotations

import fcntl
import hashlib
import io
import json
import logging
import threading
from pathlib import Path

import numpy as np

from finance_context.mapping.knn import concept_vectors
from finance_context.mapping.models import Concept
from finance_context.observability import log_event
from finance_context.ports.protocols import EmbedPort
from finance_context.store.fs import atomic_write_bytes

_LOGGER = logging.getLogger(__name__)


def cache_key(taxonomy: list[Concept], *, model: str, dim: int) -> str:
    payload = json.dumps(
        [{"id": c.id, "labels": list(c.labels), "definition": c.definition} for c in taxonomy],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"{digest}:{model}:{dim}"


class TaxonomyPrefetch:
    """Embed taxonomy labels while earlier pipeline stages run."""

    def __init__(
        self,
        embed: EmbedPort,
        taxonomy: list[Concept],
        *,
        cache_path: Path | None,
        model: str,
    ) -> None:
        self._index: dict[str, list[float]] | None = None
        self._error: Exception | None = None
        self._thread = threading.Thread(
            target=self._load,
            args=(embed, taxonomy, cache_path, model),
            name="taxonomy-embed",
        )
        self._thread.start()

    def _load(
        self,
        embed: EmbedPort,
        taxonomy: list[Concept],
        cache_path: Path | None,
        model: str,
    ) -> None:
        try:
            self._index = load_concept_vectors(
                embed,
                taxonomy,
                cache_path=cache_path,
                model=model,
            )
        except Exception as exc:
            self._error = exc

    def result(self) -> dict[str, list[float]]:
        self._thread.join()
        if self._error is not None:
            raise self._error
        return self._index or {}

    def join(self) -> None:
        self._thread.join()


def load_concept_vectors(
    embed: EmbedPort,
    taxonomy: list[Concept],
    *,
    cache_path: Path | None = None,
    model: str = "",
) -> dict[str, list[float]]:
    if cache_path is None:
        return concept_vectors(embed, taxonomy)
    cached = _read_if_valid(cache_path, taxonomy, model)
    if cached is not None:
        _log_cache_hit(cached)
        return cached
    index = concept_vectors(embed, taxonomy)
    if not index:
        return index
    lock_path = cache_path.with_name(cache_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        cached = _read_if_valid(cache_path, taxonomy, model)
        if cached is not None:
            _log_cache_hit(cached)
            return cached
        dim = len(next(iter(index.values())))
        _write_npz(cache_path, cache_key(taxonomy, model=model, dim=dim), index)
    return index


def _log_cache_hit(index: dict[str, list[float]]) -> None:
    log_event(
        _LOGGER,
        logging.INFO,
        "embed_cache",
        "taxonomy embeddings cached",
        port="embed",
        reason="cache",
        count=len(index),
    )


def _read_if_valid(
    path: Path, taxonomy: list[Concept], model: str
) -> dict[str, list[float]] | None:
    if not path.exists():
        return None
    try:
        data = np.load(path, allow_pickle=False)
        raw_key = data["key"]
        key = str(raw_key.item()) if getattr(raw_key, "shape", None) == () else str(raw_key)
        ids = [str(item) for item in data["ids"].tolist()]
        vectors = np.asarray(data["vectors"], dtype=float)
    except (OSError, KeyError, ValueError):
        return None
    if vectors.ndim != 2 or len(ids) != vectors.shape[0]:
        return None
    expected = cache_key(taxonomy, model=model, dim=int(vectors.shape[1]))
    if key != expected:
        return None
    return {cid: vectors[i].tolist() for i, cid in enumerate(ids)}


def _write_npz(path: Path, key: str, index: dict[str, list[float]]) -> None:
    ids = list(index.keys())
    vectors = np.asarray([index[cid] for cid in ids], dtype=float)
    buf = io.BytesIO()
    np.savez(buf, key=np.asarray(key), ids=np.asarray(ids), vectors=vectors)
    atomic_write_bytes(path, buf.getvalue())
