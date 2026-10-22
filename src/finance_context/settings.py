from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Self

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATA_DIR = Path("data")
_DEFAULT_MAX_CONCURRENT_JOBS = 2
# Fitted to ru_maxrss of the default streaming pipeline: 166 MiB plus 128 bytes
# per formula, 220 per expanded cell, and 1120 per present cell. On Packt, RVI,
# the private .xlsx and .xls books, and the 384k-formula synthetic book the
# estimate is 1.08–1.20 of that peak, and the synthetic book stays under the
# 1536 MiB default budget. If PARSE_STREAMING, GRAPH_STREAM, or
# COMPILE_AST_ZLIB is off, preflight uses the unstreamed coefficients below
# (the previous fit, about 1.04–1.16 of the switches-off peak).
DEFAULT_ESTIMATE_BASE_BYTES = 166 * 1024 * 1024
DEFAULT_ESTIMATE_BYTES_PER_FORMULA = 128
DEFAULT_ESTIMATE_BYTES_PER_EXPANDED_CELL = 220
DEFAULT_ESTIMATE_BYTES_PER_PRESENT_CELL = 1120
DEFAULT_ESTIMATE_UNSTREAMED_BASE_BYTES = 132 * 1024 * 1024
DEFAULT_ESTIMATE_UNSTREAMED_BYTES_PER_FORMULA = 3840
DEFAULT_ESTIMATE_UNSTREAMED_BYTES_PER_EXPANDED_CELL = 960
DEFAULT_ESTIMATE_UNSTREAMED_BYTES_PER_PRESENT_CELL = 960
DEFAULT_MEMORY_FLOOR_BYTES = 128 * 1024 * 1024
DEFAULT_API_RESERVE_FRACTION = 0.25
DEFAULT_MEMORY_BUDGET_BYTES = 1536 * 1024 * 1024
DEFAULT_MEMORY_WATCH_INTERVAL_SEC = 0.2
DEFAULT_RANGE_EXPAND_CAP = 2000
DEFAULT_OOM_SCORE_ADJ = 1000
# One parquet row group of this many rows. 0 keeps a single row group.
DEFAULT_PARQUET_ROW_GROUP_ROWS = 65536
# glibc arenas for a spawned job child. None leaves an inherited value.
DEFAULT_MALLOC_ARENA_MAX = 2
# One directory segment. Length is SESSION_ID_MAX_LENGTH, not part of the pattern.
DEFAULT_SESSION_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
DEFAULT_SESSION_ID_MAX_LENGTH = 64
# Wait between attempts while another session holds the shared book lock.
DEFAULT_SHARED_CACHE_BUSY_RETRY_SEC = 30
# Extra attempts after the first busy result. 0 fails on the first busy result.
# 120 is the queue bound. Those waits happen outside the worker. The interim
# in-process sleep stops before JOB_TIMEOUT_SEC so the child can record
# shared_cache_busy before the parent watchdog.
DEFAULT_SHARED_CACHE_BUSY_MAX_RETRIES = 120


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    data_dir: Path = DEFAULT_DATA_DIR
    # Startup refuses a DATA_DIR on nfs, cifs/smb, sshfs, afp, or webdav unless this is set.
    data_dir_allow_network_fs: bool = False
    session_id: str = "local"
    session_id_pattern: str = DEFAULT_SESSION_ID_PATTERN
    session_id_max_length: int = DEFAULT_SESSION_ID_MAX_LENGTH
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
    # Previous fit. Used when parse, graph streaming, or AST compression is off.
    job_estimate_unstreamed_base_bytes: int = DEFAULT_ESTIMATE_UNSTREAMED_BASE_BYTES
    job_estimate_unstreamed_bytes_per_formula: int = (
        DEFAULT_ESTIMATE_UNSTREAMED_BYTES_PER_FORMULA
    )
    job_estimate_unstreamed_bytes_per_expanded_cell: int = (
        DEFAULT_ESTIMATE_UNSTREAMED_BYTES_PER_EXPANDED_CELL
    )
    job_estimate_unstreamed_bytes_per_present_cell: int = (
        DEFAULT_ESTIMATE_UNSTREAMED_BYTES_PER_PRESENT_CELL
    )
    job_memory_floor_bytes: int = DEFAULT_MEMORY_FLOOR_BYTES
    job_memory_api_reserve_fraction: float = DEFAULT_API_RESERVE_FRACTION
    job_memory_default_budget_bytes: int = DEFAULT_MEMORY_BUDGET_BYTES
    job_memory_watch_interval_sec: float = DEFAULT_MEMORY_WATCH_INTERVAL_SEC
    range_expand_cap: int = DEFAULT_RANGE_EXPAND_CAP
    job_oom_score_adj: int = DEFAULT_OOM_SCORE_ADJ
    job_memory_preflight_refusal: bool = True
    # lxml iterparse into column lists. Raw parquet stays one row group.
    parse_streaming: bool = True
    # json.dump instead of json.dumps for context.json and other job JSON.
    json_stream: bool = True
    # IR and graph parquet row-group size. 0 writes one group, matching the
    # single-table bytes. Raw cells and presence ignore this and stay one group.
    parquet_row_group_rows: int = DEFAULT_PARQUET_ROW_GROUP_ROWS
    # Compile-local map of repeated node ids. Dropped after compile. Not sys.intern.
    compile_string_pool: bool = True
    # Keep formula AST JSON zlib-compressed on the cell until relations run.
    compile_ast_zlib: bool = True
    # Column batches of cell edges, no second in-memory edge list, streamed graph files.
    graph_stream: bool = True
    # Arrow scan of indent and outline. An all-zero book skips the dict list.
    layout_columnar_facts: bool = True
    # MALLOC_ARENA_MAX on the spawned job child. 0 or blank keeps an inherited value.
    job_malloc_arena_max: int | None = DEFAULT_MALLOC_ARENA_MAX
    # malloc_trim after each pipeline stage. A no-op where libc has no such function.
    job_malloc_trim: bool = True
    # Seconds between attempts while another session holds shared/books/{job_id}/.lock.
    # PR3 maps SharedCacheBusy.retry_after_sec to an RQ Retry interval.
    shared_cache_busy_retry_sec: float = DEFAULT_SHARED_CACHE_BUSY_RETRY_SEC
    # How many extra attempts follow the first busy result. 0 fails immediately.
    shared_cache_busy_max_retries: int = DEFAULT_SHARED_CACHE_BUSY_MAX_RETRIES

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
        "job_estimate_unstreamed_base_bytes",
        "job_memory_floor_bytes",
        "job_memory_default_budget_bytes",
        "range_expand_cap",
        "session_id_max_length",
        mode="before",
    )
    @classmethod
    def require_positive_int(cls, value: object, info: ValidationInfo) -> int:
        return _require_int(value, info, minimum=1)

    @field_validator(
        "job_estimate_bytes_per_formula",
        "job_estimate_bytes_per_expanded_cell",
        "job_estimate_bytes_per_present_cell",
        "job_estimate_unstreamed_bytes_per_formula",
        "job_estimate_unstreamed_bytes_per_expanded_cell",
        "job_estimate_unstreamed_bytes_per_present_cell",
        mode="before",
    )
    @classmethod
    def require_non_negative_int(cls, value: object, info: ValidationInfo) -> int:
        return _require_int(value, info, minimum=0)

    @field_validator(
        "job_memory_watch_interval_sec",
        "shared_cache_busy_retry_sec",
        mode="before",
    )
    @classmethod
    def require_positive_seconds(cls, value: object, info: ValidationInfo) -> float:
        name = info.field_name.upper()
        message = f"{name} must be a positive number of seconds"
        if isinstance(value, bool):
            raise ValueError(message)
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(message) from exc
        if not math.isfinite(number) or number <= 0:
            raise ValueError(message)
        return number

    @field_validator("job_memory_api_reserve_fraction", mode="before")
    @classmethod
    def require_open_unit_interval(cls, value: object, info: ValidationInfo) -> float:
        name = info.field_name.upper()
        message = f"{name} must be greater than 0 and less than 1"
        if isinstance(value, bool):
            raise ValueError(message)
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(message) from exc
        if not math.isfinite(number) or not 0 < number < 1:
            raise ValueError(message)
        return number

    @field_validator("job_oom_score_adj", mode="before")
    @classmethod
    def require_oom_score(cls, value: object, info: ValidationInfo) -> int:
        number = _require_int(value, info, minimum=-1000)
        if number > 1000:
            raise ValueError(f"{info.field_name.upper()} must be from -1000 to 1000")
        return number

    @field_validator(
        "parse_streaming",
        "json_stream",
        "compile_string_pool",
        "compile_ast_zlib",
        "graph_stream",
        "layout_columnar_facts",
        "job_malloc_trim",
        "data_dir_allow_network_fs",
        mode="before",
    )
    @classmethod
    def require_bool(cls, value: object, info: ValidationInfo) -> bool:
        return _require_bool(value, info)

    @field_validator("shared_cache_busy_max_retries", mode="before")
    @classmethod
    def require_busy_retries(cls, value: object, info: ValidationInfo) -> int:
        return _require_whole_int(value, info, minimum=0)

    @field_validator("parquet_row_group_rows", mode="before")
    @classmethod
    def require_row_group_rows(cls, value: object, info: ValidationInfo) -> int:
        return _require_whole_int(value, info, minimum=0)

    @field_validator("job_malloc_arena_max", mode="before")
    @classmethod
    def optional_arena_max(cls, value: object, info: ValidationInfo) -> int | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        if value == 0 or (isinstance(value, str) and value.strip() in {"0", "+0", "-0"}):
            return None
        return _require_whole_int(value, info, minimum=1)

    @model_validator(mode="after")
    def session_id_matches_pattern(self) -> Self:
        try:
            pattern = re.compile(self.session_id_pattern)
        except re.error as exc:
            raise ValueError(
                f"SESSION_ID_PATTERN is not a valid regular expression: {exc}"
            ) from exc
        session = self.session_id
        if pattern.fullmatch(session) is None or len(session) > self.session_id_max_length:
            raise ValueError(
                "SESSION_ID must match SESSION_ID_PATTERN "
                f"({self.session_id_pattern}) and be at most "
                f"{self.session_id_max_length} characters; refusing {session!r}"
            )
        return self


def _require_bool(value: object, info: ValidationInfo) -> bool:
    name = info.field_name.upper()
    message = f"{name} must be a boolean"
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
    raise ValueError(message)


def _require_whole_int(value: object, info: ValidationInfo, *, minimum: int) -> int:
    """Reject bools, floats, and non-finite text. A bool is not a row-group size."""
    name = info.field_name.upper()
    message = f"{name} must be an integer >= {minimum}"
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(message)
    if isinstance(value, str):
        token = value.strip()
        if token.lower() in {"inf", "+inf", "-inf", "infinity", "nan", "+nan", "-nan"}:
            raise ValueError(message)
        if not token.lstrip("+-").isdigit():
            raise ValueError(message)
        value = token
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(message) from exc
    if number < minimum:
        raise ValueError(message)
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
