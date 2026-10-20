from __future__ import annotations

import importlib
from typing import Any

__all__ = ["build_context"]


def __getattr__(name: str) -> Any:
    """Load ``build_context`` on first use.

    A static import here would run while layout loads ``context.measure``, and
    the layout hash would then cover the whole context builder.
    """
    if name == "build_context":
        module = importlib.import_module(f"{__name__}.build")
        value = module.build_context
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
