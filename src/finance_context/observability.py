from __future__ import annotations

import json
import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import TextIO

job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
stage_var: ContextVar[str | None] = ContextVar("stage", default=None)
source_filename_var: ContextVar[str | None] = ContextVar("source_filename", default=None)

# Workbook names use source_filename. `filename` stays forbidden because it is
# the LogRecord attribute for the .py file that called the logger.
FORBIDDEN_LOG_KEYS = frozenset(
    {
        "actor_id",
        "api_key",
        "authorization",
        "cached_value",
        "cell",
        "content",
        "filename",
        "formula",
        "formula_raw",
        "label",
    }
)

# Blank record attributes, plus the two the formatter adds itself.
# Anything else on the record came from log_event and is emitted.
_RESERVED_LOG_KEYS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_KEYS or key in FORBIDDEN_LOG_KEYS or value is None:
                continue
            payload[key] = value
        if "job_id" not in payload:
            aid = job_id_var.get()
            if aid:
                payload["job_id"] = aid
        if "source_filename" not in payload:
            book = source_filename_var.get()
            if book:
                payload["source_filename"] = book
        if "stage" not in payload:
            st = stage_var.get()
            if st:
                payload["stage"] = st
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def _build_handler(
    *,
    level: str,
    json_output: bool,
    stream: TextIO | None,
) -> logging.Handler:
    del level
    handler = logging.StreamHandler(stream or sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    return handler


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = True,
    stream: TextIO | None = None,
) -> None:
    handler = _build_handler(level=level, json_output=json_output, stream=stream)
    chosen = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger("finance_context")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(chosen)
    root.propagate = False
    # RQ's setup_loghandlers adds color handlers only when no parent handler
    # exists. One JSON handler here covers rq.worker and rq.worker_pool.
    rq_logger = logging.getLogger("rq")
    rq_logger.handlers.clear()
    rq_logger.addHandler(_build_handler(level=level, json_output=json_output, stream=stream))
    rq_logger.setLevel(chosen)
    rq_logger.propagate = False
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, level: int, event: str, msg: str, **fields: object) -> None:
    extra = {"event": event}
    exc_info = fields.pop("exc_info", False)
    for key, value in fields.items():
        if key in FORBIDDEN_LOG_KEYS or key in _RESERVED_LOG_KEYS:
            continue
        extra[key] = value
    logger.log(level, msg, extra=extra, exc_info=exc_info)


@contextmanager
def bind_log_context(
    job_id: str | None = None,
    source_filename: str | None = None,
) -> Generator[None, None, None]:
    """Attach the job id and workbook name to later log lines.

    A bind that is already active stays as it is. The inner call does not
    clear it on the way out, so a failure log after ``Pipeline.run`` still
    sees the id the child set.
    """
    tokens: list[tuple[ContextVar[str | None], Token[str | None]]] = []
    if job_id and job_id_var.get() is None:
        tokens.append((job_id_var, job_id_var.set(job_id)))
    if source_filename and source_filename_var.get() is None:
        tokens.append((source_filename_var, source_filename_var.set(source_filename)))
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
