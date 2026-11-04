from __future__ import annotations

import io
import logging
import sys
import threading
import time
import types

if sys.platform == "win32" and "fcntl" not in sys.modules:
    _fcntl = types.ModuleType("fcntl")
    _fcntl.LOCK_EX = 2  # type: ignore[attr-defined]
    _fcntl.LOCK_SH = 1  # type: ignore[attr-defined]
    _fcntl.LOCK_UN = 8  # type: ignore[attr-defined]
    _fcntl.LOCK_NB = 4  # type: ignore[attr-defined]
    _fcntl.flock = lambda *_args, **_kwargs: None  # type: ignore[attr-defined]
    sys.modules["fcntl"] = _fcntl

from finance_context.observability import configure_logging, flush_logs, log_event


class _BlockedStdout(io.TextIOBase):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.written = io.StringIO()

    def write(self, text: str) -> int:
        self.started.set()
        self.release.wait(5)
        self.written.write(text)
        return len(text)

    def flush(self) -> None:
        return None


def _logger_state(name: str) -> tuple[list[logging.Handler], int, bool]:
    logger = logging.getLogger(name)
    return list(logger.handlers), logger.level, logger.propagate


def _restore(name: str, state: tuple[list[logging.Handler], int, bool]) -> None:
    logger = logging.getLogger(name)
    logger.handlers[:] = state[0]
    logger.setLevel(state[1])
    logger.propagate = state[2]


def test_stdout_log_returns_while_the_pipe_write_is_blocked(monkeypatch) -> None:
    """A full stdout pipe must not hold the caller. The API logs on the event loop."""
    blocked = _BlockedStdout()
    monkeypatch.setattr(sys, "stdout", blocked)
    saved = {name: _logger_state(name) for name in ("finance_context", "rq")}
    try:
        configure_logging(level="INFO", json_output=True)
        log_event(
            logging.getLogger("finance_context.api.http"),
            logging.INFO,
            "http_start",
            "request start",
            method="GET",
            path="/v1/context-jobs/x",
        )
        assert blocked.started.wait(2)
        started = time.monotonic()
        log_event(
            logging.getLogger("finance_context.api.http"),
            logging.INFO,
            "http_done",
            "request done",
            method="GET",
            path="/v1/context-jobs/x",
            status=200,
            duration_ms=2,
        )
        assert time.monotonic() - started < 0.2
        blocked.release.set()
        flush_logs()
        text = blocked.written.getvalue()
        assert '"event": "http_start"' in text
        assert '"event": "http_done"' in text
        assert '"duration_ms": 2' in text
    finally:
        blocked.release.set()
        from finance_context.observability import _stop_log_listener

        _stop_log_listener()
        for name, state in saved.items():
            _restore(name, state)
