from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ArtifactStore(Protocol):
    def dest_dir(self, job_id: str) -> Path: ...
