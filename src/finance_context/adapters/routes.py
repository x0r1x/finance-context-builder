from __future__ import annotations

from pathlib import Path

import httpx

from finance_context.adapters.tls import httpx_verify
from finance_context.errors import PortError

CHAT_SUFFIX = "/chat/completions"
EMBED_SUFFIX = "/embeddings"


def openai_path(value: str | None, default: str) -> str:
    if value is None or not str(value).strip():
        return default
    path = str(value).strip()
    if "://" in path or not path.startswith("/"):
        raise PortError("invalid openai path")
    return path


def join_route(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + openai_path(path, path)


def rewrite_request(
    request: httpx.Request,
    *,
    base_url: str,
    sdk_suffix: str,
    dest_path: str,
) -> httpx.Request:
    dest = openai_path(dest_path, sdk_suffix)
    if dest == sdk_suffix or not request.url.path.endswith(sdk_suffix):
        return request
    headers = httpx.Headers(request.headers)
    headers.pop("host", None)
    return httpx.Request(
        request.method,
        join_route(base_url, dest),
        headers=headers,
        content=request.content,
        extensions=request.extensions,
    )


class RewriteTransport(httpx.BaseTransport):
    def __init__(
        self,
        inner: httpx.BaseTransport,
        *,
        base_url: str,
        sdk_suffix: str,
        dest_path: str,
    ) -> None:
        self._inner = inner
        self._base_url = base_url
        self._sdk_suffix = sdk_suffix
        self._dest_path = dest_path

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._inner.handle_request(
            rewrite_request(
                request,
                base_url=self._base_url,
                sdk_suffix=self._sdk_suffix,
                dest_path=self._dest_path,
            )
        )

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if close is not None:
            close()


class AsyncRewriteTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        base_url: str,
        sdk_suffix: str,
        dest_path: str,
    ) -> None:
        self._inner = inner
        self._base_url = base_url
        self._sdk_suffix = sdk_suffix
        self._dest_path = dest_path

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._inner.handle_async_request(
            rewrite_request(
                request,
                base_url=self._base_url,
                sdk_suffix=self._sdk_suffix,
                dest_path=self._dest_path,
            )
        )

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if close is not None:
            await close()


def wrap_sync_transport(
    inner: httpx.BaseTransport | None,
    *,
    base_url: str,
    ca_file: Path | None,
    sdk_suffix: str,
    dest_path: str,
) -> httpx.BaseTransport:
    transport: httpx.BaseTransport
    if inner is None:
        transport = httpx.HTTPTransport(verify=httpx_verify(ca_file))
    else:
        transport = inner
    dest = openai_path(dest_path, sdk_suffix)
    if dest == sdk_suffix:
        return transport
    return RewriteTransport(
        transport, base_url=base_url, sdk_suffix=sdk_suffix, dest_path=dest
    )


def wrap_async_transport(
    inner: httpx.AsyncBaseTransport | httpx.BaseTransport | None,
    *,
    base_url: str,
    ca_file: Path | None,
    sdk_suffix: str,
    dest_path: str,
) -> httpx.AsyncBaseTransport:
    transport: httpx.AsyncBaseTransport
    if inner is None:
        transport = httpx.AsyncHTTPTransport(verify=httpx_verify(ca_file))
    else:
        transport = inner  # type: ignore[assignment]
    if not sdk_suffix:
        return transport
    dest = openai_path(dest_path, sdk_suffix)
    if dest == sdk_suffix:
        return transport
    return AsyncRewriteTransport(
        transport, base_url=base_url, sdk_suffix=sdk_suffix, dest_path=dest
    )
