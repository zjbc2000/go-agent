"""Runtime isolation tests: the container config and lease/recovery behavior.
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
    """Returns a pre-built grant token string."""

    def __init__(self, token: str = "fake-grant-token"):
        self._token = token
        self.last_call: dict | None = None

    def sign(
        self, *, run_id: str, user_id: str, plan_hash: str,
        step_ids: list[str], ttl_seconds: int = 600,
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
    return ExecutionPlan(skill_version_id=plan.skill_version_id, hash=plan_hash,
                         steps=plan.steps)


# --- Container config tests ---


def test_container_config_non_root_user():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer,
                                broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    config = runner.configs[0]
    assert config.user == "1000:1000"


def test_container_config_read_only_rootfs():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer,
                                broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    assert runner.configs[0].read_only_rootfs is True


def test_container_config_tmpfs_mount_with_size_cap():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer,
                                broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    assert "size=" in runner.configs[0].tmpfs_mount


def test_container_config_no_host_mounts():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer,
                                broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    assert runner.configs[0].host_mounts == []


def test_container_config_capabilities_dropped():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer,
                                broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    assert "ALL" in runner.configs[0].cap_drop


def test_container_config_limits_set():
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer,
                                broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    config = runner.configs[0]
    assert config.pids_limit == 64
    assert config.memory_limit == "256m"
    assert config.cpu_limit == 1.0
    assert config.timeout_seconds == 300


def test_grant_token_in_env_not_cmdline():
    """NEW #4 MINOR: grant token in environment, NOT on the in-container command
    line. The DockerSubprocessRunner passes env vars via --env-file so the token
    never appears in docker argv either — the SandboxContainerConfig.command and
    environment are what the runtime sets; the runner's transport (--env-file) is
    the runner's implementation detail tested by asserting the token is NOT in
    the container command.
    """
    runner = FakeContainerRunner()
    signer = FakeGrantSigner(token="secret-grant-123")
    runtime = ContainerRuntime(runner=runner, grant_signer=signer,
                                broker_url="http://broker:8000")
    runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))
    config = runner.configs[0]
    for arg in config.command:
        assert "secret-grant-123" not in arg
    assert config.environment.get("AGENT_TOOL_GRANT_TOKEN") == "secret-grant-123"


def test_container_nonzero_exit_raises_runtime_error():
    """NEW #4: non-zero exit → RuntimeError."""
    runner = FakeContainerRunner(exit_code=1)
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer,
                                broker_url="http://broker:8000")
    with pytest.raises(RuntimeError, match="Container exited with code 1"):
        runtime.execute(_make_plan(), str(uuid.uuid4()), str(uuid.uuid4()))


def test_hash_drift_refuses_execution():
    """T2 M2 fix."""
    from app.skills.schemas import CompiledStep, ExecutionPlan
    runner = FakeContainerRunner()
    signer = FakeGrantSigner()
    runtime = ContainerRuntime(runner=runner, grant_signer=signer,
                                broker_url="http://broker:8000")
    plan = ExecutionPlan(
        skill_version_id=uuid.uuid4(), hash="deadbeef-not-matching",
        steps=[CompiledStep(id="s1", tool="document.read",
                            input={"document_id": str(uuid.uuid4())})],
    )
    with pytest.raises(RuntimeError, match="Plan hash mismatch"):
        runtime.execute(plan, str(uuid.uuid4()), str(uuid.uuid4()))


# --- NEW #4 IMPORTANT: container mode fail-fast test ---


def test_container_mode_raises_when_docker_unavailable(monkeypatch):
    """NEW #4 IMPORTANT: SANDBOX_RUNTIME=container with no docker binary must
    raise at executor construction, never fall back to fake success.
    """
    # Patch subprocess.run to simulate docker missing.
    import subprocess as sp
    original_run = sp.run

    def _fake_run(cmd, **kwargs):
        if "docker" in cmd and "info" in cmd:
            raise FileNotFoundError("docker")
        return original_run(cmd, **kwargs)

    monkeypatch.setattr(sp, "run", _fake_run)
    monkeypatch.setenv("SANDBOX_RUNTIME", "container")

    from worker.celery_app import build_executor
    with pytest.raises(RuntimeError, match="SANDBOX_RUNTIME=container"):
        build_executor()


# --- Lease re-claim tests ---


def test_stale_running_row_is_claimable(worker_repository, seeded_run):
    first = worker_repository.claim(seeded_run.id)
    assert first is not None

    from datetime import UTC, datetime, timedelta

    from worker.repository import LEASE_TTL
    stale_time = datetime.now(UTC) - LEASE_TTL - timedelta(seconds=10)
    import os

    from sqlalchemy import create_engine, text
    from worker.config import sync_database_url

    db_url = os.getenv(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres")
    engine = create_engine(sync_database_url(db_url))
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE sandbox_runs SET claimed_at = :ts WHERE id = :id"),
            {"ts": stale_time, "id": seeded_run.id})
    second = worker_repository.claim(seeded_run.id)
    assert second is not None


def test_fresh_running_row_is_not_claimable(worker_repository, seeded_run):
    first = worker_repository.claim(seeded_run.id)
    assert first is not None
    second = worker_repository.claim(seeded_run.id)
    assert second is None
