from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BlankRule = Literal["none", "default", "pool_default", "as_is"]


@dataclass(frozen=True)
class EnvSpec:
    env_name: str
    field_name: str
    blank: BlankRule


ENV_SPECS: tuple[EnvSpec, ...] = (
    EnvSpec("DATA_DIR", "data_dir", "as_is"),
    EnvSpec("SESSION_ID", "session_id", "as_is"),
    EnvSpec("MAX_UPLOAD_BYTES", "max_upload_bytes", "as_is"),
    EnvSpec("MAX_CONCURRENT_JOBS", "max_concurrent_jobs", "pool_default"),
    EnvSpec("JOB_TIMEOUT_SEC", "job_timeout_sec", "as_is"),
    EnvSpec("LOG_LEVEL", "log_level", "as_is"),
    EnvSpec("LOG_JSON", "log_json", "as_is"),
)
