"""Runtime isolation tests: the container config and lease/recovery behavior.

The real runtime (ContainerRuntime) builds an OCI run config behind an injectable
``ContainerRunner`` so tests are deterministic — a fake runner captures the config
and asserts the security contract. Lease re-claim (I1) and hash-drift refusal
(T2 M2) are also exercised against the real WorkerRepository.

Grant token is in the AGENT_TOOL_GRANT_TOKEN env var, never on the command line
(Minor #6).
"""

import uuid

import pytest
from worker.runtime import (
    ContainerResult,
    ContainerRuntime,
    SandboxContainerConfig,
    _rehash_plan,
)


class FakeContainerRunner:
    """Captures the run config for assertion instead of launching a real container."""

    def __init__(self, exit_code: int = 0):
        self.exit_code = exit_code
        self.configs: list[SandboxContainerConfig] = []

    def run(self, config: SandboxContainerConfig) -> ContainerResult:
        self.configs.append(config)
        return ContainerResult(exit_code=self.exit_code, stdout="ok", stderr="")


class FakeGrantSigner:
    """Returns a pre-built grant token string (matches agent-service GrantSigner.sign return type)."""

    def __init__(self, token: str = "fake-grant-token"):
        self._token = token
        self.last_call: dict | None = None

    def sign(
        self, *, run_id: str, user_id: str, plan_hash: str, step_ids: list[str], ttl_seconds: int = 600
    ) -> str:
        self.last_call = {
            "run_id": run_id, "user_id": user_id, "plan_hash": plan_hash,
            "step_ids": step_ids, "ttl_seconds": ttl_seconds,
        }
        return self._token


def _make_plan(steps=None):
    from app.skills.schemas import CompiledStep, ExecutionPlan
    if steps is None:
        steps = [CompiledStep(id="s1", tool="document.read",
                              input={"document_id": str(uuid.uuid4())})]
    plan = ExecutionPlan(skill_version_id=uuid.uuid4(), hash="", steps=steps)
    plan_hash = _rehash_plan(plan)
    return ExecutionPlan(skill_version_id=plan.skill_version_id, hash=plan_hash, steps=plan.steps)


# --- Container config tests (fake runner) ---


def test_container_config_non_root_user():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    plan = _make_plan()
    runtime.execute(plan, str(uuid.uuid4()), str(uuid.uuid4()))
    assert len(runner.configs) == 1
    config = runner.configs[0]
    assert config.user == "1000:1000"


def test_container_config_read_only_rootfs():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    config = runner.configs[0]
    assert config.read_only_rootfs is True


def test_container_config_tmpfs_mount_with_size_cap():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    config = runner.configs[0]
    assert "size=" in config.tmpfs_mount


def test_container_config_no_host_mounts():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    config = runner.configs[0]
    assert config.host_mounts == []


def test_container_config_capabilities_dropped():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    config = runner.configs[0]
    assert "ALL" in config.cap_drop


def test_container_config_limits_set():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    config = runner.configs[0]
    assert config.pids_limit == 64
    assert config.memory_limit == "256m"
    assert config.cpu_limit == 1.0
    assert config.timeout_seconds == 300


def test_grant_token_in_env_not_cmdline():
    """Minor #6: grant token must be in the environment, not on the command line."""
    runner = FakeContainerRunner()
    signer = FakeGrantSigner(token="secret-grant-123")
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    config = runner.configs[0]
    # Token must NOT appear on the command line.
    for arg in config.command:
        assert "secret-grant-123" not in arg
    # Token must be in the environment.
    assert config.environment.get("AGENT_TOOL_GRANT_TOKEN") == "secret-grant-123"


def test_hash_drift_refuses_execution():
    """T2 M2 fix: a plan whose hash doesn't match its re-derived hash is refused."""
    from app.skills.schemas import CompiledStep, ExecutionPlan

    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    plan = ExecutionPlan(
        skill_version_id=uuid.uuid4(),
        hash="deadbeef-not-matching",
        steps=[CompiledStep(id="s1", tool="document.read",
                            input={"document_id": str(uuid.uuid4())})],
    )
    with pytest.raises(RuntimeError, match="Plan hash mismatch"):
        runtime.execute(plan, str(uuid.uuid4()), str(uuid.uuid4()))


# --- Lease re-claim tests (I1) ---


def test_stale_running_row_is_claimable(worker_repository, seeded_run):
    """A run claimed more than LEASE_TTL ago is re-claimable by a new worker."""
    first = worker_repository.claim(seeded_run.id)
    assert first is not None

    from datetime import UTC, datetime, timedelta

    from worker.repository import LEASE_TTL
    stale_time = datetime.now(UTC) - LEASE_TTL - timedelta(seconds=10)
    import os

    from sqlalchemy import create_engine, text
    from worker.config import sync_database_url

    db_url = os.getenv("TEST_DATABASE_URL",
                       "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres")
    engine = create_engine(sync_database_url(db_url))
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE sandbox_runs SET claimed_at = :ts WHERE id = :id"),
            {"ts": stale_time, "id": seeded_run.id},
        )

    second = worker_repository.claim(seeded_run.id)
    assert second is not None


def test_fresh_running_row_is_not_claimable(worker_repository, seeded_run):
    """A freshly claimed run cannot be claimed again by a concurrent worker."""
    first = worker_repository.claim(seeded_run.id)
    assert first is not None
    second = worker_repository.claim(seeded_run.id)
    assert second is None
