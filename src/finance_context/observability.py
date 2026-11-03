from __future__ import annotations

import atexit
import json
import logging
import logging.handlers
import queue
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


# Default stdout is written from a thread. The HTTP middleware logs on the
# event loop, and a full pipe blocks the caller; asyncio counts that wait as
# the callback. An explicit stream stays synchronous so a test can read it
# before the next line.
_log_listener: logging.handlers.QueueListener | None = None
_log_listener_atexit = False


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


def _stop_log_listener() -> None:
    global _log_listener
    listener = _log_listener
    _log_listener = None
    if listener is not None:
        listener.stop()


class _LiveStdoutHandler(logging.StreamHandler):
    """Write to the process stdout at emit time.

    pytest replaces ``sys.stdout`` for each test. A handler that kept the
    stream from ``configure_logging`` would write into the previous test's
    closed capture.
    """

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    def emit(self, record: logging.LogRecord) -> None:
        self.stream = sys.stdout
        super().emit(record)


def flush_logs() -> None:
    """Wait until queued stdout lines are written. A direct stream has nothing to flush."""
    listener = _log_listener
    if listener is None:
        return
    listener.queue.join()


def _queue_handlers(formatter: logging.Formatter) -> tuple[logging.Handler, logging.Handler]:
    global _log_listener, _log_listener_atexit
    _stop_log_listener()
    records: queue.Queue[logging.LogRecord] = queue.Queue()
    writer = _LiveStdoutHandler()
    writer.setFormatter(logging.Formatter("%(message)s"))
    listener = logging.handlers.QueueListener(records, writer, respect_handler_level=True)
    listener.start()
    _log_listener = listener
    if not _log_listener_atexit:
        atexit.register(_stop_log_listener)
        _log_listener_atexit = True
    finance = logging.handlers.QueueHandler(records)
    finance.setFormatter(formatter)
    rq = logging.handlers.QueueHandler(records)
    rq.setFormatter(formatter)
    return finance, rq


def configure_logging(
    *,
    level: str = "INFO",
    json_output: bool = True,
    stream: TextIO | None = None,
) -> None:
    formatter_source = _build_handler(level=level, json_output=json_output, stream=stream)
    chosen = getattr(logging, level.upper(), logging.INFO)
    if stream is None:
        formatter = formatter_source.formatter
        if formatter is None:
            formatter = logging.Formatter("%(message)s")
        finance_handler, rq_handler = _queue_handlers(formatter)
    else:
        _stop_log_listener()
        finance_handler = formatter_source
        rq_handler = _build_handler(level=level, json_output=json_output, stream=stream)
    root = logging.getLogger("finance_context")
    root.handlers.clear()
    root.addHandler(finance_handler)
    root.setLevel(chosen)
    root.propagate = False
    # RQ's setup_loghandlers adds color handlers only when no parent handler
    # exists. One JSON handler here covers rq.worker and rq.worker_pool.
    rq_logger = logging.getLogger("rq")
    rq_logger.handlers.clear()
    rq_logger.addHandler(rq_handler)
    rq_logger.setLevel(chosen)
    rq_logger.propagate = False
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # cli and create_app build Settings before this handler exists.
    from finance_context.settings import Settings, _warn_duckdb_memory_limit

    _warn_duckdb_memory_limit(Settings())


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
