"""A stand-in for the LibreOffice port: copies a prepared .xlsx instead of converting."""

from __future__ import annotations

import shutil
from pathlib import Path


class FakeConverter:
    def __init__(self, output: Path | None = None, *, available: bool = True) -> None:
        self.output = output
        self._available = available
        self.calls: list[Path] = []

    def available(self) -> bool:
        return self._available

    def convert(self, source: Path, out_dir: Path) -> Path:
        self.calls.append(source)
        assert self.output is not None, "no prepared .xlsx"
        target = out_dir / "converted.xlsx"
        shutil.copyfile(self.output, target)
        return target
