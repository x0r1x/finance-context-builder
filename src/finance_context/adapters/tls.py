from __future__ import annotations

import ssl
from pathlib import Path
from urllib.parse import urlsplit

from finance_context.errors import PortError


def httpx_verify(ca_file: Path | None) -> bool | ssl.SSLContext:
    if ca_file is None:
        return True
    if not ca_file.is_file():
        raise PortError("tls ca file missing")
    return ssl.create_default_context(cafile=str(ca_file))


def log_host(base_url: str) -> str:
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    if parts.port is not None:
        return f"{host}:{parts.port}"
    return host
