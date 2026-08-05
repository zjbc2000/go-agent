"""Celery worker for the ``sandbox.execute`` queue.

The worker is the TRUSTED orchestrator: it holds a service-role Postgres session
(repository.py) and consumes ``sandbox.execute`` messages that carry only the
``sandbox_run_id``. ``acks_late`` + json serialization are set so a crashed or
pre-acked worker never loses a run; the executor's guards make re-delivery safe.
"""

from __future__ import annotations

import os

from app.core.crypto import LocalEnvelopeCipher
from celery import Celery
from kombu import Queue

from worker.audit import MemoryAuditStore
from worker.config import WorkerConfig
from worker.repository import WorkerRepository
from worker.runtime import StubRuntime
from worker.tasks import SandboxExecutor

celery_app = Celery("goudan-sandbox")
celery_app.conf.broker_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1:5672/")
# The worker consumes ONLY the dedicated sandbox.execute queue (durable), and the
# task routes there so the publisher's default-exchange message is picked up.
celery_app.conf.task_queues = (Queue("sandbox.execute", durable=True),)
celery_app.conf.task_routes = {"sandbox.execute": {"queue": "sandbox.execute"}}
celery_app.conf.task_default_queue = "sandbox.execute"
celery_app.conf.task_acks_late = True
celery_app.conf.task_serializer = "json"
celery_app.conf.result_serializer = "json"
celery_app.conf.accept_content = ["json"]
celery_app.conf.broker_connection_retry_on_startup = True


def build_executor() -> SandboxExecutor:
    """Construct the executor from the environment (service-role worker config)."""
    config = WorkerConfig.from_env()
    cipher = LocalEnvelopeCipher.from_base64_key(config.crypto_key_b64)
    repository = WorkerRepository(config.database_url, cipher=cipher)
    return SandboxExecutor(repository=repository, runtime=StubRuntime(audit=MemoryAuditStore()))


@celery_app.task(name="sandbox.execute", acks_late=True)
def execute_sandbox_run(sandbox_run_id: str) -> None:
    build_executor().execute_sandbox_run(sandbox_run_id)
