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
    EnvSpec("LLM_BASE_URL", "llm_base_url", "none"),
    EnvSpec("LLM_API_KEY", "llm_api_key", "none"),
    EnvSpec("LLM_MODEL", "llm_model", "as_is"),
    EnvSpec("LLM_TLS_CA_FILE", "llm_tls_ca_file", "none"),
    EnvSpec("LLM_CHAT_PATH", "llm_chat_path", "as_is"),
    EnvSpec("EMBEDDING_BASE_URL", "embedding_base_url", "none"),
    EnvSpec("EMBEDDING_API_KEY", "embedding_api_key", "none"),
    EnvSpec("EMBEDDING_MODEL", "embedding_model", "none"),
    EnvSpec("EMBEDDING_TLS_CA_FILE", "embedding_tls_ca_file", "none"),
    EnvSpec("EMBEDDING_PATH", "embedding_path", "as_is"),
    EnvSpec("EMBEDDING_BATCH_SIZE", "embedding_batch_size", "pool_default"),
    EnvSpec("EMBEDDING_CONCURRENCY", "embedding_concurrency", "pool_default"),
    EnvSpec("LLM_CONCURRENCY", "llm_concurrency", "pool_default"),
    EnvSpec("MAX_CONCURRENT_JOBS", "max_concurrent_jobs", "pool_default"),
    EnvSpec("LLM_SLOT_WAIT_SEC", "llm_slot_wait_sec", "as_is"),
    EnvSpec("JOB_TIMEOUT_SEC", "job_timeout_sec", "as_is"),
    EnvSpec("LOG_LEVEL", "log_level", "as_is"),
    EnvSpec("LOG_JSON", "log_json", "as_is"),
    EnvSpec("CONCEPT_ACCEPT_MIN", "concept_accept_min", "default"),
    EnvSpec("EMBED_SCORE_MIN", "embed_score_min", "default"),
    EnvSpec("EMBED_SCORE_GAP", "embed_score_gap", "default"),
    EnvSpec("EMBED_TOP_K", "embed_top_k", "default"),
    EnvSpec("MINT_SCORE_MAX", "mint_score_max", "default"),
)
