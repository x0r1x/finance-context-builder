#!/usr/bin/env python3
"""Write a synthetic workbook for a manual measurement.

The shapes live in ``tests/fixtures/synthetic``. Run ``--help`` on a shape
for its size flags. Bytes are deterministic for a given revision and seed.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.fixtures.synthetic.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
