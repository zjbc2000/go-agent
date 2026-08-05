"""In-process audit store for the Task-2 stub runtime.

The real runtime (Task 3) records step writes durably; this memory store is
injectable and keyed by ``(sandbox_run_id, step_id)`` so a step can never be
recorded twice within a worker process, which the duplicate-delivery test
relies on. Idempotency across crashes still comes from the database guards
(``is_terminal`` + atomic claim), not from this store.
"""

from __future__ import annotations

import threading
from collections import Counter


class MemoryAuditStore:
    """A thread-safe, deduplicating step audit keyed by (run_id, step_id)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: set[tuple[str, str]] = set()
        self._counts: Counter[str] = Counter()

    def record(self, sandbox_run_id: str, step_id: str) -> None:
        with self._lock:
            key = (sandbox_run_id, step_id)
            if key in self._seen:
                return
            self._seen.add(key)
            self._counts[step_id] += 1

    def count(self, step_id: str) -> int:
        with self._lock:
            return self._counts[step_id]
