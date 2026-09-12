"""Outbox publisher tests.

Unit tests use a fake broker client and assert the published-only-on-ack
contract: an event is marked ``published`` only after the client returns, a
failed publish increments ``attempts`` and schedules a bounded backoff, and
exhausted/not-yet-due events are left alone. One integration test under the
Step-4 gate publishes through the real ``goudan-rabbitmq`` broker.
"""

import json
import os

import pytest
from app.execution.celery_message import TaskMessage
from app.execution.outbox import SANDBOX_EXECUTE_TASK
from app.execution.publisher import OutboxPublisher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


class FakeRabbitMqClient:
    """A broker stub that records publishes and can be told to fail."""

    def __init__(self, *, fail_with: Exception | None = None) -> None:
        self.published: list[tuple[TaskMessage, str]] = []
        self._fail_with = fail_with

    async def publish(self, message: TaskMessage, routing_key: str) -> None:
        if self._fail_with is not None:
            raise self._fail_with
        self.published.append((message, routing_key))


def _publisher(engine, client, **overrides) -> OutboxPublisher:
    kwargs = dict(
        queue="sandbox.execute",
        max_attempts=5,
        backoff_base_seconds=10,
        backoff_cap_seconds=600,
    )
    kwargs.update(overrides)
    return OutboxPublisher(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        rabbitmq_client=client,
        **kwargs,
    )


async def test_publish_marks_published_only_after_ack(engine, service, user_context, approval, outbox):
    run = await service.confirm_execution(user_context, approval.id, "confirm-1")
    client = FakeRabbitMqClient()
    publisher = _publisher(engine, client)
    assert await publisher.publish_pending_outbox_events() == 1
    assert len(client.published) == 1
    assert await outbox.event_status(SANDBOX_EXECUTE_TASK, run.id) == "published"


async def test_failed_publish_retries_with_bounded_backoff(engine, db_session, service, user_context, approval, outbox):
    run = await service.confirm_execution(user_context, approval.id, "confirm-1")
    client = FakeRabbitMqClient(fail_with=RuntimeError("broker down"))
    publisher = _publisher(engine, client)
    assert await publisher.publish_pending_outbox_events() == 0
    assert client.published == []
    assert await outbox.event_status(SANDBOX_EXECUTE_TASK, run.id) == "pending"
    attempts = await db_session.scalar(
        text("select attempts from outbox_events where aggregate_id = :rid"), {"rid": run.id}
    )
    next_attempt_at = await db_session.scalar(
        text("select next_attempt_at from outbox_events where aggregate_id = :rid"), {"rid": run.id}
    )
    assert attempts == 1
    assert next_attempt_at is not None


async def test_publish_skips_exhausted_attempts(engine, db_session, service, user_context, approval):
    run = await service.confirm_execution(user_context, approval.id, "confirm-1")
    await db_session.execute(text("update outbox_events set attempts = 5 where aggregate_id = :rid"), {"rid": run.id})
    await db_session.commit()
    client = FakeRabbitMqClient()
    publisher = _publisher(engine, client, max_attempts=5)
    assert await publisher.publish_pending_outbox_events() == 0
    assert client.published == []


async def test_publish_skips_event_not_yet_due(engine, db_session, service, user_context, approval):
    run = await service.confirm_execution(user_context, approval.id, "confirm-1")
    await db_session.execute(
        text("update outbox_events set next_attempt_at = now() + interval '1 hour' where aggregate_id = :rid"),
        {"rid": run.id},
    )
    await db_session.commit()
    client = FakeRabbitMqClient()
    publisher = _publisher(engine, client)
    assert await publisher.publish_pending_outbox_events() == 0
    assert client.published == []


async def test_real_rabbitmq_publish_lands_in_sandbox_execute_queue(engine, service, user_context, approval):
    """Integration under the Step-4 gate: requires the goudan-rabbitmq broker."""
    import aio_pika
    from app.execution.publisher import AioPikaRabbitMqClient

    url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1:5672/")
    try:
        probe = await aio_pika.connect(url, timeout=3)
        await probe.close()
    except Exception:
        pytest.skip("RabbitMQ not reachable; integration test skipped")

    run = await service.confirm_execution(user_context, approval.id, "confirm-1")
    client = AioPikaRabbitMqClient(url, queue="sandbox.execute")
    publisher = _publisher(engine, client)
    assert await publisher.publish_pending_outbox_events() == 1

    connection = await aio_pika.connect(url)
    try:
        channel = await connection.channel()
        queue = await channel.declare_queue("sandbox.execute", durable=True)
        message = await queue.get(timeout=5, fail=False)
        assert message is not None, "expected a sandbox.execute message in the queue"
        # The Celery task-protocol-v2 message: JSON args body + task headers.
        args = json.loads(message.body)
        assert args[0] == [str(run.id)]
        assert message.headers["task"] == "sandbox.execute"
        await message.ack()
    finally:
        await connection.close()
