from __future__ import annotations

from typing import TYPE_CHECKING

from finance_context.errors import PortError
from finance_context.ports.protocols import ChatPort, EmbedPort

if TYPE_CHECKING:
    from finance_context.settings import Settings


def build_chat(settings: Settings) -> ChatPort | None:
    if not settings.llm_configured():
        return None
    from finance_context.adapters.openai_chat import OpenAIChat

    try:
        return OpenAIChat(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            ca_file=settings.llm_tls_ca_file,
            chat_path=settings.llm_chat_path,
            concurrency=settings.llm_concurrency,
        )
    except PortError:
        return None


def build_embed(settings: Settings) -> EmbedPort | None:
    if not settings.embed_configured():
        return None
    from finance_context.adapters.openai_embed import OpenAIEmbed

    try:
        return OpenAIEmbed(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            model=settings.embedding_model,
            ca_file=settings.embedding_tls_ca_file,
            embed_path=settings.embedding_path,
            batch_size=settings.embedding_batch_size,
            concurrency=settings.embedding_concurrency,
        )
    except PortError:
        return None
