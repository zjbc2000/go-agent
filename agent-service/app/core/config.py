"""Runtime settings loaded from the environment."""

import base64
import os
from dataclasses import dataclass

# DEV-ONLY default: a well-known all-zero key used only when AGENT_CRYPTO_KEY is
# unset during local development and tests. Production must set AGENT_CRYPTO_KEY
# from the cloud KMS; this value must never be deployed.
DEV_CRYPTO_KEY = base64.urlsafe_b64encode(b"0" * 32).decode()

# DEV-ONLY default: the shared token the BFF uses to call internal routes. Production
# must set AGENT_INTERNAL_TOKEN; the app refuses to start for internal routing if the
# env token is unset AND this dev token is still in use.
DEV_INTERNAL_TOKEN = "dev-internal-token"

# DEV-ONLY default: the local Supabase Postgres used by `supabase db reset`.
DEV_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres"

# DEV-ONLY default: the local RabbitMQ. The `goudan-rabbitmq` container serves
# 5672/15672 on this machine; production must set RABBITMQ_URL explicitly.
DEV_RABBITMQ_URL = "amqp://guest:guest@127.0.0.1:5672/"

# DEV-ONLY default: the shared secret for HMAC-signed sandbox tool grants.
# Production must set AGENT_TOOL_GRANT_SECRET; this value must never be deployed.
DEV_TOOL_GRANT_SECRET = "dev-tool-grant-secret"


@dataclass(frozen=True)
class Settings:
    app_name: str = "Goudan Agent API"
    version: str = "1.0.0"
    crypto_key_b64: str = DEV_CRYPTO_KEY
    database_url: str = DEV_DATABASE_URL
    internal_token: str = DEV_INTERNAL_TOKEN
    provider_base_url: str | None = None
    provider_api_key: str | None = None
    provider_model: str = "gpt-4o-mini"
    stream_event_retention_seconds: int = 7 * 24 * 60 * 60
    # Transactional outbox publisher (Task 2).
    rabbitmq_url: str = DEV_RABBITMQ_URL
    outbox_queue: str = "sandbox.execute"
    outbox_poll_interval_seconds: int = 5
    outbox_max_attempts: int = 10
    outbox_backoff_base_seconds: int = 10
    outbox_backoff_cap_seconds: int = 600
    outbox_poller_enabled: bool = True
    # Sandbox tool-grant shared secret (Task 3).
    tool_grant_secret: str = DEV_TOOL_GRANT_SECRET

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            crypto_key_b64=os.getenv("AGENT_CRYPTO_KEY", DEV_CRYPTO_KEY),
            database_url=os.getenv("DATABASE_URL", DEV_DATABASE_URL),
            internal_token=os.getenv("AGENT_INTERNAL_TOKEN", DEV_INTERNAL_TOKEN),
            provider_base_url=os.getenv("AGENT_PROVIDER_BASE_URL") or None,
            provider_api_key=os.getenv("AGENT_PROVIDER_API_KEY") or None,
            provider_model=os.getenv("AGENT_PROVIDER_MODEL", "gpt-4o-mini"),
            stream_event_retention_seconds=int(
                os.getenv("AGENT_STREAM_EVENT_RETENTION_SECONDS", str(7 * 24 * 60 * 60))
            ),
            rabbitmq_url=os.getenv("RABBITMQ_URL", DEV_RABBITMQ_URL),
            outbox_queue=os.getenv("OUTBOX_QUEUE", "sandbox.execute"),
            outbox_poll_interval_seconds=int(os.getenv("OUTBOX_POLL_INTERVAL_SECONDS", "5")),
            outbox_max_attempts=int(os.getenv("OUTBOX_MAX_ATTEMPTS", "10")),
            outbox_backoff_base_seconds=int(os.getenv("OUTBOX_BACKOFF_BASE_SECONDS", "10")),
            outbox_backoff_cap_seconds=int(os.getenv("OUTBOX_BACKOFF_CAP_SECONDS", "600")),
            outbox_poller_enabled=os.getenv("OUTBOX_POLLER_ENABLED", "true").lower() != "false",
            tool_grant_secret=os.getenv("AGENT_TOOL_GRANT_SECRET", DEV_TOOL_GRANT_SECRET),
        )
