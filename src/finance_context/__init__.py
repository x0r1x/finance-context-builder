"""Excel cash-flow models → JSON/Markdown financial context."""

import os

# PyArrow's default mimalloc pool reserves address space up front. The system
# allocator tracks real RSS, which is what the job watchdog measures.
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

__version__ = "0.1.0"
