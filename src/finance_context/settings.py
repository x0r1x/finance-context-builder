from __future__ import annotations

from pathlib import Path

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATA_DIR = Path("data")
_DEFAULT_MAX_CONCURRENT_JOBS = 2
# Fitted to measured highest-stage RSS: 132 MiB plus 3840 bytes per formula,
# 960 per expanded cell, and 960 per present cell. Every profiled book lands
# between 1.07 and 1.15 of its peak, under the 1536 MiB default budget except
# the 380k-formula timeline.
DEFAULT_ESTIMATE_BASE_BYTES = 132 * 1024 * 1024
DEFAULT_ESTIMATE_BYTES_PER_FORMULA = 3840
DEFAULT_ESTIMATE_BYTES_PER_EXPANDED_CELL = 960
DEFAULT_ESTIMATE_BYTES_PER_PRESENT_CELL = 960
DEFAULT_MEMORY_FLOOR_BYTES = 128 * 1024 * 1024
DEFAULT_API_RESERVE_FRACTION = 0.25
DEFAULT_MEMORY_BUDGET_BYTES = 1536 * 1024 * 1024
DEFAULT_MEMORY_WATCH_INTERVAL_SEC = 0.2
DEFAULT_RANGE_EXPAND_CAP = 2000
DEFAULT_OOM_SCORE_ADJ = 1000


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
    # Per-job RSS cap in bytes. Unset means "derive from the cgroup limit".
    job_memory_budget_bytes: int | None = None
    job_timeout_sec: float = 3600
    soffice_bin: str | None = None
    xls_convert_timeout_sec: float = 300
    log_level: str = "INFO"
    log_json: bool = True
    job_estimate_base_bytes: int = DEFAULT_ESTIMATE_BASE_BYTES
    job_estimate_bytes_per_formula: int = DEFAULT_ESTIMATE_BYTES_PER_FORMULA
    job_estimate_bytes_per_expanded_cell: int = DEFAULT_ESTIMATE_BYTES_PER_EXPANDED_CELL
    job_estimate_bytes_per_present_cell: int = DEFAULT_ESTIMATE_BYTES_PER_PRESENT_CELL
    job_memory_floor_bytes: int = DEFAULT_MEMORY_FLOOR_BYTES
    job_memory_api_reserve_fraction: float = DEFAULT_API_RESERVE_FRACTION
    job_memory_default_budget_bytes: int = DEFAULT_MEMORY_BUDGET_BYTES
    job_memory_watch_interval_sec: float = DEFAULT_MEMORY_WATCH_INTERVAL_SEC
    range_expand_cap: int = DEFAULT_RANGE_EXPAND_CAP
    job_oom_score_adj: int = DEFAULT_OOM_SCORE_ADJ
    job_memory_preflight_refusal: bool = True

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

    @field_validator("job_memory_budget_bytes", mode="before")
    @classmethod
    def optional_positive_budget(cls, value: object, info: ValidationInfo) -> object:
        del info
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        if number <= 0:
            return None
        return number

    @field_validator(
        "job_estimate_base_bytes",
        "job_memory_floor_bytes",
        "job_memory_default_budget_bytes",
        "range_expand_cap",
        mode="before",
    )
    @classmethod
    def require_positive_int(cls, value: object, info: ValidationInfo) -> int:
        return _require_int(value, info, minimum=1)

    @field_validator(
        "job_estimate_bytes_per_formula",
        "job_estimate_bytes_per_expanded_cell",
        "job_estimate_bytes_per_present_cell",
        mode="before",
    )
    @classmethod
    def require_non_negative_int(cls, value: object, info: ValidationInfo) -> int:
        return _require_int(value, info, minimum=0)

    @field_validator("job_memory_watch_interval_sec", mode="before")
    @classmethod
    def require_positive_seconds(cls, value: object, info: ValidationInfo) -> float:
        name = info.field_name.upper()
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a positive number of seconds") from exc
        if number <= 0:
            raise ValueError(f"{name} must be a positive number of seconds")
        return number

    @field_validator("job_memory_api_reserve_fraction", mode="before")
    @classmethod
    def require_open_unit_interval(cls, value: object, info: ValidationInfo) -> float:
        name = info.field_name.upper()
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be greater than 0 and less than 1") from exc
        if not 0 < number < 1:
            raise ValueError(f"{name} must be greater than 0 and less than 1")
        return number

    @field_validator("job_oom_score_adj", mode="before")
    @classmethod
    def require_oom_score(cls, value: object, info: ValidationInfo) -> int:
        number = _require_int(value, info, minimum=-1000)
        if number > 1000:
            raise ValueError(f"{info.field_name.upper()} must be from -1000 to 1000")
        return number


def _require_int(value: object, info: ValidationInfo, *, minimum: int) -> int:
    name = info.field_name.upper()
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer >= {minimum}") from exc
    if isinstance(value, bool) or number < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    if info.field_name == "job_oom_score_adj" and number > 1000:
        raise ValueError(f"{name} must be from -1000 to 1000")
    return number
