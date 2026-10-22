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
        "source_filename",
        "authorization",
    }
)

_ALLOWED_EXTRA = frozenset(
    {
        "event",
        "stage",
        "duration_ms",
        "http_status",
        "reason",
        "skipped",
        "worker",
        "kind",
        "reachable",
        "latency_ms",
        "status",
        "error_code",
        "count",
        "cells",
        "formulas",
        "edges",
        "cell_edges",
        "estimate_bytes",
        "budget_bytes",
        "estimate_base_bytes",
        "bytes_per_formula",
        "bytes_per_expanded_cell",
        "bytes_per_present_cell",
        "floor_bytes",
        "api_reserve_fraction",
        "default_budget_bytes",
        "watch_interval_sec",
        "range_expand_cap",
        "oom_score_adj",
        "preflight_refusal",
        "parse_streaming",
        "json_stream",
        "parquet_row_group_rows",
        "compile_string_pool",
        "compile_ast_zlib",
        "graph_stream",
        "layout_columnar_facts",
        "malloc_arena_max",
        "malloc_trim",
        "estimate_streaming",
        "attempt",
        "retry_after_sec",
        "max_retries",
        "stale_from",
        "rss_in_mb",
        "rss_out_mb",
        "rss_peak_mb",
        "configured",
        "path",
        "method",
        "http_code",
        "exc_type",
        "job_id",
        "host",
        "queue",
        "session_id",
        "rq_job_id",
        "generation",
        "redis_scheme",
        "redis_host",
        "redis_port",
        "redis_db",
        "slots",
        "retry_in_sec",
        "inflight",
        "oom_kill",
        "timeout_sec",
        "margin_sec",
        "ttl_sec",
        "result_ttl_sec",
        "failure_ttl_sec",
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
        if key in FORBIDDEN_LOG_KEYS:
            continue
        extra[key] = value
    logger.log(level, msg, extra=extra, exc_info=exc_info)
