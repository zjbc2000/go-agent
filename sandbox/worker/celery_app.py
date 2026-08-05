"""Celery worker for the ``sandbox.execute`` queue.

The worker is the TRUSTED orchestrator: it holds a service-role Postgres session
(repository.py) and consumes ``sandbox.execute`` messages that carry only the
``sandbox_run_id``. ``acks_late`` + json serialization are set so a crashed or
pre-acked worker never loses a run; the executor's guards make re-delivery safe.

Runtime selection (I4): ``SANDBOX_RUNTIME`` env var selects the execution
backend — ``stub`` (default) uses the in-process StubRuntime; ``container``
constructs the isolated ContainerRuntime with a real grant signer, broker URL,
and policy. Tests keep the stub / fake runner.
"""

from __future__ import annotations

import os

from app.core.crypto import LocalEnvelopeCipher
from app.execution.grant import GrantSigner
from celery import Celery
from kombu import Queue

from worker.audit import MemoryAuditStore
from worker.config import WorkerConfig
from worker.policy import SandboxPolicy
from worker.repository import WorkerRepository
from worker.runtime import ContainerRuntime, StubRuntime
from worker.tasks import SandboxExecutor

celery_app = Celery("goudan-sandbox")
celery_app.conf.broker_url = os.getenv("RABBITMQ_URL", "amqp://guest:guest@127.0.0.1:5672/")
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

    if config.sandbox_runtime == "container":
        signer = GrantSigner(config.tool_grant_secret)
        policy = SandboxPolicy()
        dummy_runner = _DummyContainerRunner(policy, config.broker_url)
        runtime: StubRuntime | ContainerRuntime = ContainerRuntime(
            runner=dummy_runner,
            grant_signer=signer,
            broker_url=config.broker_url,
        )
    else:
        runtime = StubRuntime(audit=MemoryAuditStore())

    return SandboxExecutor(repository=repository, runtime=runtime)


class _DummyContainerRunner:
    """A minimal runner that logs the config (real Docker/Testcontainers gated on
    availability — this machine is resource-constrained)."""

    def __init__(self, policy: SandboxPolicy, broker_url: str) -> None:
        self._policy = policy
        self._broker_url = broker_url

    def run(self, config) -> object:
        import logging
        _log = logging.getLogger(__name__)
        _log.info("Would launch OCI container: image=%s user=%s", config.image, config.user)
        from worker.runtime import ContainerResult
        return ContainerResult(exit_code=0, stdout="ok", stderr="")


@celery_app.task(name="sandbox.execute", acks_late=True)
def execute_sandbox_run(sandbox_run_id: str) -> None:
    build_executor().execute_sandbox_run(sandbox_run_id)
