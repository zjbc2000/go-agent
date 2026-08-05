"""Sandbox worker configuration loaded from the environment.

The worker is the trusted orchestrator: it connects with a service-role Postgres
session (``DATABASE_URL``) and consumes the RabbitMQ broker (``RABBITMQ_URL``).
It reuses the agent-service envelope cipher key (``AGENT_CRYPTO_KEY``) so the
worker can decrypt a run's document body and inputs to re-derive its plan.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from app.core.config import DEV_CRYPTO_KEY, DEV_DATABASE_URL

DEV_RABBITMQ_URL = "amqp://guest:guest@127.0.0.1:5672/"


@dataclass(frozen=True)
class WorkerConfig:
    database_url: str
    rabbitmq_url: str
    crypto_key_b64: str
    outbox_queue: str = "sandbox.execute"

    @classmethod
    def from_env(cls) -> WorkerConfig:
        return cls(
            database_url=os.getenv("DATABASE_URL", DEV_DATABASE_URL),
            rabbitmq_url=os.getenv("RABBITMQ_URL", DEV_RABBITMQ_URL),
            crypto_key_b64=os.getenv("AGENT_CRYPTO_KEY", DEV_CRYPTO_KEY),
            outbox_queue=os.getenv("OUTBOX_QUEUE", "sandbox.execute"),
        )


def sync_database_url(url: str) -> str:
    """Normalize an asyncpg URL to the psycopg sync dialect the worker uses."""
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql+asyncpg://")
    return url
