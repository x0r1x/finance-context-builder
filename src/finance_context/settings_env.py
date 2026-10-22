from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EnvSpec:
    env_name: str
    field_name: str


ENV_SPECS: tuple[EnvSpec, ...] = (
    EnvSpec("DATA_DIR", "data_dir"),
    EnvSpec("DATA_DIR_ALLOW_NETWORK_FS", "data_dir_allow_network_fs"),
    EnvSpec("SESSION_ID", "session_id"),
    EnvSpec("SESSION_ID_PATTERN", "session_id_pattern"),
    EnvSpec("SESSION_ID_MAX_LENGTH", "session_id_max_length"),
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
    EnvSpec("REDIS_URL", "redis_url"),
    EnvSpec("REDIS_CONNECT_TIMEOUT_SEC", "redis_connect_timeout_sec"),
    EnvSpec("QUEUE_NAME", "queue_name"),
    EnvSpec("JOB_QUEUE_MAX_PER_SESSION", "job_queue_max_per_session"),
    EnvSpec("QUEUE_JOB_TIMEOUT_MARGIN_SEC", "queue_job_timeout_margin_sec"),
    EnvSpec("QUEUE_JOB_TTL_SEC", "queue_job_ttl_sec"),
    EnvSpec("QUEUE_RESULT_TTL_SEC", "queue_result_ttl_sec"),
    EnvSpec("QUEUE_FAILURE_TTL_SEC", "queue_failure_ttl_sec"),
    EnvSpec("JOB_RETRY_ON_LOST", "job_retry_on_lost"),
    EnvSpec("SHARED_CACHE_BUSY_RETRY_SEC", "shared_cache_busy_retry_sec"),
    EnvSpec("SHARED_CACHE_BUSY_MAX_RETRIES", "shared_cache_busy_max_retries"),
    EnvSpec("JOB_ADMISSION_CGROUP_CHECK", "job_admission_cgroup_check"),
    EnvSpec("JOB_ADMISSION_RETRY_SEC", "job_admission_retry_sec"),
    EnvSpec("JOB_PARENT_WATCH_INTERVAL_SEC", "job_parent_watch_interval_sec"),
    EnvSpec("JOB_RETRY_AFTER_SEC", "job_retry_after_sec"),
    EnvSpec("JOB_TERMINATE_GRACE_SEC", "job_terminate_grace_sec"),
    EnvSpec("JOB_KILL_GRACE_SEC", "job_kill_grace_sec"),
    EnvSpec("EMBEDDED_WORKER", "embedded_worker"),
    EnvSpec("EMBEDDED_WORKER_RESTART_BACKOFF_SEC", "embedded_worker_restart_backoff_sec"),
)
