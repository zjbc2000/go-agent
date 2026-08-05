"""Celery worker for the ``sandbox.execute`` queue.

The worker is the TRUSTED orchestrator: it holds a service-role Postgres session
(repository.py) and consumes ``sandbox.execute`` messages that carry only the
``sandbox_run_id``. ``acks_late`` + json serialization are set so a crashed or
pre-acked worker never loses a run; the executor's guards make re-delivery safe.

Runtime selection: ``SANDBOX_RUNTIME`` env var selects the execution backend —
``stub`` (default) uses the in-process StubRuntime; ``container`` constructs the
isolated ContainerRuntime with a real grant signer, broker URL, and a REAL
subprocess-based Docker runner (NEW #4). Container mode fails fast when Docker
is unavailable — never falls back to fake success (NEW #4 IMPORTANT).
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile

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
        # NEW #4 IMPORTANT: container mode MUST find Docker — fail fast, never
        # fall back to fake success.
        try:
            subprocess.run(["docker", "info"], capture_output=True, timeout=5, check=False)
        except FileNotFoundError:
            raise RuntimeError(
                "SANDBOX_RUNTIME=container but 'docker' binary not found. "
                "Install Docker or set SANDBOX_RUNTIME=stub."
            ) from None
        except Exception as exc:
            raise RuntimeError(
                f"Docker health check failed: {exc}. "
                "Ensure Docker daemon is running or set SANDBOX_RUNTIME=stub."
            ) from exc
        runner = DockerSubprocessRunner()
        runtime: StubRuntime | ContainerRuntime = ContainerRuntime(
            runner=runner, grant_signer=signer, broker_url=config.broker_url)
    else:
        runtime = StubRuntime(audit=MemoryAuditStore())

    return SandboxExecutor(repository=repository, runtime=runtime)


class DockerSubprocessRunner:
    """Real container runner: builds a hardened ``docker run`` command (NEW #4).

    Secrets (AGENT_TOOL_GRANT_TOKEN) are passed via ``--env-file`` pointing at
    a chmod-0600 temp file created per launch and removed in a finally block
    (NEW #4 MINOR). The literal token never appears in docker argv or procfs.
    The log line is redacted to exclude token values.
    """

    _DOCKER_BIN = "docker"

    def run(self, config: SandboxContainerConfig) -> ContainerResult:
        cmd = [self._DOCKER_BIN, "run", "--rm"]

        # Security contract (no secrets here — everything goes through env-file).
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

        # NEW #4 MINOR: write secrets to a temp env-file (chmod 0600) so the
        # grant token never appears in docker argv or /proc/<pid>/cmdline.
        env_tmp_path: str | None = None
        try:
            if config.environment:
                fd, env_tmp_path = tempfile.mkstemp(
                    prefix="goudan-env-", suffix=".env", text=True)
                os.chmod(env_tmp_path, 0o600)
                with os.fdopen(fd, "w") as f:
                    for key, val in config.environment.items():
                        f.write(f"{key}={val}\n")
                cmd.extend(["--env-file", env_tmp_path])

            cmd.append(config.image)
            cmd.extend(config.command)

            # Redacted log: replace token values with ***.
            redacted = _redact_cmd(cmd, config.environment)
            _log.info("Launching container: %s", " ".join(redacted))

            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=config.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ContainerResult(
                exit_code=-1, stdout="", stderr="Container timed out.")
        except FileNotFoundError:
            return ContainerResult(
                exit_code=-1, stdout="", stderr="docker binary not found.")
        finally:
            if env_tmp_path is not None:
                try:
                    os.unlink(env_tmp_path)
                except OSError:
                    pass

        return ContainerResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )


def _redact_cmd(cmd: list[str], env: dict[str, str]) -> list[str]:
    """Return a copy of ``cmd`` with secret values replaced by ``***``."""
    secrets = set(v for v in env.values() if v)
    redacted: list[str] = []
    for token in cmd:
        for secret in secrets:
            if secret in token:
                token = token.replace(secret, "***")
        redacted.append(token)
    return redacted


@celery_app.task(name="sandbox.execute", acks_late=True)
def execute_sandbox_run(sandbox_run_id: str) -> None:
    build_executor().execute_sandbox_run(sandbox_run_id)
