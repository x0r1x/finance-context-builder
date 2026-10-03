from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import Request

from finance_context.api.errors import ApiError
from finance_context.api.schemas import ErrorBody
from finance_context.api.state import AppContext

_LOGGER = logging.getLogger("finance_context.api")
_ALLOWED = {".xlsx", ".xlsm"}
_JOB_ID = re.compile(r"^[0-9a-f]{64}$")

_MARKDOWN = {
    "content": {"text/markdown": {"schema": {"type": "string"}}},
    "description": "Markdown rendering of the same document.",
}

def _errors(*codes: int) -> dict[int, dict[str, object]]:
    return {code: {"model": ErrorBody} for code in codes}
_STAGE_RANK = {
    "queued": 0,
    "parse": 1,
    "compile": 2,
    "layout": 3,
    "mapping": 4,
    "graph": 5,
    "build": 6,
    "render": 7,
    "done": 8,
}

def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx

def _schema_id(model: type) -> str:
    return str(model.model_fields["schema_version"].default)


def _content_sha256(dest: Path) -> str:
    from finance_context.api.routes.jobs import _load_meta

    meta = _load_meta(dest)
    if meta is not None and isinstance(meta.get("content_sha256"), str):
        return meta["content_sha256"]
    return ""


def _read_json(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiError(409, "report_not_ready") from exc
    if not isinstance(loaded, dict):
        raise ApiError(409, "report_not_ready")
    return loaded

def _check_job_id(job_id: str) -> None:
    if _JOB_ID.fullmatch(job_id) is None:
        raise ApiError(404, "not_found")


def _require_artifact(request: Request, job_id: str, name: str) -> Path:
    _check_job_id(job_id)
    dest = _ctx(request).store.dest_dir(job_id)
    path = dest / name
    if not dest.exists():
        raise ApiError(404, "not_found")
    if not path.exists():
        raise ApiError(409, "report_not_ready")
    return path
