"""The sandbox execution boundary: the idempotent queued->running->succeeded loop.

The Celery task is named ``sandbox.execute`` and receives ONLY ``sandbox_run_id``
(plan Global Constraint). Three independent guards make duplicate delivery safe:
``is_terminal`` short-circuits already-finished runs, ``claim`` is an atomic
queued->running conditional update that exactly one caller can win, and the audit
store is keyed by ``(sandbox_run_id, step_id)`` so a step can never be recorded
twice. A crash or duplicate delivery can therefore never produce duplicate step
writes.

Task 3 extends the executor: ``claim`` is now a lease (stale running rows are
re-claimable, I1), the stub runtime is swapped for the isolated Docker runtime
when ``SANDBOX_RUNTIME=container``, and the claimed user_id is passed through
so the ContainerRuntime can mint a grant with the real user.
"""

from __future__ import annotations

import logging

from worker.repository import WorkerRepository
from worker.runtime import Runtime

logger = logging.getLogger(__name__)


class SandboxExecutor:
    """Runs one ``sandbox_run_id`` through the claim guard and the runtime seam."""

    def __init__(self, repository: WorkerRepository, runtime: Runtime) -> None:
        self._repository = repository
        self._runtime = runtime

    def execute_sandbox_run(self, sandbox_run_id: str) -> None:
        if self._repository.is_terminal(sandbox_run_id):
            return
        claimed = self._repository.claim(sandbox_run_id)
        if claimed is None:
            return
        try:
            self._runtime.execute(claimed.plan, claimed.id, str(claimed.user_id))
        except Exception:
            logger.exception("Sandbox run %s failed", sandbox_run_id)
            self._repository.finish(sandbox_run_id, status="failed", error_code="SANDBOX_ERROR")
            raise
        self._repository.finish(sandbox_run_id, status="succeeded")
