from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from finance_context.adapters.disk_store import DiskStore
from finance_context.adapters.memory_bus import MemoryJobBus
from finance_context.app.pipeline import Pipeline
from finance_context.settings import Settings


@dataclass
class AppContext:
    settings: Settings
    store: DiskStore
    bus: MemoryJobBus
    pipeline: Pipeline
    max_upload_bytes: int

    @property
    def data_dir(self) -> Path:
        return self.settings.data_dir
