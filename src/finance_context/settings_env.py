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
    EnvSpec("JOB_ESTIMATE_BASE_BYTES", "job_estimate_base_bytes"),
    EnvSpec("JOB_ESTIMATE_BYTES_PER_FORMULA", "job_estimate_bytes_per_formula"),
    EnvSpec("JOB_ESTIMATE_BYTES_PER_EXPANDED_CELL", "job_estimate_bytes_per_expanded_cell"),
    EnvSpec("JOB_ESTIMATE_BYTES_PER_PRESENT_CELL", "job_estimate_bytes_per_present_cell"),
    EnvSpec("JOB_ESTIMATE_UNSTREAMED_BASE_BYTES", "job_estimate_unstreamed_base_bytes"),
    EnvSpec(
        "JOB_ESTIMATE_UNSTREAMED_BYTES_PER_FORMULA",
        "job_estimate_unstreamed_bytes_per_formula",
    ),
    EnvSpec(
        "JOB_ESTIMATE_UNSTREAMED_BYTES_PER_EXPANDED_CELL",
        "job_estimate_unstreamed_bytes_per_expanded_cell",
    ),
    EnvSpec(
        "JOB_ESTIMATE_UNSTREAMED_BYTES_PER_PRESENT_CELL",
        "job_estimate_unstreamed_bytes_per_present_cell",
    ),
    EnvSpec("JOB_MEMORY_FLOOR_BYTES", "job_memory_floor_bytes"),
    EnvSpec("JOB_MEMORY_API_RESERVE_FRACTION", "job_memory_api_reserve_fraction"),
    EnvSpec("JOB_MEMORY_DEFAULT_BUDGET_BYTES", "job_memory_default_budget_bytes"),
    EnvSpec("JOB_MEMORY_WATCH_INTERVAL_SEC", "job_memory_watch_interval_sec"),
    EnvSpec("RANGE_EXPAND_CAP", "range_expand_cap"),
    EnvSpec("JOB_OOM_SCORE_ADJ", "job_oom_score_adj"),
    EnvSpec("JOB_MEMORY_PREFLIGHT_REFUSAL", "job_memory_preflight_refusal"),
    EnvSpec("PARSE_STREAMING", "parse_streaming"),
    EnvSpec("JSON_STREAM", "json_stream"),
    EnvSpec("PARQUET_ROW_GROUP_ROWS", "parquet_row_group_rows"),
    EnvSpec("COMPILE_STRING_POOL", "compile_string_pool"),
    EnvSpec("COMPILE_AST_ZLIB", "compile_ast_zlib"),
    EnvSpec("GRAPH_STREAM", "graph_stream"),
    EnvSpec("LAYOUT_COLUMNAR_FACTS", "layout_columnar_facts"),
    EnvSpec("JOB_MALLOC_ARENA_MAX", "job_malloc_arena_max"),
    EnvSpec("JOB_MALLOC_TRIM", "job_malloc_trim"),
)
