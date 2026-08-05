"""Service-role worker repository: claim runs and re-derive their immutable plans.

The worker is the TRUSTED orchestrator and acts as the system: it connects with a
service-role Postgres session (bypassing RLS) to read ``sandbox_runs`` and the
pinned ``document_versions`` row. The isolated sandbox CONTAINER gets no database
credentials — that is Task 3. ``plan_hash`` pins the re-derived plan to the exact
version the service compiled against; recompiling the pinned document body with
the run's stored inputs reproduces the same immutable plan.

Lease-based claim (Task 3 I1): ``claim`` now atomically transitions queued->running
OR re-claims a stale running row whose ``claimed_at`` is older than the lease TTL,
so a crashed worker's stuck running row is recoverable.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.crypto import EnvelopeCipher
from app.models.execution import SandboxRun as SandboxRunRecord
from app.models.planning import DocumentVersion as DocumentVersionRecord
from app.skills.compiler import compile_skill
from app.skills.schemas import ExecutionPlan
from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import Engine

from worker.config import sync_database_url

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "timed_out", "policy_denied"})

# A run stuck in ``running`` for longer than this window is re-claimable
# (the worker that claimed it is presumed dead).
LEASE_TTL = timedelta(minutes=5)


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class ClaimedRun:
    """A run the caller won the right to execute, with its immutable plan."""

    id: str
    plan: ExecutionPlan
    user_id: uuid.UUID


class WorkerRepository:
    """Persistence boundary for the trusted worker (service-role Postgres)."""

    def __init__(self, database_url: str, cipher: EnvelopeCipher) -> None:
        self._engine: Engine = create_engine(sync_database_url(database_url), pool_pre_ping=True)
        self._cipher = cipher

    def is_terminal(self, run_id: str) -> bool:
        """True when the run has already reached a terminal status (no-op)."""
        return self.get_status(run_id) in TERMINAL_STATUSES

    def get_status(self, run_id: str) -> str | None:
        """Return the run's current status, or None if the run does not exist."""
        with self._engine.connect() as conn:
            return conn.execute(
                select(SandboxRunRecord.status).where(SandboxRunRecord.id == uuid.UUID(run_id))
            ).scalar_one_or_none()

    def claim(self, run_id: str) -> ClaimedRun | None:
        """Atomically claim a run (queued or stale-running) and load its plan.

        Lease-based (Task 3 I1): a run stuck in ``running`` whose ``claimed_at``
        is older than ``LEASE_TTL`` is re-claimable — the original worker is
        presumed dead. A fresh ``running`` row is not claimable (exactly one
        winner).
        """
        run_id_uuid = uuid.UUID(run_id)
        now = _utcnow()
        stale_threshold = now - LEASE_TTL
        with self._engine.begin() as conn:
            row = conn.execute(
                update(SandboxRunRecord)
                .where(
                    SandboxRunRecord.id == run_id_uuid,
                    (
                        (SandboxRunRecord.status == "queued")
                        | (
                            (SandboxRunRecord.status == "running")
                            & (
                                (SandboxRunRecord.claimed_at.is_(None))
                                | (SandboxRunRecord.claimed_at < stale_threshold)
                            )
                        )
                    ),
                )
                .values(status="running", claimed_at=now, started_at=now, updated_at=now)
                .returning(
                    SandboxRunRecord.id,
                    SandboxRunRecord.document_id,
                    SandboxRunRecord.version_id,
                    SandboxRunRecord.plan_hash,
                    SandboxRunRecord.inputs_ciphertext,
                    SandboxRunRecord.user_id,
                )
            ).first()
        if row is None:
            return None
        claimed_id, document_id, version_id, _plan_hash, inputs_ciphertext, user_id = row
        plan = self._load_plan(document_id, version_id, inputs_ciphertext)
        return ClaimedRun(id=str(claimed_id), plan=plan, user_id=user_id)

    def finish(self, run_id: str, *, status: str, error_code: str | None = None) -> None:
        """Mark a run terminal (succeeded / failed / ...)."""
        now = _utcnow()
        with self._engine.begin() as conn:
            conn.execute(
                update(SandboxRunRecord)
                .where(SandboxRunRecord.id == uuid.UUID(run_id))
                .values(status=status, error_code=error_code, finished_at=now, updated_at=now)
            )

    def _load_plan(
        self, document_id: uuid.UUID, version_id: uuid.UUID, inputs_ciphertext: str
    ) -> ExecutionPlan:
        """Re-derive the immutable plan from the pinned version and stored inputs."""
        with self._engine.connect() as conn:
            body_ciphertext = conn.execute(
                select(DocumentVersionRecord.body_ciphertext).where(
                    DocumentVersionRecord.id == version_id,
                    DocumentVersionRecord.document_id == document_id,
                )
            ).scalar_one_or_none()
        if body_ciphertext is None:
            raise RuntimeError("Sandbox run references an unknown document version.")
        manifest = json.loads(self._cipher.decrypt(body_ciphertext))
        inputs = self._cipher.decrypt_json(inputs_ciphertext).get("inputs", {})
        return compile_skill(manifest, inputs, version_id=version_id)
