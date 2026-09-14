from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from finance_context.adapters.routes import EMBED_SUFFIX, openai_path, wrap_sync_transport
from finance_context.adapters.tls import log_host
from finance_context.errors import PortError
from finance_context.observability import log_event

_LOGGER = logging.getLogger(__name__)


_PLACEHOLDER_KEY = "not-needed"


class OpenAIEmbed:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        ca_file: Path | None = None,
        embed_path: str = EMBED_SUFFIX,
    ) -> None:
        from openai import OpenAI

        try:
            dest = openai_path(embed_path, EMBED_SUFFIX)
            transport = wrap_sync_transport(
                None,
                base_url=base_url,
                ca_file=ca_file,
                sdk_suffix=EMBED_SUFFIX,
                dest_path=dest,
            )
        except PortError as exc:
            log_event(
                _LOGGER,
                logging.ERROR,
                "port_error",
                "embed tls" if "tls" in str(exc) else "embed path",
                port="embed",
                model=model,
                exc_type=type(exc).__name__,
            )
            raise PortError(str(exc), port="embed") from exc
        http = httpx.Client(transport=transport)
        self._client = OpenAI(
            base_url=base_url, api_key=api_key or _PLACEHOLDER_KEY, http_client=http
        )
        self.model = model
        log_event(
            _LOGGER,
            logging.INFO,
            "port_connect",
            "embed client",
            port="embed",
            model=model,
            host=log_host(base_url),
            path=dest,
            tls_ca=ca_file is not None,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        t0 = time.monotonic()
        try:
            response = self._client.embeddings.create(model=self.model, input=texts)
            vectors = [list(item.embedding) for item in response.data]
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            log_event(
                _LOGGER,
                logging.ERROR,
                "port_error",
                "embed failed",
                port="embed",
                model=self.model,
                http_status=status_code,
                latency_ms=int((time.monotonic() - t0) * 1000),
                exc_type=type(exc).__name__,
            )
            raise PortError("embed failed", port="embed", status_code=status_code) from exc
        log_event(
            _LOGGER,
            logging.INFO,
            "port_ok",
            "embed ok",
            port="embed",
            model=self.model,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return vectors
