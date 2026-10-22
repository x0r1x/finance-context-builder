"""Strong ETags for projections of a finished job."""

from __future__ import annotations

import json
from pathlib import Path

from starlette.responses import Response

CACHE_CONTROL = "private, no-cache"


def strong_etag(schema: str, content_sha256: str, *paths: Path) -> str:
    """Workbook hash plus file mtime and size."""
    parts = [schema, content_sha256]
    for path in paths:
        stat = path.stat()
        parts.append(str(stat.st_mtime_ns))
        parts.append(str(stat.st_size))
    return '"' + ":".join(parts) + '"'


def etag_matches(header: str | None, etag: str) -> bool:
    """Strong compare. A weak validator does not match. ``*`` matches any tag."""
    if header is None:
        return False
    text = header.strip()
    if not text:
        return False
    if text == "*":
        return True
    wanted = _unquote(etag)
    for part in text.split(","):
        token = part.strip()
        if token.upper().startswith("W/"):
            continue
        if token == etag or _unquote(token) == wanted:
            return True
    return False


def json_bytes(body: object) -> bytes:
    """The same encoding as Starlette's JSONResponse, done by the caller.

    Slice and trace routes call this from a worker thread so ``json.dumps``
    does not run on the event loop.
    """
    return json.dumps(
        body,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def cached_json(body: dict, etag: str) -> Response:
    return Response(
        content=json_bytes(body),
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": CACHE_CONTROL},
    )


def not_modified(etag: str) -> Response:
    return Response(status_code=304, headers={"ETag": etag, "Cache-Control": CACHE_CONTROL})


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] == '"':
        return token[1:-1]
    return token
