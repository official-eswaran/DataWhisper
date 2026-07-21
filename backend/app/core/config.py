import secrets
import warnings
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel value shipped in source control. If SECRET_KEY still equals this in a
# non-DEBUG run, the process must refuse to start — a known signing key means
# anyone can forge admin JWTs.
_INSECURE_SECRET = "change-this-to-a-random-secret-key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "DataWhisper"
    DEBUG: bool = False

    # ── JWT ────────────────────────────────────────────────────────────────────
    SECRET_KEY: str = _INSECURE_SECRET
    ALGORITHM: str = "HS256"
    # Short-lived access token; refresh tokens handle long sessions + revocation.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Default seed passwords (override via .env before first run) ─────────────
    ADMIN_PASSWORD: str = "Admin@2024"
    MANAGER_PASSWORD: str = "Manager@2024"

    # ── Account lockout ─────────────────────────────────────────────────────────
    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_MINUTES: int = 15

    # ── Local LLM (Ollama) ──────────────────────────────────────────────────────
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_MODEL: str = "llama3.2:3b"
    LLM_TIMEOUT_SECONDS: int = 60
    # Cap simultaneous LLM calls so a burst of users cannot overwhelm a single
    # Ollama instance (the real bottleneck of the system).
    LLM_MAX_CONCURRENCY: int = 4

    # ── Data paths ──────────────────────────────────────────────────────────────
    UPLOAD_DIR: Path = Path("data/uploads")
    DATABASE_DIR: Path = Path("data/databases")

    # ── Metadata database (users, orgs, audit, sessions, tokens) ────────────────
    # Empty → SQLite file under DATABASE_DIR (dev/single-node). For production
    # multi-node, set a Postgres URL, e.g.
    #   postgresql+psycopg://user:pass@host:5432/datawhisper
    DATABASE_URL: str = ""

    @property
    def database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"sqlite:///{(self.DATABASE_DIR / 'app.db').as_posix()}"

    # ── Limits ──────────────────────────────────────────────────────────────────
    MAX_UPLOAD_SIZE_MB: int = 500
    MAX_RESULT_ROWS: int = 10_000
    # Sessions (uploaded data) older than this are eligible for cleanup.
    SESSION_TTL_HOURS: int = 24
    # Conversation-memory retention for the in-process store.
    CONVERSATION_TTL_MINUTES: int = 120
    MAX_ACTIVE_CONVERSATIONS: int = 10_000

    # ── Rate limits (slowapi syntax) ────────────────────────────────────────────
    RATE_LIMIT_LOGIN: str = "10/minute"
    RATE_LIMIT_QUERY: str = "30/minute"
    RATE_LIMIT_UPLOAD: str = "20/minute"

    # ── Shared state / scaling ──────────────────────────────────────────────────
    # When set (e.g. redis://redis:6379/0), the conversation store and rate
    # limiter use Redis, making WEB_CONCURRENCY>1 and multi-replica correct.
    # Empty → in-process fallback (single worker only).
    REDIS_URL: str = ""

    # ── Observability ───────────────────────────────────────────────────────────
    LOG_JSON: bool = False          # structured JSON logs (for shipping to Loki/ELK)
    METRICS_ENABLED: bool = True    # expose Prometheus /metrics

    # Error tracking + tracing — both are no-ops unless configured, so local/dev
    # and tests are unaffected.
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = ""
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0
    # OTLP/HTTP collector base URL, e.g. http://otel-collector:4318 (Tempo/Jaeger).
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""
    OTEL_SERVICE_NAME: str = "datawhisper-backend"

    # ── LLM reliability ─────────────────────────────────────────────────────────
    LLM_RETRY_ATTEMPTS: int = 2                 # total attempts per call
    LLM_RETRY_BACKOFF_SECONDS: float = 0.5      # base backoff (exponential)
    LLM_CIRCUIT_FAIL_THRESHOLD: int = 5         # consecutive failures to trip
    LLM_CIRCUIT_RESET_SECONDS: int = 30         # cool-down before half-open

    # ── LLM response cache (Redis when REDIS_URL set, else in-process) ──────────
    LLM_CACHE_ENABLED: bool = True
    LLM_CACHE_TTL_SECONDS: int = 3600
    LLM_CACHE_MAX_ENTRIES: int = 5000           # in-memory cap only

    # ── Dataset storage (per-session DuckDB files) ──────────────────────────────
    # "local" keeps *.duckdb on DATABASE_DIR — needs a ReadWriteMany volume to be
    # shared across replicas. "s3" stores them in object storage and materialises
    # them to a local cache on demand, so replicas need no shared filesystem.
    DATASET_STORAGE_BACKEND: str = "local"      # local | s3
    DATASET_S3_BUCKET: str = ""
    DATASET_S3_PREFIX: str = "datasets/"
    DATASET_S3_ENDPOINT_URL: str = ""           # set for MinIO / GCS-compat
    DATASET_S3_REGION: str = ""

    @property
    def rate_limit_storage_uri(self) -> str:
        return self.REDIS_URL or "memory://"

    # ── CORS — comma-separated origins. "*" is refused when DEBUG is False. ──────
    ALLOWED_ORIGINS: str = "*"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @field_validator("SECRET_KEY")
    @classmethod
    def _strip_secret(cls, v: str) -> str:
        return v.strip()

    @model_validator(mode="after")
    def _enforce_production_safety(self) -> "Settings":
        insecure_secret = (not self.SECRET_KEY) or self.SECRET_KEY == _INSECURE_SECRET or len(self.SECRET_KEY) < 32
        wildcard_cors = "*" in self.allowed_origins_list

        if self.DEBUG:
            if insecure_secret:
                # Never run with a predictable key, even in dev — generate an
                # ephemeral one so tokens issued this run cannot be forged.
                object.__setattr__(self, "SECRET_KEY", secrets.token_hex(32))
                warnings.warn(
                    "SECRET_KEY was unset/insecure — generated an ephemeral key "
                    "for this DEBUG run. Set a real SECRET_KEY in .env.",
                    stacklevel=2,
                )
            return self

        # Production (DEBUG=False): fail fast rather than boot insecurely.
        problems = []
        if insecure_secret:
            problems.append(
                "SECRET_KEY is missing, default, or shorter than 32 chars. "
                'Generate one with: python3 -c "import secrets; print(secrets.token_hex(32))"'
            )
        if wildcard_cors:
            problems.append(
                'ALLOWED_ORIGINS="*" is not allowed in production. '
                "List explicit origins, e.g. https://app.example.com"
            )
        if problems:
            raise RuntimeError(
                "Refusing to start with insecure configuration:\n  - "
                + "\n  - ".join(problems)
            )
        return self


settings = Settings()
