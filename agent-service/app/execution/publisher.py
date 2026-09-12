"""Outbox publisher: drains pending events to RabbitMQ with broker confirms.

The publisher runs as the SYSTEM (service_session), not as a user: it reads the
pending outbox rows for every user and marks them published. An event is marked
``published`` ONLY after the broker acknowledges the durable publish; a failed or
dropped publish increments ``attempts`` and schedules a bounded-backoff retry, so
nothing is lost and nothing is double-marked. The FastAPI lifespan background task
(main.py) polls ``publish_pending_outbox_events`` on an interval.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import aio_pika
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.execution.celery_message import TaskMessage, build_sandbox_execute_message
from app.execution.outbox import OutboxRepository

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RabbitMqClient(Protocol):
    """Port for confirmed, durable publishes to the sandbox.execute queue."""

    async def publish(self, message: TaskMessage, routing_key: str) -> None: ...


class AioPikaRabbitMqClient:
    """aio-pika client: durable queue, persistent messages, broker confirms."""

    def __init__(self, url: str, *, queue: str) -> None:
        self._url = url
        self._queue = queue

    async def publish(self, message: TaskMessage, routing_key: str) -> None:
        connection = await aio_pika.connect(self._url)
        try:
            channel = await connection.channel()
            # Publisher confirms: publish() below returns only after the broker
            # acknowledges, and raises on a nack or dropped connection.
            channel.publisher_confirms = True
            await channel.declare_queue(self._queue, durable=True)
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=message.body,
                    content_type=message.content_type,
                    content_encoding=message.content_encoding,
                    headers=message.headers,
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    correlation_id=message.properties.get("correlation_id"),
                ),
                routing_key=self._queue,
            )
        finally:
            await connection.close()


class OutboxPublisher:
    """Drains pending outbox events and publishes them to the sandbox queue."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        rabbitmq_client: RabbitMqClient,
        *,
        queue: str,
        max_attempts: int,
        backoff_base_seconds: int,
        backoff_cap_seconds: int,
    ) -> None:
        self._outbox = OutboxRepository(session_factory)
        self._rabbitmq_client = rabbitmq_client
        self._queue = queue
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base_seconds
        self._backoff_cap = backoff_cap_seconds

    async def publish_pending_outbox_events(self) -> int:
        """Publish every eligible pending event; return how many were published."""
        now = _utcnow()
        events: list[dict[str, Any]] = await self._outbox.list_pending(max_attempts=self._max_attempts, now=now)
        published = 0
        for event in events:
            run_id = event["sandbox_run_id"]
            if not run_id:
                logger.warning("Outbox event %s has no sandbox_run_id; skipped", event["id"])
                continue
            message = build_sandbox_execute_message(str(run_id))
            try:
                await self._rabbitmq_client.publish(message, routing_key=self._queue)
            except Exception:
                logger.warning("Publishing outbox event %s failed", event["id"], exc_info=True)
                await self._outbox.record_failure(
                    event["id"],
                    attempts=event["attempts"] + 1,
                    next_attempt_at=self._backoff_at(event["attempts"] + 1, now),
                )
            else:
                await self._outbox.mark_published(event["id"], published_at=now)
                published += 1
        return published

    def _backoff_at(self, attempts: int, now: datetime) -> datetime:
        """Bounded exponential backoff for the next attempt."""
        delay = min(self._backoff_cap, self._backoff_base * (2 ** (attempts - 1)))
        return now + timedelta(seconds=delay)
