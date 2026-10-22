"""Blocking API work runs here, on the limiter created in the app lifespan."""

from __future__ import annotations

from collections.abc import Callable

import anyio

from finance_context.api.state import AppContext


async def run_blocking[T](ctx: AppContext, fn: Callable[..., T], /, *args: object) -> T:
    """Run ``fn(*args)`` outside the event loop.

    The limiter is the one ``API_THREAD_LIMIT`` sized during lifespan. File
    I/O, hashing, JSON, and the synchronous RQ client go through this so a
    slow call cannot stall ``/healthz``.
    """
    limiter = ctx.thread_limiter
    if limiter is None:
        raise RuntimeError("API thread limiter is unset; the app lifespan did not run")
    return await anyio.to_thread.run_sync(fn, *args, limiter=limiter)
