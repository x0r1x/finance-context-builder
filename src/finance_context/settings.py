from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from finance_context.errors import PortError
from finance_context.ports.protocols import ChatPort, EmbedPort

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_DEFAULT_DATA_DIR = Path("data")
_DEFAULT_LLM_SLOT_WAIT_SEC = 120.0
_DEFAULT_EMBEDDING_BATCH_SIZE = 32
_DEFAULT_EMBEDDING_CONCURRENCY = 1
_DEFAULT_LLM_CONCURRENCY = 1
_POOL_DEFAULTS = {
    "embedding_batch_size": _DEFAULT_EMBEDDING_BATCH_SIZE,
    "embedding_concurrency": _DEFAULT_EMBEDDING_CONCURRENCY,
    "llm_concurrency": _DEFAULT_LLM_CONCURRENCY,
}


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

    data_dir: Path = _DEFAULT_DATA_DIR
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

    llm_slot_wait_sec: float = _DEFAULT_LLM_SLOT_WAIT_SEC
    job_timeout_sec: float = 3600
    log_level: str = "INFO"
    log_json: bool = True

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

    @field_validator("llm_base_url", "embedding_base_url", mode="after")
    @classmethod
    def openai_compatible_base(cls, value: str | None) -> str | None:
        if not value:
            return None
        return normalize_openai_base_url(value, rewrite_loopback_to=_loopback_rewrite_host())

    def llm_configured(self) -> bool:
        return bool(self.llm_base_url)

    def embed_configured(self) -> bool:
        return bool(self.embedding_base_url and self.embedding_model)

    def chat(self) -> ChatPort | None:
        if not self.llm_configured():
            return None
        from finance_context.adapters.openai_chat import OpenAIChat

        try:
            return OpenAIChat(
                base_url=self.llm_base_url,
                api_key=self.llm_api_key,
                model=self.llm_model,
                ca_file=self.llm_tls_ca_file,
                chat_path=self.llm_chat_path,
                concurrency=self.llm_concurrency,
            )
        except PortError:
            return None

    def embed(self) -> EmbedPort | None:
        if not self.embed_configured():
            return None
        from finance_context.adapters.openai_embed import OpenAIEmbed

        try:
            return OpenAIEmbed(
                base_url=self.embedding_base_url,
                api_key=self.embedding_api_key,
                model=self.embedding_model,
                ca_file=self.embedding_tls_ca_file,
                embed_path=self.embedding_path,
                batch_size=self.embedding_batch_size,
                concurrency=self.embedding_concurrency,
            )
        except PortError:
            return None
