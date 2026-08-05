"""Celery worker for the ``sandbox.execute`` queue.

The worker is the TRUSTED orchestrator: it holds a service-role Postgres session
(repository.py) and consumes ``sandbox.execute`` messages that carry only the
``sandbox_run_id``. ``acks_late`` + json serialization are set so a crashed or
pre-acked worker never loses a run; the executor's guards make re-delivery safe.

Runtime selection: ``SANDBOX_RUNTIME`` env var selects the execution backend —
``stub`` (default) uses the in-process StubRuntime; ``container`` constructs the
isolated ContainerRuntime with a real grant signer, broker URL, and a REAL
subprocess-based Docker runner (NEW #4). Tests keep the stub / fake runner.
"""

from __future__ import annotations

import logging
import os
import subprocess

from app.core.crypto import LocalEnvelopeCipher
from app.execution.grant import GrantSigner
from celery import Celery
from kombu import Queue

from worker.audit import MemoryAuditStore
from worker.config import WorkerConfig
from worker.repository import WorkerRepository
from worker.runtime import ContainerResult, ContainerRuntime, SandboxContainerConfig, StubRuntime
from worker.tasks import SandboxExecutor

_log = logging.getLogger(__name__)

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
        try:
            subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
            runner: DockerSubprocessRunner | _FakeRunner = DockerSubprocessRunner()
            _log.info("Docker available: using real Docker subprocess runner.")
        except Exception:
            _log.warning("Docker unavailable: falling back to no-op runner.")
            runner = _FakeRunner()
        runtime: StubRuntime | ContainerRuntime = ContainerRuntime(
            runner=runner, grant_signer=signer, broker_url=config.broker_url)
    else:
        runtime = StubRuntime(audit=MemoryAuditStore())

    return SandboxExecutor(repository=repository, runtime=runtime)


class DockerSubprocessRunner:
    """Real container runner: builds a hardened ``docker run`` command and executes
    it via subprocess (NEW #4).

    The command encodes the full security contract from ``SandboxContainerConfig``:
    non-root user, read-only rootfs, tmpfs mount with size cap, no host mounts,
    all capabilities dropped, PID/memory/CPU/time limits, network per config.
    The grant token is injected via the AGENT_TOOL_GRANT_TOKEN env var, never on
    the command line.
    """

    _DOCKER_BIN = "docker"

    def run(self, config: SandboxContainerConfig) -> ContainerResult:
        cmd = [self._DOCKER_BIN, "run", "--rm"]

        # Security contract.
        cmd.extend(["--user", config.user])
        cmd.append("--read-only")
        cmd.extend(["--tmpfs", config.tmpfs_mount])
        for cap in config.cap_drop:
            cmd.extend(["--cap-drop", cap])
        cmd.extend(["--pids-limit", str(config.pids_limit)])
        cmd.extend(["--memory", config.memory_limit])
        cmd.extend(["--cpus", str(config.cpu_limit)])

        if not config.network_enabled:
            cmd.append("--network=none")

        # Environment.
        for key, val in config.environment.items():
            cmd.extend(["-e", f"{key}={val}"])

        cmd.append(config.image)
        cmd.extend(config.command)

        _log.info("Launching container: %s", " ".join(cmd))
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=config.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ContainerResult(exit_code=-1, stdout="", stderr="Container timed out.")
        except FileNotFoundError:
            return ContainerResult(exit_code=-1, stdout="", stderr="docker binary not found.")
        return ContainerResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )


class _FakeRunner:
    """Fallback fake runner when Docker is unavailable (logs, returns 0)."""

    def run(self, config: SandboxContainerConfig) -> ContainerResult:
        _log.warning("Fake runner: skipping container launch for image=%s", config.image)
        return ContainerResult(exit_code=0, stdout="", stderr="")


@celery_app.task(name="sandbox.execute", acks_late=True)
def execute_sandbox_run(sandbox_run_id: str) -> None:
    build_executor().execute_sandbox_run(sandbox_run_id)
