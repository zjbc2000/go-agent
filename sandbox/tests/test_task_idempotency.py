"""Worker idempotency tests: a duplicate delivery can never write a step twice.

The three guards (``is_terminal``, the atomic queued->running claim, and the
``(sandbox_run_id, step_id)`` audit key) are exercised directly: calling the task
twice records the step once, a terminal run is a no-op, and a concurrent
double-claim has exactly one winner.
"""

import threading

from worker.repository import WorkerRepository


def test_duplicate_task_executes_each_step_once(worker, seeded_run, audit):
    worker.execute_sandbox_run(seeded_run.id)
    worker.execute_sandbox_run(seeded_run.id)
    assert audit.count(step_id="write-title") == 1


def test_run_reaches_succeeded_status(worker_repository, worker, seeded_run, audit):
    worker.execute_sandbox_run(seeded_run.id)
    assert worker_repository.get_status(seeded_run.id) == "succeeded"
    assert audit.count(step_id="write-title") == 1


def test_terminal_run_is_a_noop(worker_repository, worker, seeded_run, audit):
    worker_repository.finish(seeded_run.id, status="succeeded")
    worker.execute_sandbox_run(seeded_run.id)
    assert worker_repository.get_status(seeded_run.id) == "succeeded"
    assert audit.count(step_id="write-title") == 0


def test_claim_is_atomic_single_winner(worker_repository, seeded_run):
    first = worker_repository.claim(seeded_run.id)
    second = worker_repository.claim(seeded_run.id)
    assert first is not None
    assert second is None
    # A claimed (now running) run can never be claimed again.
    assert worker_repository.claim(seeded_run.id) is None


def test_concurrent_double_claim_has_one_winner(worker_repository: WorkerRepository, seeded_run):
    results = []
    lock = threading.Lock()

    def attempt() -> None:
        claimed = worker_repository.claim(seeded_run.id)
        with lock:
            results.append(claimed)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winners = [claimed for claimed in results if claimed is not None]
    assert len(winners) == 1
