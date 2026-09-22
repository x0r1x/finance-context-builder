from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TextIO

job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
stage_var: ContextVar[str | None] = ContextVar("stage", default=None)

FORBIDDEN_LOG_KEYS = frozenset(
    {
        "actor_id",
        "api_key",
        "cached_value",
        "cell",
        "content",
        "filename",
        "formula",
        "formula_raw",
        "label",
        "messages",
        "prompt",
        "source_filename",
        "texts",
        "authorization",
    }
)

_ALLOWED_EXTRA = frozenset(
    {
        "event",
        "stage",
        "duration_ms",
        "port",
        "model",
        "http_status",
        "reason",
        "skipped",
        "worker",
        "kind",
        "reachable",
        "model_present",
        "latency_ms",
        "status",
        "error_code",
        "count",
        "configured",
        "path",
        "method",
        "http_code",
        "exc_type",
        "job_id",
        "host",
        "tls_ca",
        "request",
        "response",
        "batch",
        "batches",
    }
)


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
        aid = job_id_var.get()
        if aid:
            payload["job_id"] = aid
        st = stage_var.get()
        if st:
            payload.setdefault("stage", st)
        for key, value in record.__dict__.items():
            if key in FORBIDDEN_LOG_KEYS:
                continue
            if key in _ALLOWED_EXTRA and value is not None:
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exc_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = True,
    stream: TextIO | None = None,
) -> None:
    handler = logging.StreamHandler(stream or sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root = logging.getLogger("finance_context")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False
    for noisy in ("httpx", "httpcore", "openai", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def debug_port_io(logger: logging.Logger, msg: str, **fields: object) -> None:
    """Log model request and response bodies only when DEBUG is enabled."""
    if not logger.isEnabledFor(logging.DEBUG):
        return
    log_event(logger, logging.DEBUG, "port_io", msg, **fields)


def log_event(logger: logging.Logger, level: int, event: str, msg: str, **fields: object) -> None:
    extra = {"event": event}
    exc_info = fields.pop("exc_info", False)
    for key, value in fields.items():
        if key in FORBIDDEN_LOG_KEYS:
            continue
        extra[key] = value
    logger.log(level, msg, extra=extra, exc_info=exc_info)
