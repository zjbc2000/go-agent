"""Runtime seam between the executor and an isolated sandbox.

Task 2 ships a stub in-process runtime that "executes" a claimed run's compiled
steps by recording each ``(sandbox_run_id, step_id)`` into an injectable audit
store. Task 3 replaces ``StubRuntime`` with the isolated Docker runtime (no DB
credentials, non-root, deny-by-default egress); the executor only depends on the
``Runtime`` protocol, so swapping is a one-line change in ``celery_app``.
"""

from __future__ import annotations

from typing import Protocol

from app.audit import MemoryAuditStore
from app.skills.schemas import ExecutionPlan


class AuditStore(Protocol):
    def record(self, sandbox_run_id: str, step_id: str) -> None: ...


class Runtime(Protocol):
    def execute(self, plan: ExecutionPlan, sandbox_run_id: str) -> None: ...


class StubRuntime:
    """In-process runtime seam: records each compiled step into the audit store."""

    def __init__(self, audit: MemoryAuditStore | None = None) -> None:
        self._audit = audit or MemoryAuditStore()

    def execute(self, plan: ExecutionPlan, sandbox_run_id: str) -> None:
        for step in plan.steps:
            self._audit.record(sandbox_run_id, step.id)
