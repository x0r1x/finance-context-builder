from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Self
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from pydantic import SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATA_DIR = Path("data")
# How many books run at once. The per-job RSS cap does not depend on this number.
_DEFAULT_MAX_CONCURRENT_JOBS = 4
DEFAULT_API_RESERVE_FRACTION = 0.25
# No-cgroup fallback, and the ceiling when a cgroup limit is known.
# With a known cgroup the per-job cap is the lesser of (limit minus the API
# reserve) and these 1536 MiB. Slots do not divide it.
DEFAULT_MEMORY_BUDGET_BYTES = 1536 * 1024 * 1024
DEFAULT_MEMORY_WATCH_INTERVAL_SEC = 0.2
DEFAULT_RANGE_EXPAND_CAP = 2000
# A whole column or row, or a rectangle over this many cells, is one range node.
DEFAULT_RANGE_NODE_MIN_CELLS = 64
# Members of one range node listed by a trace. 0 lists all of them.
DEFAULT_TRACE_RANGE_MEMBERS = 200
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
# AnyIO's default CapacityLimiter. Blocking API work uses this many threads.
DEFAULT_API_THREAD_LIMIT = 40
# How long one /readyz disk and Redis probe is reused.
DEFAULT_READYZ_CACHE_TTL_SEC = 10.0
# Redis stream of progress events for one job. Approximate cap.
DEFAULT_JOB_EVENTS_MAXLEN = 256
# Seconds the stream key lives after a terminal event.
DEFAULT_JOB_EVENTS_TTL_SEC = 3600
# SSE comment interval while the stream is quiet.
DEFAULT_JOB_EVENTS_HEARTBEAT_SEC = 15.0
# Open GET .../events responses at once.
DEFAULT_JOB_EVENTS_MAX_CONNECTIONS = 32


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
    # The reserve fraction covers the API and the embedded worker in that cgroup.
    # An explicit value wins and is not clipped by the 1536 MiB ceiling.
    job_memory_budget_bytes: int | None = None
    job_timeout_sec: float = 3600
    soffice_bin: str | None = None
    xls_convert_timeout_sec: float = 300
    log_level: str = "INFO"
    log_json: bool = True
    # Share of the cgroup kept for the API and the embedded worker.
    # Slots do not divide the remainder.
    job_memory_api_reserve_fraction: float = DEFAULT_API_RESERVE_FRACTION
    job_memory_default_budget_bytes: int = DEFAULT_MEMORY_BUDGET_BYTES
    job_memory_watch_interval_sec: float = DEFAULT_MEMORY_WATCH_INTERVAL_SEC
    range_expand_cap: int = DEFAULT_RANGE_EXPAND_CAP
    range_node_min_cells: int = DEFAULT_RANGE_NODE_MIN_CELLS
    trace_range_members: int = DEFAULT_TRACE_RANGE_MEMBERS
    job_oom_score_adj: int = DEFAULT_OOM_SCORE_ADJ
    # lxml iterparse into column lists. Raw parquet uses parquet_row_group_rows.
    parse_streaming: bool = True
    # json.dump instead of json.dumps for context.json and other job JSON.
    json_stream: bool = True
    # Raw, IR and graph parquet row-group size. 0 writes one group, matching the
    # single-table bytes. One group of a large book does not fit DuckDB's 256MB.
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
    # RQ. The API and the worker both take the address from here. No in-process fallback.
    # No userinfo: AUTH is sent only when a username or password is configured.
    redis_url: str = "redis://redis:6379/0"
    # Empty means the default user. A non-empty value overrides URL userinfo.
    redis_username: str = ""
    # Empty or unset means no password. A non-empty value overrides URL userinfo.
    redis_password: SecretStr | None = None
    redis_connect_timeout_sec: float = 2
    queue_name: str = "finance-context"
    # Queued plus started jobs for one SESSION_ID. Above this, POST is 429.
    job_queue_max_per_session: int = 3
    # RQ job_timeout is JOB_TIMEOUT_SEC plus both stop pauses plus this margin.
    queue_job_timeout_margin_sec: float = 120
    # 0 means a queued job can wait without an RQ ttl.
    queue_job_ttl_sec: int = 0
    queue_result_ttl_sec: int = 3600
    queue_failure_ttl_sec: int = 604800
    # Retries after a lost worker or horse. Pipeline errors and OOM do not use this.
    job_retry_on_lost: int = 1
    # Seconds between attempts while another session holds shared/books/{job_id}/.lock.
    # The horse maps SharedCacheBusy to Retry(interval) and releases the slot.
    # CLI build still waits in-process, and stops before JOB_TIMEOUT_SEC.
    shared_cache_busy_retry_sec: float = DEFAULT_SHARED_CACHE_BUSY_RETRY_SEC
    # Extra attempts after the first busy result. 0 fails on that first result.
    shared_cache_busy_max_retries: int = DEFAULT_SHARED_CACHE_BUSY_MAX_RETRIES
    job_admission_cgroup_check: bool = True
    job_admission_retry_sec: float = 30
    # How often a child, and a horse, notice that their parent pid changed.
    job_parent_watch_interval_sec: float = 1.0
    # Retry-After on 429 and 503.
    job_retry_after_sec: int = 30
    # The horse stops the spawn child with SIGTERM, then SIGKILL.
    job_terminate_grace_sec: float = 5
    job_kill_grace_sec: float = 5
    # Threads for blocking API work: file I/O, hashing, JSON, and the RQ client.
    api_thread_limit: int = DEFAULT_API_THREAD_LIMIT
    # /readyz reuses one writability check and Redis ping for this long.
    # The usual probe uses an unnamed inode. Without O_TMPFILE the probe
    # creates a short-lived .ready- file and unlinks it. /healthz does no I/O.
    # A dead embedded worker is not part of this cache.
    readyz_cache_ttl_sec: float = DEFAULT_READYZ_CACHE_TTL_SEC
    # `finance-context serve` starts the RQ pool as its own child process.
    embedded_worker: bool = True
    # Seconds before that child is started again after it exits.
    embedded_worker_restart_backoff_sec: float = 1.0
    # Approximate MAXLEN for one job's progress stream.
    job_events_maxlen: int = DEFAULT_JOB_EVENTS_MAXLEN
    # EXPIRE the stream key this many seconds after succeeded, failed, or queue_lost.
    job_events_ttl_sec: int = DEFAULT_JOB_EVENTS_TTL_SEC
    # How often GET .../events writes an SSE comment while Redis is quiet.
    job_events_heartbeat_sec: float = DEFAULT_JOB_EVENTS_HEARTBEAT_SEC
    # Concurrent SSE responses. Above this, the route is 503.
    job_events_max_connections: int = DEFAULT_JOB_EVENTS_MAX_CONNECTIONS
    # Empty quartet: DuckDB reads and writes DATA_DIR only. All four together:
    # the same bytes are also copied to the bucket. A partial quartet fails startup.
    minio_endpoint: str = ""
    minio_bucket: str = ""
    minio_access_key: str = ""
    minio_secret_key: SecretStr | None = None
    # Optional key prefix inside the bucket. Empty is books/{job_id}/...
    minio_prefix: str = ""
    minio_region: str = "us-east-1"
    # Set only for temporary credentials. A static key leaves this empty.
    minio_session_token: SecretStr | None = None
    # Set only when the bucket requires an SSE-KMS key. Empty sends no header.
    minio_kms_key_id: str = ""

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
        "job_memory_default_budget_bytes",
        "range_expand_cap",
        "range_node_min_cells",
        "session_id_max_length",
        mode="before",
    )
    @classmethod
    def require_positive_int(cls, value: object, info: ValidationInfo) -> int:
        return _require_int(value, info, minimum=1)

    @field_validator(
        "trace_range_members",
        mode="before",
    )
    @classmethod
    def require_non_negative_int(cls, value: object, info: ValidationInfo) -> int:
        return _require_int(value, info, minimum=0)

    @field_validator(
        "job_memory_watch_interval_sec",
        "redis_connect_timeout_sec",
        "queue_job_timeout_margin_sec",
        "shared_cache_busy_retry_sec",
        "job_admission_retry_sec",
        "job_parent_watch_interval_sec",
        "job_terminate_grace_sec",
        "job_kill_grace_sec",
        "readyz_cache_ttl_sec",
        "embedded_worker_restart_backoff_sec",
        "job_events_heartbeat_sec",
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
        "job_admission_cgroup_check",
        "embedded_worker",
        mode="before",
    )
    @classmethod
    def require_bool(cls, value: object, info: ValidationInfo) -> bool:
        return _require_bool(value, info)

    @field_validator("parquet_row_group_rows", mode="before")
    @classmethod
    def require_row_group_rows(cls, value: object, info: ValidationInfo) -> int:
        return _require_whole_int(value, info, minimum=0)

    @field_validator("redis_url", mode="before")
    @classmethod
    def require_redis_url(cls, value: object, info: ValidationInfo) -> str:
        del info
        if not isinstance(value, str) or not value.strip():
            raise ValueError("REDIS_URL must be a Redis URL")
        token = value.strip()
        if not token.startswith(("redis://", "rediss://", "unix://")):
            raise ValueError("REDIS_URL must start with redis://, rediss://, or unix://")
        return token

    @field_validator("redis_username", mode="before")
    @classmethod
    def empty_redis_username(cls, value: object, info: ValidationInfo) -> str:
        del info
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("REDIS_USERNAME must be a string")
        return value.strip()

    @field_validator("redis_password", mode="before")
    @classmethod
    def empty_redis_password(cls, value: object, info: ValidationInfo) -> object:
        del info
        if value is None:
            return None
        if isinstance(value, SecretStr):
            return None if value.get_secret_value() == "" else value
        if isinstance(value, str):
            return None if value == "" else value
        raise ValueError("REDIS_PASSWORD must be a string")

    @field_validator("minio_endpoint", "minio_bucket", "minio_access_key", mode="before")
    @classmethod
    def empty_minio_text(cls, value: object, info: ValidationInfo) -> str:
        name = info.field_name.upper()
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        return value.strip()

    @field_validator("minio_endpoint")
    @classmethod
    def minio_endpoint_is_a_host(cls, value: str) -> str:
        if not value:
            return value
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("MINIO_ENDPOINT must start with http:// or https://")
        if parts.username or parts.password:
            raise ValueError("MINIO_ENDPOINT must not include credentials")
        if parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("MINIO_ENDPOINT must be a host and port, not a path")
        return value

    @field_validator("minio_secret_key", "minio_session_token", mode="before")
    @classmethod
    def empty_minio_secret(cls, value: object, info: ValidationInfo) -> object:
        name = info.field_name.upper()
        if value is None:
            return None
        if isinstance(value, SecretStr):
            return None if value.get_secret_value() == "" else value
        if isinstance(value, str):
            return None if value.strip() == "" else value.strip()
        raise ValueError(f"{name} must be a string")

    @field_validator("minio_prefix", mode="before")
    @classmethod
    def clean_minio_prefix(cls, value: object, info: ValidationInfo) -> str:
        del info
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("MINIO_PREFIX must be a string")
        token = value.strip().strip("/")
        if "\\" in token or any(char.isspace() for char in token):
            raise ValueError("MINIO_PREFIX must not contain spaces or backslashes")
        parts = [part for part in token.split("/") if part]
        if any(part in {".", ".."} for part in parts):
            raise ValueError("MINIO_PREFIX must be a relative path")
        return "/".join(parts)

    @field_validator("minio_region", mode="before")
    @classmethod
    def default_minio_region(cls, value: object, info: ValidationInfo) -> str:
        del info
        if value is None or (isinstance(value, str) and not value.strip()):
            return "us-east-1"
        if not isinstance(value, str):
            raise ValueError("MINIO_REGION must be a string")
        token = value.strip()
        if any(char.isspace() for char in token):
            raise ValueError("MINIO_REGION must be a single token")
        return token

    @field_validator("minio_kms_key_id", mode="before")
    @classmethod
    def empty_minio_kms_key(cls, value: object, info: ValidationInfo) -> str:
        del info
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValueError("MINIO_KMS_KEY_ID must be a string")
        token = value.strip()
        if any(char.isspace() for char in token) or "\\" in token:
            raise ValueError("MINIO_KMS_KEY_ID must be a single token")
        return token

    @field_validator("queue_name", mode="before")
    @classmethod
    def require_queue_name(cls, value: object, info: ValidationInfo) -> str:
        del info
        if not isinstance(value, str) or not value.strip():
            raise ValueError("QUEUE_NAME must be a non-empty name")
        token = value.strip()
        if any(char.isspace() for char in token) or "/" in token:
            raise ValueError("QUEUE_NAME must be a single token")
        return token

    @field_validator(
        "job_queue_max_per_session",
        "api_thread_limit",
        "job_events_maxlen",
        "job_events_ttl_sec",
        "job_events_max_connections",
        mode="before",
    )
    @classmethod
    def require_session_cap(cls, value: object, info: ValidationInfo) -> int:
        return _require_whole_int(value, info, minimum=1)

    @field_validator(
        "queue_job_ttl_sec",
        "queue_result_ttl_sec",
        "queue_failure_ttl_sec",
        "job_retry_on_lost",
        "shared_cache_busy_max_retries",
        "job_retry_after_sec",
        mode="before",
    )
    @classmethod
    def require_non_negative_whole(cls, value: object, info: ValidationInfo) -> int:
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

    @model_validator(mode="after")
    def redis_username_needs_password(self) -> Self:
        # Raises when a username, from the env or the URL, has no password.
        self.redis_from_url_args()
        return self

    @model_validator(mode="after")
    def minio_quartet_is_complete(self) -> Self:
        secret = ""
        if self.minio_secret_key is not None:
            secret = self.minio_secret_key.get_secret_value()
        filled = (
            bool(self.minio_endpoint),
            bool(self.minio_bucket),
            bool(self.minio_access_key),
            bool(secret),
        )
        if any(filled) and not all(filled):
            raise ValueError(
                "MINIO_ENDPOINT, MINIO_BUCKET, MINIO_ACCESS_KEY, and "
                "MINIO_SECRET_KEY must all be set or all be empty"
            )
        return self

    def minio_configured(self) -> bool:
        """True when the four connection fields are set. The bucket is a second copy."""
        secret = ""
        if self.minio_secret_key is not None:
            secret = self.minio_secret_key.get_secret_value()
        return bool(self.minio_endpoint and self.minio_bucket and self.minio_access_key and secret)

    def redis_from_url_args(self) -> tuple[str, dict[str, str]]:
        """URL and credential kwargs for ``Redis.from_url``.

        When ``REDIS_USERNAME`` and ``REDIS_PASSWORD`` are both empty, the URL
        is returned unchanged and the dict is empty. A credential-less URL
        then sends no AUTH. A non-empty env value overrides that one field of
        the URL userinfo; the other field still comes from the URL. redis-py
        lets the URL win over kwargs, so an override strips userinfo first.
        """
        user, password, override = self._resolved_redis_auth()
        if user is not None and password is None:
            raise ValueError("REDIS_USERNAME requires REDIS_PASSWORD")
        if not override:
            return self.redis_url, {}
        kwargs: dict[str, str] = {}
        if user is not None:
            kwargs["username"] = user
        if password is not None:
            kwargs["password"] = password
        return _strip_redis_credentials(self.redis_url), kwargs

    def _resolved_redis_auth(self) -> tuple[str | None, str | None, bool]:
        env_user = self.redis_username or None
        env_password = None
        if self.redis_password is not None:
            raw = self.redis_password.get_secret_value()
            env_password = raw if raw else None
        url_user, url_password = _url_redis_credentials(self.redis_url)
        user = env_user if env_user is not None else url_user
        password = env_password if env_password is not None else url_password
        return user, password, env_user is not None or env_password is not None

    def queue_job_timeout_sec(self) -> int:
        """RQ's spare limit. The horse's own JOB_TIMEOUT_SEC stays the real stop."""
        total = (
            self.job_timeout_sec
            + self.job_terminate_grace_sec
            + self.job_kill_grace_sec
            + self.queue_job_timeout_margin_sec
        )
        return max(1, math.ceil(total))


