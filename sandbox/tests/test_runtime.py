"""Runtime isolation tests: the container config and lease/recovery behavior.

The real runtime (ContainerRuntime) builds an OCI run config behind an injectable
``ContainerRunner`` so tests are deterministic — a fake runner captures the config
and asserts the security contract. Lease re-claim (I1) and hash-drift refusal
(T2 M2) are also exercised against the real WorkerRepository.
"""

import uuid

import pytest
from worker.runtime import (
    ContainerResult,
    ContainerRuntime,
    SandboxContainerConfig,
    ToolGrant,
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
    """Returns a pre-built grant token without real crypto."""

    def __init__(self, token: str = "fake-grant-token"):
        self._token = token

    def sign(self, *, run_id: str, user_id: str, plan_hash: str, step_ids: list[str]) -> ToolGrant:
        return ToolGrant(
            token=self._token,
            run_id=run_id,
            user_id=user_id,
            plan_hash=plan_hash,
            expires_at="2099-01-01T00:00:00Z",
            step_ids=step_ids,
        )


# --- Container config tests (fake runner) ---


def test_container_config_non_root_user():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    from app.skills.schemas import CompiledStep, ExecutionPlan

    steps = [CompiledStep(id="s1", tool="document.read", input={"document_id": str(uuid.uuid4())})]
    plan = ExecutionPlan(skill_version_id=uuid.uuid4(), hash="", steps=steps)
    plan_hash = _rehash_plan(plan)
    plan = ExecutionPlan(skill_version_id=plan.skill_version_id, hash=plan_hash, steps=plan.steps)

    runtime.execute(plan, str(uuid.uuid4()))
    assert len(runner.configs) == 1
    config = runner.configs[0]
    assert config.user == "1000:1000"


def test_container_config_read_only_rootfs():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    from app.skills.schemas import CompiledStep, ExecutionPlan

    plan = ExecutionPlan(
        skill_version_id=uuid.uuid4(),
        hash="abc",
        steps=[CompiledStep(id="s1", tool="document.read", input={"document_id": str(uuid.uuid4())})],
    )
    plan_hash = _rehash_plan(plan)
    plan = ExecutionPlan(skill_version_id=plan.skill_version_id, hash=plan_hash, steps=plan.steps)

    runtime.execute(plan, str(uuid.uuid4()))
    config = runner.configs[0]
    assert config.read_only_rootfs is True


def test_container_config_tmpfs_mount_with_size_cap():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    from app.skills.schemas import CompiledStep, ExecutionPlan

    plan = ExecutionPlan(
        skill_version_id=uuid.uuid4(),
        hash="abc",
        steps=[CompiledStep(id="s1", tool="document.read", input={"document_id": str(uuid.uuid4())})],
    )
    plan_hash = _rehash_plan(plan)
    plan = ExecutionPlan(skill_version_id=plan.skill_version_id, hash=plan_hash, steps=plan.steps)

    runtime.execute(plan, str(uuid.uuid4()))
    config = runner.configs[0]
    assert "size=" in config.tmpfs_mount


def test_container_config_no_host_mounts():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    from app.skills.schemas import CompiledStep, ExecutionPlan

    plan = ExecutionPlan(
        skill_version_id=uuid.uuid4(),
        hash="abc",
        steps=[CompiledStep(id="s1", tool="document.read", input={"document_id": str(uuid.uuid4())})],
    )
    plan_hash = _rehash_plan(plan)
    plan = ExecutionPlan(skill_version_id=plan.skill_version_id, hash=plan_hash, steps=plan.steps)

    runtime.execute(plan, str(uuid.uuid4()))
    config = runner.configs[0]
    assert config.host_mounts == []


def test_container_config_capabilities_dropped():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    from app.skills.schemas import CompiledStep, ExecutionPlan

    plan = ExecutionPlan(
        skill_version_id=uuid.uuid4(),
        hash="abc",
        steps=[CompiledStep(id="s1", tool="document.read", input={"document_id": str(uuid.uuid4())})],
    )
    plan_hash = _rehash_plan(plan)
    plan = ExecutionPlan(skill_version_id=plan.skill_version_id, hash=plan_hash, steps=plan.steps)

    runtime.execute(plan, str(uuid.uuid4()))
    config = runner.configs[0]
    assert "ALL" in config.cap_drop


def test_container_config_limits_set():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    from app.skills.schemas import CompiledStep, ExecutionPlan

    plan = ExecutionPlan(
        skill_version_id=uuid.uuid4(),
        hash="abc",
        steps=[CompiledStep(id="s1", tool="document.read", input={"document_id": str(uuid.uuid4())})],
    )
    plan_hash = _rehash_plan(plan)
    plan = ExecutionPlan(skill_version_id=plan.skill_version_id, hash=plan_hash, steps=plan.steps)

    runtime.execute(plan, str(uuid.uuid4()))
    config = runner.configs[0]
    assert config.pids_limit == 64
    assert config.memory_limit == "256m"
    assert config.cpu_limit == 1.0
    assert config.timeout_seconds == 300


def test_hash_drift_refuses_execution():
    """T2 M2 fix: a plan whose hash doesn't match its re-derived hash is refused."""
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer, broker_url="http://broker:8000")
    from app.skills.schemas import CompiledStep, ExecutionPlan

    # Create a plan with a deliberately wrong hash.
    plan = ExecutionPlan(
        skill_version_id=uuid.uuid4(),
        hash="deadbeef-not-matching",
        steps=[CompiledStep(id="s1", tool="document.read", input={"document_id": str(uuid.uuid4())})],
    )
    with pytest.raises(RuntimeError, match="Plan hash mismatch"):
        runtime.execute(plan, str(uuid.uuid4()))


# --- Lease re-claim tests (I1) ---


def test_stale_running_row_is_claimable(worker_repository, seeded_run):
    """A run claimed more than LEASE_TTL ago is re-claimable by a new worker."""
    # First claim succeeds.
    first = worker_repository.claim(seeded_run.id)
    assert first is not None

    # Manually make the claimed_at stale (older than the 5-minute TTL).
    from datetime import UTC, datetime, timedelta

    from worker.repository import LEASE_TTL
    stale_time = datetime.now(UTC) - LEASE_TTL - timedelta(seconds=10)
    import os

    from sqlalchemy import create_engine, text
    from worker.config import sync_database_url

    db_url = os.getenv("TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres")
    engine = create_engine(sync_database_url(db_url))
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE sandbox_runs SET claimed_at = :ts WHERE id = :id"),
            {"ts": stale_time, "id": seeded_run.id},
        )

    # A second claim should succeed because the row is stale.
    second = worker_repository.claim(seeded_run.id)
    assert second is not None


def test_fresh_running_row_is_not_claimable(worker_repository, seeded_run):
    """A freshly claimed run cannot be claimed again by a concurrent worker."""
    first = worker_repository.claim(seeded_run.id)
    assert first is not None
    # Second claim must fail (the row was just claimed).
    second = worker_repository.claim(seeded_run.id)
    assert second is None
