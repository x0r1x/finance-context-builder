from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx
from pydantic import BaseModel

from finance_context.adapters.routes import CHAT_SUFFIX, openai_path, wrap_sync_transport
from finance_context.adapters.tls import log_host
from finance_context.errors import PortError
from finance_context.observability import debug_port_io, log_event

_LOGGER = logging.getLogger(__name__)


_PLACEHOLDER_KEY = "not-needed"
_DEFAULT_CONCURRENCY = 4


class OpenAIChat:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        ca_file: Path | None = None,
        chat_path: str = CHAT_SUFFIX,
        concurrency: int = _DEFAULT_CONCURRENCY,
    ) -> None:
        from openai import OpenAI

        try:
            dest = openai_path(chat_path, CHAT_SUFFIX)
            transport = wrap_sync_transport(
                None,
                base_url=base_url,
                ca_file=ca_file,
                sdk_suffix=CHAT_SUFFIX,
                dest_path=dest,
            )
        except PortError as exc:
            log_event(
                _LOGGER,
                logging.ERROR,
                "port_error",
                "chat tls" if "tls" in str(exc) else "chat path",
                port="chat",
                model=model,
                exc_type=type(exc).__name__,
            )
            raise PortError(str(exc), port="chat") from exc
        workers = concurrency if concurrency > 0 else _DEFAULT_CONCURRENCY
        http = httpx.Client(
            transport=transport,
            limits=httpx.Limits(max_connections=workers, max_keepalive_connections=workers),
        )
        self._client = OpenAI(
            base_url=base_url, api_key=api_key or _PLACEHOLDER_KEY, http_client=http
        )
        self.model = model
        log_event(
            _LOGGER,
            logging.INFO,
            "port_connect",
            "chat client",
            port="chat",
            model=model,
            host=log_host(base_url),
            path=dest,
            tls_ca=ca_file is not None,
        )

    def complete_json(self, schema: type[BaseModel], messages: list) -> BaseModel:
        t0 = time.monotonic()
        try:
            response = self._client.chat.completions.parse(
                model=self.model,
                messages=messages,
                response_format=schema,
                tool_choice="none",
            )
            message = response.choices[0].message
            parsed = getattr(message, "parsed", None)
            if isinstance(parsed, schema):
                result = parsed
            else:
                content = getattr(message, "content", None) or "{}"
                result = schema.model_validate_json(content)
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            debug_port_io(
                _LOGGER,
                "chat io",
                port="chat",
                model=self.model,
                request=messages,
            )
            log_event(
                _LOGGER,
                logging.ERROR,
                "port_error",
                "chat failed",
                port="chat",
                model=self.model,
                http_status=status_code,
                latency_ms=int((time.monotonic() - t0) * 1000),
                exc_type=type(exc).__name__,
            )
            raise PortError("chat failed", port="chat", status_code=status_code) from exc
        log_event(
            _LOGGER,
            logging.INFO,
            "port_ok",
            "chat ok",
            port="chat",
            model=self.model,
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
        debug_port_io(
            _LOGGER,
            "chat io",
            port="chat",
            model=self.model,
            request=messages,
            response=result.model_dump(mode="json"),
        )
        return result
