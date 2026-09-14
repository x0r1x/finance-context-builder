from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from finance_context.errors import ContextError

_HTTP = {
    "unsupported_media_type": 400,
    "empty_file": 400,
    "file_too_large": 413,
    "encrypted_workbook": 422,
    "zip_rejected": 422,
    "not_found": 404,
    "report_not_ready": 409,
}


class ApiError(Exception):
    def __init__(self, http_status: int, code: str, **extra: object) -> None:
        self.http_status = http_status
        self.code = code
        self.extra = extra
        super().__init__(code)


def error_body(code: str, **extra: object) -> dict[str, object]:
    body: dict[str, object] = {"error": code}
    body.update(extra)
    return body


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(error_body(exc.code, **exc.extra), status_code=exc.http_status)


async def context_error_handler(_request: Request, exc: ContextError) -> JSONResponse:
    status = _HTTP.get(exc.code, 400)
    extra: dict[str, object] = {}
    if exc.detail:
        extra["detail"] = exc.detail
    return JSONResponse(error_body(exc.code, **extra), status_code=status)
