"""Strong ETags for projections of a finished job."""

from __future__ import annotations

from pathlib import Path

from starlette.responses import JSONResponse, Response

CACHE_CONTROL = "private, no-cache"


def strong_etag(schema: str, content_sha256: str, *paths: Path) -> str:
    """Workbook hash plus file stat. A remap keeps the hash and rewrites the file."""
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


def cached_json(body: dict, etag: str) -> JSONResponse:
    return JSONResponse(body, headers={"ETag": etag, "Cache-Control": CACHE_CONTROL})


def not_modified(etag: str) -> Response:
    return Response(status_code=304, headers={"ETag": etag, "Cache-Control": CACHE_CONTROL})


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] == '"':
        return token[1:-1]
    return token
