from __future__ import annotations

import logging
import threading
import time

import pytest

from finance_context.adapters.openai_embed import OpenAIEmbed
from finance_context.errors import PortError
from finance_context.observability import JsonFormatter


class _Item:
    def __init__(self, index: int, embedding: list[float]) -> None:
        self.index = index
        self.embedding = embedding


class _Response:
    def __init__(self, data: list[_Item]) -> None:
        self.data = data


def _client(*, batch_size: int = 2, concurrency: int = 4) -> OpenAIEmbed:
    embed = OpenAIEmbed(
        base_url="http://127.0.0.1:9/v1",
        api_key=None,
        model="test-embed",
        batch_size=batch_size,
        concurrency=concurrency,
    )
    return embed


def test_batches_keep_input_order_when_response_indexes_are_reversed() -> None:
    embed = _client(batch_size=2, concurrency=1)
    seen: list[list[str]] = []

    def create(*, model: str, input: list[str]) -> _Response:
        seen.append(list(input))
        items = [_Item(i, [float(i)]) for i in range(len(input))]
        items.reverse()
        return _Response(items)

    embed._client.embeddings.create = create  # type: ignore[method-assign]
    vectors = embed.embed(["a", "b", "c", "d", "e"])
    assert seen == [["a", "b"], ["c", "d"], ["e"]]
    assert vectors == [[0.0], [1.0], [0.0], [1.0], [0.0]]


def test_batches_run_concurrently_up_to_the_cap() -> None:
    embed = _client(batch_size=1, concurrency=3)
    lock = threading.Lock()
    inflight = 0
    peak = 0

    def create(*, model: str, input: list[str]) -> _Response:
        nonlocal inflight, peak
        with lock:
            inflight += 1
            peak = max(peak, inflight)
        time.sleep(0.05)
        with lock:
            inflight -= 1
        return _Response([_Item(0, [1.0])])

    embed._client.embeddings.create = create  # type: ignore[method-assign]
    assert len(embed.embed(["a", "b", "c", "d", "e", "f"])) == 6
    assert peak == 3


def test_failed_batch_fails_the_whole_call() -> None:
    embed = _client(batch_size=1, concurrency=2)

    def create(*, model: str, input: list[str]) -> _Response:
        if input == ["bad"]:
            raise RuntimeError("down")
        return _Response([_Item(0, [1.0])])

    embed._client.embeddings.create = create  # type: ignore[method-assign]
    with pytest.raises(PortError):
        embed.embed(["ok", "bad"])


def test_debug_logs_embed_bodies_and_info_does_not(caplog: pytest.LogCaptureFixture) -> None:
    embed = _client(batch_size=8, concurrency=1)

    def create(*, model: str, input: list[str]) -> _Response:
        return _Response([_Item(0, [0.5])])

    embed._client.embeddings.create = create  # type: ignore[method-assign]
    caplog.set_level(logging.INFO, logger="finance_context.adapters.openai_embed")
    embed.embed(["secret-label"])
    assert not any(getattr(record, "request", None) for record in caplog.records)
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="finance_context.adapters.openai_embed")
    embed.embed(["secret-label"])
    bodies = [record for record in caplog.records if getattr(record, "event", None) == "port_io"]
    assert bodies
    assert bodies[0].request == ["secret-label"]
    assert bodies[0].response == [[0.5]]
    formatted = JsonFormatter().format(bodies[0])
    assert "secret-label" in formatted
    assert "0.5" in formatted