def write_redis_env(env: dict[str, str], settings: Settings) -> None:
    """Copy Redis settings into a child environment. Empty credentials are removed."""
    env["REDIS_URL"] = settings.redis_url
    if settings.redis_username:
        env["REDIS_USERNAME"] = settings.redis_username
    else:
        env.pop("REDIS_USERNAME", None)
    secret = ""
    if settings.redis_password is not None:
        secret = settings.redis_password.get_secret_value()
    if secret:
        env["REDIS_PASSWORD"] = secret
    else:
        env.pop("REDIS_PASSWORD", None)


def _url_redis_credentials(url: str) -> tuple[str | None, str | None]:
    parts = urlsplit(url)
    username = unquote(parts.username) if parts.username else None
    password = unquote(parts.password) if parts.password else None
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key == "username" and not username:
            username = value or None
        elif key == "password" and not password:
            password = value or None
    return username or None, password or None


def _strip_redis_credentials(url: str) -> str:
    """Drop userinfo and credential query keys. Host, db, and other query stay."""
    parts = urlsplit(url)
    if parts.scheme == "unix":
        netloc = ""
    else:
        host = parts.hostname or ""
        if host and ":" in host and not host.startswith("["):
            host = f"[{host}]"
        netloc = host
        if parts.port is not None:
            netloc = f"{host}:{parts.port}"
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key not in {"username", "password"}
        ]
    )
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


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
