from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    # Set in the API lifespan. AnyIO's limiter object, shared by every request.
    thread_limiter: Any = None

    @property
    def data_dir(self) -> Path:
        return self.settings.data_dir
