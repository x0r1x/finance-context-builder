from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from finance_context.ports.protocols import ChatPort, EmbedPort
from finance_context.settings_ports import build_chat, build_embed

if TYPE_CHECKING:
    from finance_context.mapping.models import MappingThresholds

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_DATA_DIR = Path("data")
_DEFAULT_DATA_DIR = DEFAULT_DATA_DIR
_DEFAULT_LLM_SLOT_WAIT_SEC = 120.0
_DEFAULT_EMBEDDING_BATCH_SIZE = 32
_DEFAULT_EMBEDDING_CONCURRENCY = 1
_DEFAULT_LLM_CONCURRENCY = 1
_DEFAULT_MAX_CONCURRENT_JOBS = 2
_POOL_DEFAULTS = {
    "embedding_batch_size": _DEFAULT_EMBEDDING_BATCH_SIZE,
    "embedding_concurrency": _DEFAULT_EMBEDDING_CONCURRENCY,
    "llm_concurrency": _DEFAULT_LLM_CONCURRENCY,
    "max_concurrent_jobs": _DEFAULT_MAX_CONCURRENT_JOBS,
}
_THRESHOLD_FIELDS = (
    "concept_accept_min",
    "embed_score_min",
    "embed_score_gap",
    "embed_top_k",
    "mint_score_max",
)


def _blank_to_none(value: object) -> object:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _loopback_rewrite_host() -> str | None:
    return "host.docker.internal" if Path("/.dockerenv").exists() else None


def normalize_openai_base_url(url: str, *, rewrite_loopback_to: str | None = None) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    if path == "":
        path = "/v1"
    netloc = parts.netloc
    host = parts.hostname
    if rewrite_loopback_to and host in _LOOPBACK_HOSTS:
        userinfo = ""
        if parts.username:
            userinfo = parts.username
            if parts.password is not None:
                userinfo += f":{parts.password}"
            userinfo += "@"
        hostport = rewrite_loopback_to
        if parts.port is not None:
            hostport = f"{hostport}:{parts.port}"
        netloc = f"{userinfo}{hostport}"
    return urlunsplit((parts.scheme, netloc, path, parts.query, parts.fragment))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    data_dir: Path = DEFAULT_DATA_DIR
    session_id: str = "local"
    max_upload_bytes: int = 50 * 1024 * 1024

    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "qwen3.6-27b-fp8"
    llm_tls_ca_file: Path | None = None
    llm_chat_path: str = "/chat/completions"

    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    embedding_model: str | None = None
    embedding_tls_ca_file: Path | None = None
    embedding_path: str = "/embeddings"
    embedding_batch_size: int = _DEFAULT_EMBEDDING_BATCH_SIZE
    embedding_concurrency: int = _DEFAULT_EMBEDDING_CONCURRENCY
    llm_concurrency: int = _DEFAULT_LLM_CONCURRENCY
    max_concurrent_jobs: int = _DEFAULT_MAX_CONCURRENT_JOBS

    llm_slot_wait_sec: float = _DEFAULT_LLM_SLOT_WAIT_SEC
    job_timeout_sec: float = 3600
    log_level: str = "INFO"
    log_json: bool = True

    concept_accept_min: float = Field(default=0.82, gt=0, le=1)
    embed_score_min: float = Field(default=0.85, gt=0, le=1)
    embed_score_gap: float = Field(default=0.08, ge=0, le=1)
    embed_top_k: int = Field(default=5, ge=1, le=32)
    mint_score_max: float = Field(default=0.5, gt=0, le=1)

    @field_validator(
        "llm_base_url",
        "llm_api_key",
        "embedding_base_url",
        "embedding_api_key",
        "embedding_model",
        "llm_tls_ca_file",
        "embedding_tls_ca_file",
        mode="before",
    )
    @classmethod
    def blank_str_to_none(cls, value: object) -> object:
        return _blank_to_none(value)

    @field_validator(
        "embedding_batch_size",
        "embedding_concurrency",
        "llm_concurrency",
        "max_concurrent_jobs",
        mode="before",
    )
    @classmethod
    def default_positive_int(cls, value: object, info: ValidationInfo) -> object:
        fallback = _POOL_DEFAULTS[info.field_name]
        if value is None or (isinstance(value, str) and not value.strip()):
            return fallback
        try:
            number = int(value)
        except (TypeError, ValueError):
            return fallback
        if number <= 0:
            return fallback
        return number

    @field_validator(*_THRESHOLD_FIELDS, mode="before")
    @classmethod
    def blank_threshold_inherits_default(cls, value: object, info: ValidationInfo) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return cls.model_fields[info.field_name].default
        return value

    @field_validator("llm_base_url", "embedding_base_url", mode="after")
    @classmethod
    def openai_compatible_base(cls, value: str | None) -> str | None:
        if not value:
            return None
        return normalize_openai_base_url(value, rewrite_loopback_to=_loopback_rewrite_host())

    def mapping_thresholds(self) -> MappingThresholds:
        from finance_context.mapping.models import MappingThresholds

        return MappingThresholds(
            concept_accept_min=self.concept_accept_min,
            embed_score_min=self.embed_score_min,
            embed_score_gap=self.embed_score_gap,
            embed_top_k=self.embed_top_k,
            mint_score_max=self.mint_score_max,
        )

    @classmethod
    def default_mapping_thresholds(cls) -> MappingThresholds:
        from finance_context.mapping.models import MappingThresholds

        fields = cls.model_fields
        return MappingThresholds(
            concept_accept_min=fields["concept_accept_min"].default,
            embed_score_min=fields["embed_score_min"].default,
            embed_score_gap=fields["embed_score_gap"].default,
            embed_top_k=fields["embed_top_k"].default,
            mint_score_max=fields["mint_score_max"].default,
        )

    def llm_configured(self) -> bool:
        return bool(self.llm_base_url)

    def embed_configured(self) -> bool:
        return bool(self.embedding_base_url and self.embedding_model)

    def chat(self) -> ChatPort | None:
        return build_chat(self)

    def embed(self) -> EmbedPort | None:
        return build_embed(self)
