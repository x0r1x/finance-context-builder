from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnvSpec:
    env_name: str
    field_name: str


ENV_SPECS: tuple[EnvSpec, ...] = (
    EnvSpec("DATA_DIR", "data_dir"),
    EnvSpec("SESSION_ID", "session_id"),
    EnvSpec("MAX_UPLOAD_BYTES", "max_upload_bytes"),
    EnvSpec("MAX_CONCURRENT_JOBS", "max_concurrent_jobs"),
    EnvSpec("JOB_MEMORY_BUDGET_BYTES", "job_memory_budget_bytes"),
    EnvSpec("JOB_TIMEOUT_SEC", "job_timeout_sec"),
    EnvSpec("SOFFICE_BIN", "soffice_bin"),
    EnvSpec("XLS_CONVERT_TIMEOUT_SEC", "xls_convert_timeout_sec"),
    EnvSpec("LOG_LEVEL", "log_level"),
    EnvSpec("LOG_JSON", "log_json"),
)
