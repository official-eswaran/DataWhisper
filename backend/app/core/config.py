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

    # ── Demo seeding ────────────────────────────────────────────────────────────
    # On an empty database, init_db can seed a demo org (ceo/manager) with the
    # passwords below. That is convenient for dev/test but dangerous in
    # production: a forgotten deploy would ship known owner credentials. So
    # seeding is gated (issue #23). None = "auto": follow DEBUG, so dev/test
    # (DEBUG=true) seed and production (DEBUG=false) does not. Set explicitly to
    # force either way.
    SEED_DEMO_DATA: bool | None = None
    # Default seed passwords (override via .env before first run). Only ever used
    # when demo seeding is enabled.
    ADMIN_PASSWORD: str = "Admin@2024"
    MANAGER_PASSWORD: str = "Manager@2024"

    @property
    def should_seed_demo(self) -> bool:
        """Whether to seed the demo org. Auto-off in production."""
        return self.DEBUG if self.SEED_DEMO_DATA is None else self.SEED_DEMO_DATA

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
    # Registration creates a whole org with a free query/upload budget, so it is
    # far more expensive to abuse than a login. It gets its own, much tighter
    # per-IP limit rather than sharing the login limit (issue #21).
    RATE_LIMIT_REGISTER: str = "5/hour"

    # ── Signup gating (issue #21) ───────────────────────────────────────────────
    # Public self-service signup is an abuse vector on a compute-heavy product:
    # each new org gets free LLM quota, and quotas are per-org, so "make another
    # org" bypasses them. Set SIGNUPS_OPEN=false to close public registration for
    # private/invite-only deployments — /api/auth/register then returns 403.
    # (Existing orgs and their users are unaffected.) Default stays open so the
    # current behaviour is unchanged.
    SIGNUPS_OPEN: bool = True

    # ── Email verification (issue #21) ──────────────────────────────────────────
    # The rate limit above only slows a single IP, and SIGNUPS_OPEN=false is
    # all-or-nothing. Requiring a verified address before *queries run* puts a
    # per-identity cost on the abuse path while leaving registration itself open.
    #
    # None = "auto": follow production, i.e. required when DEBUG is false. This
    # mirrors SEED_DEMO_DATA rather than defaulting to a bare bool, so dev and
    # the test suite are unaffected without anyone having to set anything.
    REQUIRE_EMAIL_VERIFICATION: bool | None = None

    # Gate the work, not the account: registration and login still succeed and
    # return tokens, so the user can reach a UI that tells them to check their
    # mail. Only the expensive endpoints refuse.
    EMAIL_VERIFICATION_TTL_HOURS: int = 24
    # Public origin used to build the link in the mail. Empty → the mailer emits
    # the bare token and says so, which is all a no-op transport can honestly do.
    APP_BASE_URL: str = ""

    @property
    def should_require_email_verification(self) -> bool:
        """Whether unverified accounts are blocked from queries. Auto-off in dev."""
        if self.REQUIRE_EMAIL_VERIFICATION is None:
            return not self.DEBUG
        return self.REQUIRE_EMAIL_VERIFICATION

    # ── Outbound mail ───────────────────────────────────────────────────────────
    # Unset → the mailer is a no-op that logs, exactly like SENTRY_DSN and the
    # OTel endpoint. Set SMTP_HOST and mail is actually sent (issue #21).
    SMTP_HOST: str = ""
    # 587 is submission-with-STARTTLS, the default everywhere. 465 is implicit
    # TLS and switches the transport to SMTP_SSL automatically.
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    # Envelope sender. Providers reject mail whose From they do not own, so this
    # has no useful default — empty falls back to the username, which is the
    # address most providers expect anyway.
    SMTP_FROM: str = ""
    # STARTTLS on a plaintext connection. Turning this off sends credentials in
    # the clear, so the mailer refuses to authenticate without TLS regardless.
    SMTP_STARTTLS: bool = True
    # Registration blocks on the send, so this bounds how long a signup can hang
    # on an unreachable mail server.
    SMTP_TIMEOUT_SECONDS: float = 10.0

    @property
    def smtp_from(self) -> str:
        return self.SMTP_FROM or self.SMTP_USERNAME

    # ── Signup captcha (issue #21) ──────────────────────────────────────────────
    # Unset → no challenge and nothing verified, exactly like SMTP_HOST. Set a
    # provider's key pair and /api/auth/register refuses to create an org
    # without a solved challenge. This is what the per-IP register limit cannot
    # do: it only slows one address, and a distributed script mints orgs — each
    # with a free LLM budget — at whatever rate it has addresses.
    #
    # "hcaptcha" or "turnstile"; both speak the same siteverify contract. The
    # provider is what selects the CSP origins the widget needs, so an unknown
    # value disables the feature rather than half-enabling it.
    CAPTCHA_PROVIDER: str = "hcaptcha"
    # Public — it is rendered into the page and served by /api/auth/signup-config.
    CAPTCHA_SITE_KEY: str = ""
    # Secret — the thing that makes a verdict mean anything. Its presence is
    # what switches the feature on: a site key alone would draw a widget whose
    # answer nobody checks, which is worse than drawing none.
    CAPTCHA_SECRET: str = ""
    # Registration blocks on this call, so it bounds how long a signup hangs on
    # an unreachable provider. Shorter than SMTP's because this one runs before
    # anything is written and the user is waiting on it.
    CAPTCHA_TIMEOUT_SECONDS: float = 5.0
    # Override for a test double or an enterprise egress proxy. Must be https —
    # the verifier refuses anything else rather than posting the secret in the
    # clear. Empty → the provider's documented endpoint.
    CAPTCHA_VERIFY_URL: str = ""

    # ── Shared state / scaling ──────────────────────────────────────────────────
    # When set (e.g. redis://redis:6379/0), the conversation store and rate
    # limiter use Redis, making WEB_CONCURRENCY>1 and multi-replica correct.
    # Empty → in-process fallback (single worker only).
    REDIS_URL: str = ""

    # In-process state is *correct* on one replica and silently wrong on several
    # (per-pod cache, split conversations, per-pod rate limits), and a process
    # can't see its own replica count — so the default is a loud startup warning.
    # Any deployment that can scale past one replica should set this true, which
    # turns the warning into a refusal to start (issue #29). The k8s manifests do.
    REQUIRE_SHARED_STATE: bool = False

    # ── Audit chain ─────────────────────────────────────────────────────────────
    # Sign a chain checkpoint every N entries so verification can be bounded to
    # "everything since the last checkpoint" instead of a full linear re-walk
    # (issue #30). Smaller = cheaper incremental verifies, more signed rows.
    # 0 disables checkpointing entirely (verification is then always full).
    AUDIT_CHECKPOINT_INTERVAL: int = 1000

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

    # ── Stripe billing (issue #5) ───────────────────────────────────────────────
    # Entirely opt-in: with STRIPE_SECRET_KEY unset, the billing routes return
    # 503 and nothing else in the app changes. Plans stay manually settable via
    # PUT /api/usage/plan, which is the pre-billing behaviour.
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""             # whsec_… — required to accept webhooks
    # Price ID per plan. These map both ways: checkout uses plan → price, the
    # webhook uses price → plan to decide what a subscription entitles.
    STRIPE_PRICE_PRO: str = ""
    STRIPE_PRICE_ENTERPRISE: str = ""
    # Where Stripe returns the user after hosted checkout / portal.
    STRIPE_SUCCESS_URL: str = ""
    STRIPE_CANCEL_URL: str = ""

    @property
    def billing_enabled(self) -> bool:
        return bool(self.STRIPE_SECRET_KEY)

    @property
    def stripe_price_by_plan(self) -> dict[str, str]:
        """Configured plan → price ID, skipping plans with no price set."""
        pairs = {"pro": self.STRIPE_PRICE_PRO, "enterprise": self.STRIPE_PRICE_ENTERPRISE}
        return {plan: price for plan, price in pairs.items() if price}

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
