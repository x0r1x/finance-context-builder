from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx

from finance_context.adapters.routes import EMBED_SUFFIX, openai_path, wrap_sync_transport
from finance_context.adapters.tls import log_host
from finance_context.errors import PortError
from finance_context.observability import debug_port_io, log_event
from finance_context.settings import (
    _DEFAULT_EMBEDDING_BATCH_SIZE,
    _DEFAULT_EMBEDDING_CONCURRENCY,
)

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
        batch_size: int = _DEFAULT_EMBEDDING_BATCH_SIZE,
        concurrency: int = _DEFAULT_EMBEDDING_CONCURRENCY,
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
        self._batch_size = batch_size if batch_size > 0 else _DEFAULT_EMBEDDING_BATCH_SIZE
        self._concurrency = concurrency if concurrency > 0 else _DEFAULT_EMBEDDING_CONCURRENCY
        http = httpx.Client(
            transport=transport,
            limits=httpx.Limits(
                max_connections=self._concurrency,
                max_keepalive_connections=self._concurrency,
            ),
        )
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
        if not texts:
            return []
        batches = [
            texts[start : start + self._batch_size]
            for start in range(0, len(texts), self._batch_size)
        ]
        workers = min(self._concurrency, len(batches))
        ordered: list[list[list[float]] | None] = [None] * len(batches)
        if workers == 1:
            for index, batch in enumerate(batches):
                ordered[index] = self._embed_batch(index, batch, batches=len(batches))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._embed_batch, index, batch, batches=len(batches)): index
                    for index, batch in enumerate(batches)
                }
                try:
                    for future in as_completed(futures):
                        ordered[futures[future]] = future.result()
                except Exception:
                    for future in futures:
                        future.cancel()
                    raise
        flat: list[list[float]] = []
        for vectors in ordered:
            if vectors is None:
                raise PortError("embed failed", port="embed")
            flat.extend(vectors)
        return flat

    def _embed_batch(
        self, index: int, batch: list[str], *, batches: int
    ) -> list[list[float]]:
        t0 = time.monotonic()
        try:
            response = self._client.embeddings.create(model=self.model, input=batch)
            vectors = _vectors_in_order(response.data)
            if len(vectors) != len(batch):
                raise ValueError("embed response length")
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            debug_port_io(
                _LOGGER,
                "embed io",
                port="embed",
                model=self.model,
                request=batch,
                batch=index,
                batches=batches,
            )
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
                batch=index,
                batches=batches,
            )
            raise PortError("embed failed", port="embed", status_code=status_code) from exc
        latency_ms = int((time.monotonic() - t0) * 1000)
        debug_port_io(
            _LOGGER,
            "embed io",
            port="embed",
            model=self.model,
            request=batch,
            response=vectors,
            batch=index,
            batches=batches,
            latency_ms=latency_ms,
        )
        log_event(
            _LOGGER,
            logging.INFO,
            "port_ok",
            "embed ok",
            port="embed",
            model=self.model,
            latency_ms=latency_ms,
            count=len(batch),
            batch=index,
            batches=batches,
        )
        return vectors


def _vectors_in_order(data: object) -> list[list[float]]:
    items = list(data)  # type: ignore[arg-type]
    if items and all(getattr(item, "index", None) is not None for item in items):
        items.sort(key=lambda item: item.index)
    return [list(item.embedding) for item in items]
