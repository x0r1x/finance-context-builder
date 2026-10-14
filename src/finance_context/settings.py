from __future__ import annotations

from pathlib import Path

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATA_DIR = Path("data")
_DEFAULT_MAX_CONCURRENT_JOBS = 2


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    data_dir: Path = DEFAULT_DATA_DIR
    session_id: str = "local"
    max_upload_bytes: int = 50 * 1024 * 1024
    max_concurrent_jobs: int = _DEFAULT_MAX_CONCURRENT_JOBS
    job_timeout_sec: float = 3600
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("max_concurrent_jobs", mode="before")
    @classmethod
    def default_positive_int(cls, value: object, info: ValidationInfo) -> object:
        del info
        if value is None or (isinstance(value, str) and not value.strip()):
            return _DEFAULT_MAX_CONCURRENT_JOBS
        try:
            number = int(value)
        except (TypeError, ValueError):
            return _DEFAULT_MAX_CONCURRENT_JOBS
        if number <= 0:
            return _DEFAULT_MAX_CONCURRENT_JOBS
        return number
