"""Shared fixtures for the sandbox worker tests.

The worker is the trusted orchestrator and connects with a service-role Postgres
session, so the fixtures seed the shared local Supabase database directly with a
queued ``sandbox_runs`` row backed by a real skill ``documents`` +
``document_versions`` row (body ciphertext) — following the ``agent-service``
conftest pattern. The cipher matches the agent-service dev key so the worker can
decrypt and re-derive the plan.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from dataclasses import dataclass

import pytest
from app.core.crypto import LocalEnvelopeCipher
from sqlalchemy import create_engine, text
from worker.audit import MemoryAuditStore
from worker.config import sync_database_url
from worker.repository import WorkerRepository
from worker.runtime import StubRuntime
from worker.tasks import SandboxExecutor

# Dev/test-only key. Never use the all-zero key outside tests.
TEST_KEY = base64.urlsafe_b64encode(b"0" * 32).decode()

# Local Supabase Postgres (see `supabase status`); same DB the agent-service tests use.
DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres"
)

_TRUNCATE = (
    "truncate table public.sandbox_tool_calls, public.mcp_tools, public.mcp_servers, "
    "public.outbox_events, public.sandbox_runs, public.execution_approvals, "
    "public.audit_logs, public.approvals, public.document_drafts, "
    "public.document_versions, public.documents cascade"
)

# A write skill whose only step is the step the idempotency test watches.
WRITE_MANIFEST = {
    "schema_version": 1,
    "steps": [
        {
            "id": "write-title",
            "tool": "document.create",
            "input": {"type": "task", "title": "{{title}}", "body": "skill body"},
        }
    ],
}


@dataclass(frozen=True)
class SeededRun:
    id: str
    user_id: uuid.UUID
    document_id: uuid.UUID
    version_id: uuid.UUID


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Truncate execution and planning tables before each test (deterministic state)."""
    engine = create_engine(sync_database_url(DATABASE_URL))
    with engine.begin() as conn:
        conn.execute(text(_TRUNCATE))


@pytest.fixture
def cipher() -> LocalEnvelopeCipher:
    return LocalEnvelopeCipher.from_base64_key(TEST_KEY)


@pytest.fixture
def worker_repository(cipher: LocalEnvelopeCipher) -> WorkerRepository:
    return WorkerRepository(database_url=DATABASE_URL, cipher=cipher)


@pytest.fixture
def audit() -> MemoryAuditStore:
    return MemoryAuditStore()


@pytest.fixture
def worker(worker_repository: WorkerRepository, audit: MemoryAuditStore) -> SandboxExecutor:
    return SandboxExecutor(repository=worker_repository, runtime=StubRuntime(audit=audit))


@pytest.fixture
def seeded_run(cipher: LocalEnvelopeCipher) -> SeededRun:
    """A queued sandbox run whose plan has the ``write-title`` step."""
    engine = create_engine(sync_database_url(DATABASE_URL))
    with engine.begin() as conn:
        user_id = uuid.uuid4()
        conn.execute(
            text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
            {"id": user_id},
        )
        body_ct = cipher.encrypt(json.dumps(WRITE_MANIFEST))
        document_id = uuid.uuid4()
        version_id = uuid.uuid4()
        conn.execute(
            text(
                """
                insert into documents
                  (id, user_id, type, current_version, status, title_ciphertext, body_ciphertext)
                values (:id, :uid, 'skill', 1, 'active', :title, :body)
                """
            ),
            {"id": document_id, "uid": user_id, "title": body_ct, "body": body_ct},
        )
        conn.execute(
            text(
                """
                insert into document_versions
                  (id, user_id, document_id, version, title_ciphertext, body_ciphertext)
                values (:id, :uid, :doc, 1, :title, :body)
                """
            ),
            {"id": version_id, "uid": user_id, "doc": document_id, "title": body_ct, "body": body_ct},
        )
        inputs_ct = cipher.encrypt(
            json.dumps({"inputs": {"title": "x"}}, sort_keys=True, separators=(",", ":"))
        )
        run_id = uuid.uuid4()
        conn.execute(
            text(
                """
                insert into sandbox_runs
                  (id, user_id, document_id, version_id, plan_hash, status, inputs_ciphertext)
                values (:id, :uid, :doc, :ver, :hash, 'queued', :ct)
                """
            ),
            {
                "id": run_id,
                "uid": user_id,
                "doc": document_id,
                "ver": version_id,
                "hash": "x" * 64,
                "ct": inputs_ct,
            },
        )
    return SeededRun(id=str(run_id), user_id=user_id, document_id=document_id, version_id=version_id)
