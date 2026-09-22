from __future__ import annotations

import logging

import pytest
from pydantic import BaseModel

from finance_context.adapters.openai_chat import OpenAIChat


class _Pick(BaseModel):
    concept_id: str


def test_debug_logs_chat_messages_and_parsed_body(caplog: pytest.LogCaptureFixture) -> None:
    chat = OpenAIChat(
        base_url="http://127.0.0.1:9/v1",
        api_key="super-secret",
        model="test-chat",
        concurrency=2,
    )
    messages = [{"role": "user", "content": "label: Revenue"}]

    class _Message:
        parsed = _Pick(concept_id="pnl.revenue")
        content = None

    class _Choice:
        message = _Message()

    class _Response:
        choices = [_Choice()]

    def parse(*, model: str, messages: list, response_format: type, tool_choice: str):
        return _Response()

    chat._client.chat.completions.parse = parse  # type: ignore[method-assign]
    caplog.set_level(logging.INFO, logger="finance_context.adapters.openai_chat")
    assert chat.complete_json(_Pick, messages).concept_id == "pnl.revenue"
    assert not any(getattr(record, "request", None) for record in caplog.records)
    assert "super-secret" not in caplog.text
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="finance_context.adapters.openai_chat")
    chat.complete_json(_Pick, messages)
    bodies = [record for record in caplog.records if getattr(record, "event", None) == "port_io"]
    assert bodies[-1].request == messages
    assert bodies[-1].response == {"concept_id": "pnl.revenue"}
    assert "super-secret" not in caplog.text
