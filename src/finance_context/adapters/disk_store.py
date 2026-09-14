from __future__ import annotations

from pathlib import Path


class DiskStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def dest_dir(self, job_id: str) -> Path:
        return self.root / "jobs" / job_id
