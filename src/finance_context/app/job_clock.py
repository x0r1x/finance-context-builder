"""Process-start clock for a spawned job child.

Imported before the rest of the pipeline so the stamp lands before the heavy
imports. The parent watchdog starts at ``proc.start()``, which is this stamp
plus interpreter startup.
"""

from __future__ import annotations

import time

process_mono_start = time.monotonic()
