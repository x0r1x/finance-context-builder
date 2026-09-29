from __future__ import annotations

import logging

import numpy as np

from finance_context.errors import PortError
from finance_context.mapping.models import Concept
from finance_context.observability import log_event
from finance_context.ports.protocols import EmbedPort

_LOGGER = logging.getLogger(__name__)

COSINE_MIN = 0.85
COSINE_GAP = 0.08
TOP_K = 5


def cosine(a: list[float], b: list[float]) -> float:
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _concept_phrases(concept: Concept, by_id: dict[str, Concept]) -> list[str]:
    phrases = [label for label in concept.labels if label]
    phrases.extend(alias for alias in concept.aliases if alias)
    if concept.definition:
        phrases.append(concept.definition)
    parent = by_id.get(concept.broader or "")
    if parent is not None and parent.labels:
        phrases.append(parent.labels[0])
    return phrases


def concept_vectors(embed: EmbedPort, taxonomy: list[Concept]) -> dict[str, list[float]]:
    by_id = {concept.id: concept for concept in taxonomy}
    labels: list[str] = []
    owners: list[str] = []
    for concept in taxonomy:
        for phrase in _concept_phrases(concept, by_id):
            labels.append(phrase)
            owners.append(concept.id)
    if not labels:
        return {}
    try:
        vectors = embed.embed(labels)
    except PortError:
        log_event(
            _LOGGER,
            logging.WARNING,
            "port_fallback",
            "embed fallback",
            port="embed",
            reason="port_error",
        )
        return {}
    buckets: dict[str, list[list[float]]] = {}
    for concept_id, vec in zip(owners, vectors, strict=True):
        buckets.setdefault(concept_id, []).append(vec)
    return {cid: np.mean(np.asarray(vecs), axis=0).tolist() for cid, vecs in buckets.items()}


def rank_concepts(
    query: list[float],
    index: dict[str, list[float]],
) -> list[tuple[str, float]]:
    scored = [(cid, cosine(query, vec)) for cid, vec in index.items()]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored
