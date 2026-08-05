"""LangGraph main-assistant tests: intent -> context -> stream -> draft HITL.

The graph runs inside ``ChatService._generate``. Default (chitchat) keeps the
plain streaming path; a ``plan_interest`` intent routes to a document draft,
marks the run ``waiting_approval``, and the approval decision completes it.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from app.agent.graph import build_assistant_graph
from app.chat.provider import DeterministicProvider
from app.chat.service import ChatService
from app.planning.service import PlanningService
from app.repositories.chat import ChatRepository
from app.repositories.planning import DocumentRepository
from sqlalchemy import text

_PLAN_RESPONSE = json.dumps(
    {
        "intent": "plan_interest",
        "needs_more_info": False,
        "draft": {
            "type": "interest",
            "title": "学习摄影",
            "body": "目标：掌握构图与光线。步骤：每周一次街拍，整理作品集。",
        },
        "document_id": None,
    }
)


@pytest.fixture(autouse=True)
async def _provision_auth_user(db_session, user_context) -> None:
    """The planning FK references auth.users(id); the chat conftest's synthetic
    user_id needs a matching row before any document insert."""
    await db_session.execute(
        text("insert into auth.users (id) values (:id) on conflict (id) do nothing"),
        {"id": user_context.user_id},
    )
    await db_session.commit()


def _build_service(
    chat_repository: ChatRepository,
    planning_repository: DocumentRepository,
    cipher,
    *,
    complete_response: str = '{"intent": "chitchat"}',
) -> ChatService:
    provider = DeterministicProvider(delay_seconds=0.0, complete_response=complete_response)
    graph = build_assistant_graph(provider, planning_repository)
    chat = ChatService(chat_repository, provider, timedelta(days=7), graph=graph)
    planning = PlanningService(repository=planning_repository, cipher=cipher, chat=chat)
    chat.set_draft_writer(planning.create_document_draft)
    return chat


async def _collect_events(chat: ChatService, context, session_id, content, key):
    created = await chat.create_run(context, session_id, content, key)
    return [e async for e in chat.stream_run(context, created.run_id, 0)]


async def test_chitchat_produces_plain_stream(
    chat_repository, assistant_graph, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(
        session_factory=chat_repository._session_factory, cipher=cipher
    )
    chat = _build_service(
        chat_repository, planning_repository, cipher, complete_response='{"intent": "chitchat"}'
    )
    events = await _collect_events(chat, user_context, owned_session, "hello", "k-chat")

    kinds = [e.kind for e in events]
    assert kinds[0] == "run.started"
    assert kinds[-1] == "run.completed"
    assert "message.delta" in kinds
    assert "document.draft" not in kinds
    run = await chat_repository.get_run(user_context, events[0].run_id)
    assert run is not None and run.status == "completed"


async def test_plan_interest_creates_draft_and_waiting_approval(
    chat_repository, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(
        session_factory=chat_repository._session_factory, cipher=cipher
    )
    chat = _build_service(chat_repository, planning_repository, cipher, complete_response=_PLAN_RESPONSE)
    events = await _collect_events(chat, user_context, owned_session, "我想学摄影", "k-plan")

    kinds = [e.kind for e in events]
    assert kinds[0] == "run.started"
    assert kinds[-1] == "document.draft"
    assert "run.completed" not in kinds

    draft_event = events[-1]
    payload = json.loads(draft_event.payload)
    assert payload["type"] == "interest"
    assert payload["title"] == "学习摄影"

    run = await chat_repository.get_run(user_context, events[0].run_id)
    assert run is not None and run.status == "waiting_approval"


async def test_confirm_completes_waiting_approval_run(
    chat_repository, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(
        session_factory=chat_repository._session_factory, cipher=cipher
    )
    chat = _build_service(chat_repository, planning_repository, cipher, complete_response=_PLAN_RESPONSE)
    events = await _collect_events(chat, user_context, owned_session, "我想学摄影", "k-confirm")
    draft_event = events[-1]
    approval_id = uuid.UUID(json.loads(draft_event.payload)["approvalId"])

    planning = PlanningService(repository=planning_repository, cipher=cipher, chat=chat)
    result = await planning.decide_document_approval(
        user_context, approval_id, "approve", None, "confirm-1"
    )
    assert result.document is not None and result.document.type == "interest"

    run = await chat_repository.get_run(user_context, events[0].run_id)
    assert run is not None and run.status == "completed"
    messages = await chat_repository.list_messages(user_context, owned_session)
    assistant = [m for m in messages if m.role == "assistant"]
    assert assistant and assistant[-1].status == "completed"


async def test_reject_completes_waiting_approval_run(
    chat_repository, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(
        session_factory=chat_repository._session_factory, cipher=cipher
    )
    chat = _build_service(chat_repository, planning_repository, cipher, complete_response=_PLAN_RESPONSE)
    events = await _collect_events(chat, user_context, owned_session, "我想学摄影", "k-reject")
    approval_id = uuid.UUID(json.loads(events[-1].payload)["approvalId"])

    planning = PlanningService(repository=planning_repository, cipher=cipher, chat=chat)
    result = await planning.decide_document_approval(
        user_context, approval_id, "reject", None, "reject-1"
    )
    assert result.document is None

    run = await chat_repository.get_run(user_context, events[0].run_id)
    assert run is not None and run.status == "completed"
    docs = await planning_repository.list_active(user_context)
    assert all(d.type != "interest" for d in docs)


async def test_reconnect_waiting_approval_replays_without_duplicate_draft(
    chat_repository, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(
        session_factory=chat_repository._session_factory, cipher=cipher
    )
    chat = _build_service(chat_repository, planning_repository, cipher, complete_response=_PLAN_RESPONSE)
    created = await chat.create_run(user_context, owned_session, "我想学摄影", "k-replay")

    # First pass: generate to a waiting_approval state.
    first = [e async for e in chat.stream_run(user_context, created.run_id, 0)]
    assert first[-1].kind == "document.draft"

    # Reconnect from zero: the run is now waiting_approval, so it replays the SAME
    # persisted events (run.started -> deltas -> document.draft) and stops. No new
    # generation: replay must be byte-identical to the first pass.
    replay = [e async for e in chat.stream_run(user_context, created.run_id, 0)]
    kinds = [e.kind for e in replay]
    assert kinds[-1] == "document.draft"
    assert [e.kind for e in first] == kinds

    # Idempotent create_document_draft: still exactly one approval for this run.
    planning = PlanningService(repository=planning_repository, cipher=cipher, chat=chat)
    again = await planning.create_document_draft(
        user_context, "interest", "x", "y", run_id=created.run_id
    )
    draft_id = json.loads(first[-1].payload)["approvalId"]
    assert str(again.approval_id) == draft_id


async def test_context_injection_reaches_generate(
    chat_repository, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(
        session_factory=chat_repository._session_factory, cipher=cipher
    )
    doc = await planning_repository.create_active(
        user_context, "interest", "已确认摄影兴趣", "每周街拍两次"
    )
    seen: list[str] = []
    base = DeterministicProvider(delay_seconds=0.0, complete_response='{"intent": "chitchat"}')

    # Wrap stream so we capture the system prompt the graph assembles for generate.
    class CapturingProvider(DeterministicProvider):
        async def stream(self, messages):
            for m in messages:
                if m.role == "system":
                    seen.append(m.content)
            async for d in base.stream(messages):
                yield d

    provider = CapturingProvider(delay_seconds=0.0, complete_response='{"intent": "chitchat"}')
    graph = build_assistant_graph(provider, planning_repository)
    chat = ChatService(chat_repository, provider, timedelta(days=7), graph=graph)
    planning = PlanningService(repository=planning_repository, cipher=cipher, chat=chat)
    chat.set_draft_writer(planning.create_document_draft)

    await _collect_events(chat, user_context, owned_session, "你好", "k-ctx")
    assert seen, "the graph should send a system prompt to the provider"
    assert "已确认摄影兴趣" in seen[0]
    assert doc.title in seen[0]


async def test_intent_parse_failure_falls_back_to_chitchat(
    chat_repository, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(
        session_factory=chat_repository._session_factory, cipher=cipher
    )
    chat = _build_service(chat_repository, planning_repository, cipher, complete_response="not-json")
    events = await _collect_events(chat, user_context, owned_session, "hello", "k-bad")
    kinds = [e.kind for e in events]
    assert kinds[-1] == "run.completed"
    assert "document.draft" not in kinds
