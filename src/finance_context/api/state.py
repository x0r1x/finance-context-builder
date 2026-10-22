from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from finance_context.adapters.disk_store import DiskStore
from finance_context.app.pipeline import Pipeline
from finance_context.jobqueue.queue import RQJobQueue
from finance_context.settings import Settings


@dataclass
class AppContext:
    settings: Settings
    store: DiskStore
    pipeline: Pipeline
    queue: RQJobQueue
    max_upload_bytes: int

    @property
    def data_dir(self) -> Path:
        return self.settings.data_dir
