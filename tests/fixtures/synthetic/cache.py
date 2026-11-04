"""Keep a generated workbook in a directory the caller owns.

Tests pass ``tmp_path`` or the pytest cache directory. A hit is the file
already written for this generator revision, so a second test does not build
the same book again. Bump ``GENERATOR_REV`` when a shape changes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

GENERATOR_REV = 1


def cached_workbook(directory: Path, name: str, build: Callable[[Path], Path]) -> Path:
    """Return ``directory/name`` for this revision, building it once."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.r{GENERATOR_REV}.xlsx"
    if path.is_file() and path.stat().st_size > 0:
        return path
    temporary = directory / f".{name}.r{GENERATOR_REV}.xlsx.tmp"
    try:
        build(temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
