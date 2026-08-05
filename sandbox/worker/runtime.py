"""Runtime seam between the executor and an isolated sandbox.

Task 2 ships a stub in-process runtime that "executes" a claimed run's compiled
steps by recording each ``(sandbox_run_id, step_id)`` into an injectable audit
store. Task 3 replaces ``StubRuntime`` with the isolated Docker runtime (no DB
credentials, non-root, deny-by-default egress); the executor only depends on the
``Runtime`` protocol, so swapping is a one-line change in ``celery_app``.

The real runtime (``ContainerRuntime``) builds an OCI/Docker run config encoding
the security contract — non-root user, read-only rootfs, empty writable tmpfs
mount with a size cap, no host mounts, dropped capabilities, PID/memory/CPU/time
limits — behind an injectable ``ContainerRunner`` for deterministic tests.

Before execution the runtime verifies the re-derived plan hash against the
grant's ``plan_hash`` (T2 M2 fix) to refuse a drifted plan.

The grant token is passed via the AGENT_TOOL_GRANT_TOKEN env var, never on the
command line (Minor #6 — cmdline is readable via /proc/<pid>/cmdline).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol

from app.execution.grant import GrantSigner as AgentGrantSigner
from app.skills.schemas import ExecutionPlan

from worker.audit import MemoryAuditStore


class AuditStore(Protocol):
    def record(self, sandbox_run_id: str, step_id: str) -> None: ...


class ContainerRunner(Protocol):
    """Port for launching an isolated OCI container with a security-hardened config."""

    def run(self, config: SandboxContainerConfig) -> ContainerResult: ...


@dataclass(frozen=True)
class SandboxContainerConfig:
    """Security-hardened OCI/Docker run configuration.

    Encodes the mandatory security contract: non-root user, read-only rootfs,
    empty writable tmpfs mount with a size cap, no host mounts, dropped
    capabilities, and PID/memory/CPU/time limits.
    """

    image: str
    command: list[str]
    grant_token: str
    user: str = "1000:1000"
    read_only_rootfs: bool = True
    tmpfs_mount: str = "/tmp:rw,noexec,nosuid,size=64m"
    host_mounts: list[str] = field(default_factory=list)
    cap_drop: list[str] = field(default_factory=lambda: ["ALL"])
    pids_limit: int = 64
    memory_limit: str = "256m"
    cpu_limit: float = 1.0
    timeout_seconds: int = 300
    network_enabled: bool = True
    environment: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ContainerResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ExecutionResult:
    run_id: str
    status: str


class Runtime(Protocol):
    """The sandbox execution seam: takes a plan and producing terminal status."""

    def execute(self, plan: ExecutionPlan, sandbox_run_id: str, user_id: str) -> None: ...


class StubRuntime:
    """In-process runtime seam: records each compiled step into the audit store."""

    def __init__(self, audit: MemoryAuditStore | None = None) -> None:
        self._audit = audit or MemoryAuditStore()

    def execute(self, plan: ExecutionPlan, sandbox_run_id: str, user_id: str = "") -> None:
        for step in plan.steps:
            self._audit.record(sandbox_run_id, step.id)


class ContainerRuntime:
    """Real isolated runtime: builds a hardened OCI config and delegates to a runner.

    Uses the agent-service GrantSigner directly (shared type, no local protocol
    divergence — I4). The grant token is placed in the AGENT_TOOL_GRANT_TOKEN
    env var, never on the command line (Minor #6).
    """

    def __init__(
        self,
        runner: ContainerRunner,
        grant_signer: AgentGrantSigner,
        broker_url: str,
        image: str = "goudan-sandbox-runtime:latest",
    ) -> None:
        self._runner = runner
        self._signer = grant_signer
        self._broker_url = broker_url
        self._image = image

    def execute(self, plan: ExecutionPlan, sandbox_run_id: str, user_id: str) -> None:
        # T2 M2 fix: verify the re-derived plan hash before launching the container.
        rederived = _rehash_plan(plan)
        if rederived != plan.hash:
            raise RuntimeError(
                f"Plan hash mismatch: stored {plan.hash!r} != re-derived {rederived!r}"
            )

        step_ids = [step.id for step in plan.steps]
        grant_token = self._signer.sign(
            run_id=sandbox_run_id,
            user_id=user_id,
            plan_hash=plan.hash,
            step_ids=step_ids,
        )

        config = SandboxContainerConfig(
            image=self._image,
            command=["/bin/goudan-runtime"],
            grant_token=grant_token,
            environment={
                "AGENT_TOOL_BROKER_URL": self._broker_url,
                "AGENT_TOOL_GRANT_TOKEN": grant_token,
            },
        )
        self._runner.run(config)


def _rehash_plan(plan: ExecutionPlan) -> str:
    """Re-derive the plan hash from its compiled steps and version id."""
    payload = {
        "skill_version_id": str(plan.skill_version_id),
        "steps": [step.model_dump(mode="json") for step in plan.steps],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
