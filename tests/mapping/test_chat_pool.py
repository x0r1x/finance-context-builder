from __future__ import annotations

import threading
import time

from pydantic import BaseModel
from tests.helpers.ports import CapBudget, FakeEmbed
from tests.mapping.test_cascade import TAXONOMY, VECS, _layout

from finance_context.errors import PortError
from finance_context.layout.models import LayoutRow
from finance_context.mapping.cascade import map_layout
from finance_context.mapping.vectors import TaxonomyPrefetch


class _SlowChat:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()
        self._inflight = 0
        self.peak = 0

    def complete_json(self, schema: type[BaseModel], messages: list) -> BaseModel:
        label = str(messages[-1]["content"])
        with self._lock:
            self.calls += 1
            self._inflight += 1
            self.peak = max(self.peak, self._inflight)
        time.sleep(0.05 if "Alpha" in label else 0.01)
        with self._lock:
            self._inflight -= 1
        concept_id = "pnl.revenue" if "Alpha" in label else "pnl.gmv"
        return schema.model_validate({"concept_id": concept_id})


class _BoomChat:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = threading.Lock()

    def complete_json(self, schema: type[BaseModel], messages: list) -> BaseModel:
        with self._lock:
            self.calls += 1
            n = self.calls
        if n == 1:
            raise PortError("chat failed", port="chat")
        time.sleep(0.02)
        return schema.model_validate({"concept_id": "pnl.revenue"})


class _ReadyIndex:
    def __init__(self, index: dict[str, list[float]]) -> None:
        self.index = index
        self.reads = 0

    def result(self) -> dict[str, list[float]]:
        self.reads += 1
        return self.index


def test_chat_rows_run_together_and_keep_their_concepts() -> None:
    chat = _SlowChat()
    layout = _layout(
        LayoutRow(row=2, label="Alpha mystery"),
        LayoutRow(row=3, label="Beta mystery"),
    )
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=None,
        chat=chat,
        llm_concurrency=2,
    )
    by_label = {row.label: row.concept_id for row in doc.rows}
    assert by_label["Alpha mystery"] == "pnl.revenue"
    assert by_label["Beta mystery"] == "pnl.gmv"
    assert chat.calls == 2
    assert chat.peak == 2


def test_chat_budget_stops_extra_rows_even_with_a_wide_pool() -> None:
    chat = _SlowChat()
    layout = _layout(
        LayoutRow(row=2, label="Alpha mystery"),
        LayoutRow(row=3, label="Beta mystery"),
    )
    doc = map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=None,
        chat=chat,
        slots=CapBudget({"llm": 1}),
        llm_concurrency=4,
    )
    assert chat.calls == 1
    mapped = [row for row in doc.rows if row.concept_id]
    assert len(mapped) == 1


def test_chat_port_error_does_not_send_the_remaining_row() -> None:
    chat = _BoomChat()
    layout = _layout(
        LayoutRow(row=2, label="Alpha mystery"),
        LayoutRow(row=3, label="Beta mystery"),
    )
    map_layout(
        layout,
        taxonomy=TAXONOMY,
        glossary={},
        embed=None,
        chat=chat,
        llm_concurrency=1,
    )
    assert chat.calls == 1


def test_ready_concept_index_skips_taxonomy_embed() -> None:
    embed = FakeEmbed(VECS)
    ready = _ReadyIndex({"pnl.revenue": [1.0, 0.0, 0.0]})
    map_layout(
        _layout(LayoutRow(row=2, label="Mystery line")),
        taxonomy=TAXONOMY,
        glossary={},
        embed=embed,
        chat=None,
        concept_index=ready,
    )
    assert ready.reads == 1
    assert embed.calls == 1


def test_taxonomy_prefetch_embeds_once(tmp_path) -> None:
    embed = FakeEmbed(VECS)
    prefetch = TaxonomyPrefetch(
        embed,
        TAXONOMY,
        cache_path=tmp_path / "taxonomy_embeddings.npz",
        model="m",
    )
    index = prefetch.result()
    prefetch.join()
    assert embed.calls == 1
    assert "pnl.revenue" in index
