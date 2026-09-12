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
from app.employees.service import EmployeeService
from app.planning.service import PlanningService
from app.repositories.chat import ChatRepository
from app.repositories.employees import EmployeeRepository
from app.repositories.planning import DocumentRepository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

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


@pytest.fixture(autouse=True)
async def _clean_employees_tables(engine) -> None:
    """Truncate employee tables before each test (the chat conftest does not)."""
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await session.execute(text("truncate table public.employee_approvals, public.employees cascade"))
        await session.commit()


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


def _build_employee_service(
    chat_repository: ChatRepository,
    planning_repository: DocumentRepository,
    employee_repository: EmployeeRepository,
    cipher,
    *,
    complete_response: str = '{"intent": "chitchat"}',
) -> ChatService:
    provider = DeterministicProvider(delay_seconds=0.0, complete_response=complete_response)
    graph = build_assistant_graph(provider, planning_repository, employee_repository)
    chat = ChatService(chat_repository, provider, timedelta(days=7), graph=graph)
    planning = PlanningService(repository=planning_repository, cipher=cipher, chat=chat)
    employees = EmployeeService(repository=employee_repository, cipher=cipher, chat=chat)
    chat.set_draft_writer(planning.create_document_draft)
    chat.set_employee_writer(employees.create_employee_action_draft)
    return chat


async def _collect_events(chat: ChatService, context, session_id, content, key):
    created = await chat.create_run(context, session_id, content, key)
    return [e async for e in chat.stream_run(context, created.run_id, 0)]


async def test_chitchat_produces_plain_stream(
    chat_repository, assistant_graph, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    chat = _build_service(chat_repository, planning_repository, cipher, complete_response='{"intent": "chitchat"}')
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
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
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


async def test_confirm_completes_waiting_approval_run(chat_repository, cipher, user_context, owned_session, db_session):
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    chat = _build_service(chat_repository, planning_repository, cipher, complete_response=_PLAN_RESPONSE)
    events = await _collect_events(chat, user_context, owned_session, "我想学摄影", "k-confirm")
    draft_event = events[-1]
    approval_id = uuid.UUID(json.loads(draft_event.payload)["approvalId"])

    planning = PlanningService(repository=planning_repository, cipher=cipher, chat=chat)
    result = await planning.decide_document_approval(user_context, approval_id, "approve", None, "confirm-1")
    assert result.document is not None and result.document.type == "interest"

    run = await chat_repository.get_run(user_context, events[0].run_id)
    assert run is not None and run.status == "completed"
    messages = await chat_repository.list_messages(user_context, owned_session)
    assistant = [m for m in messages if m.role == "assistant"]
    assert assistant and assistant[-1].status == "completed"


async def test_reject_completes_waiting_approval_run(chat_repository, cipher, user_context, owned_session, db_session):
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    chat = _build_service(chat_repository, planning_repository, cipher, complete_response=_PLAN_RESPONSE)
    events = await _collect_events(chat, user_context, owned_session, "我想学摄影", "k-reject")
    approval_id = uuid.UUID(json.loads(events[-1].payload)["approvalId"])

    planning = PlanningService(repository=planning_repository, cipher=cipher, chat=chat)
    result = await planning.decide_document_approval(user_context, approval_id, "reject", None, "reject-1")
    assert result.document is None

    run = await chat_repository.get_run(user_context, events[0].run_id)
    assert run is not None and run.status == "completed"
    docs = await planning_repository.list_active(user_context)
    assert all(d.type != "interest" for d in docs)


async def test_reconnect_waiting_approval_replays_without_duplicate_draft(
    chat_repository, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
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
    again = await planning.create_document_draft(user_context, "interest", "x", "y", run_id=created.run_id)
    draft_id = json.loads(first[-1].payload)["approvalId"]
    assert str(again.approval_id) == draft_id


async def test_context_injection_reaches_generate(chat_repository, cipher, user_context, owned_session, db_session):
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    doc = await planning_repository.create_active(user_context, "interest", "已确认摄影兴趣", "每周街拍两次")
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
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    chat = _build_service(chat_repository, planning_repository, cipher, complete_response="not-json")
    events = await _collect_events(chat, user_context, owned_session, "hello", "k-bad")
    kinds = [e.kind for e in events]
    assert kinds[-1] == "run.completed"
    assert "document.draft" not in kinds


# --- manage_employee intent + employee.action SSE ----------------------------


def _employee_fire_response(employee_id) -> str:
    return json.dumps(
        {
            "intent": "manage_employee",
            "needs_more_info": False,
            "employee_action": {"action": "fire", "employee_id": str(employee_id)},
        }
    )


async def test_manage_employee_creates_approval_and_waiting_approval(
    chat_repository, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee_repository = EmployeeRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee = await employee_repository.create(user_context, "李雷", "运营专员", "负责社区运营")
    chat = _build_employee_service(
        chat_repository,
        planning_repository,
        employee_repository,
        cipher,
        complete_response=_employee_fire_response(employee.id),
    )
    events = await _collect_events(chat, user_context, owned_session, "把李雷解雇了", "k-emp-fire")

    kinds = [e.kind for e in events]
    assert kinds[0] == "run.started"
    assert kinds[-1] == "employee.action"
    assert "document.draft" not in kinds
    assert "run.completed" not in kinds

    payload = json.loads(events[-1].payload)
    assert payload["action"] == "fire"
    assert payload["name"] == "李雷"
    assert payload["position"] == "运营专员"
    assert payload["status"] == "active"

    run = await chat_repository.get_run(user_context, events[0].run_id)
    assert run is not None and run.status == "waiting_approval"


async def test_employee_approval_decision_completes_run(
    chat_repository, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee_repository = EmployeeRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee = await employee_repository.create(user_context, "李雷", "运营专员", "负责社区运营")
    chat = _build_employee_service(
        chat_repository,
        planning_repository,
        employee_repository,
        cipher,
        complete_response=_employee_fire_response(employee.id),
    )
    events = await _collect_events(chat, user_context, owned_session, "把李雷解雇了", "k-emp-decide")
    approval_id = uuid.UUID(json.loads(events[-1].payload)["approvalId"])

    employees = EmployeeService(repository=employee_repository, cipher=cipher, chat=chat)
    result = await employees.decide_employee_approval(user_context, approval_id, "approve", "decide-1")
    assert result.employee is not None and result.employee.status == "inactive"

    run = await chat_repository.get_run(user_context, events[0].run_id)
    assert run is not None and run.status == "completed"


async def test_context_injection_includes_only_active_employees(
    chat_repository, cipher, user_context, owned_session, db_session
):
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee_repository = EmployeeRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    active = await employee_repository.create(user_context, "李雷", "运营专员", "负责社区运营")
    fired = await employee_repository.create(user_context, "韩梅梅", "设计师", "负责视觉设计")
    await employee_repository.set_status(user_context, fired.id, "inactive")

    seen: list[str] = []
    base = DeterministicProvider(delay_seconds=0.0, complete_response='{"intent": "chitchat"}')

    class CapturingProvider(DeterministicProvider):
        async def stream(self, messages):
            for m in messages:
                if m.role == "system":
                    seen.append(m.content)
            async for d in base.stream(messages):
                yield d

    provider = CapturingProvider(delay_seconds=0.0, complete_response='{"intent": "chitchat"}')
    graph = build_assistant_graph(provider, planning_repository, employee_repository)
    chat = ChatService(chat_repository, provider, timedelta(days=7), graph=graph)
    planning = PlanningService(repository=planning_repository, cipher=cipher, chat=chat)
    employees = EmployeeService(repository=employee_repository, cipher=cipher, chat=chat)
    chat.set_draft_writer(planning.create_document_draft)
    chat.set_employee_writer(employees.create_employee_action_draft)

    await _collect_events(chat, user_context, owned_session, "你好", "k-emp-ctx")
    assert seen, "the graph should send a system prompt to the provider"
    assert active.name in seen[0]
    assert active.position in seen[0]
    assert active.prompt in seen[0]
    assert fired.name not in seen[0]


async def test_employee_session_injects_persona_and_user_docs(chat_repository, cipher, user_context, db_session):
    """An employee-bound session injects the employee's persona PLUS the user's
    confirmed planning documents — never the planning-assistant SOUL or the roster."""
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee_repository = EmployeeRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee = await employee_repository.create(user_context, "苏曼", "秘书", "负责安排日程与会议纪要")
    await planning_repository.create_active(user_context, "interest", "已确认摄影兴趣", "每周街拍")
    employee_session = await chat_repository.create_session(user_context, "[秘书]苏曼", employee.id)

    seen: list[str] = []
    base = DeterministicProvider(delay_seconds=0.0, complete_response='{"intent": "chitchat"}')

    class CapturingProvider(DeterministicProvider):
        async def stream(self, messages):
            for m in messages:
                if m.role == "system":
                    seen.append(m.content)
            async for d in base.stream(messages):
                yield d

    provider = CapturingProvider(delay_seconds=0.0, complete_response='{"intent": "chitchat"}')
    graph = build_assistant_graph(provider, planning_repository, employee_repository)
    chat = ChatService(chat_repository, provider, timedelta(days=7), graph=graph)
    planning = PlanningService(repository=planning_repository, cipher=cipher, chat=chat)
    employees = EmployeeService(repository=employee_repository, cipher=cipher, chat=chat)
    chat.set_draft_writer(planning.create_document_draft)
    chat.set_employee_writer(employees.create_employee_action_draft)

    await _collect_events(chat, user_context, employee_session.id, "你好", "k-emp-sess")
    assert seen, "the graph should send a system prompt to the provider"
    prompt = seen[0]
    assert "苏曼" in prompt
    assert "秘书" in prompt
    assert "负责安排日程与会议纪要" in prompt
    # 用户已确认文档注入员工会话（员工能“传入”用户的规划）。
    assert "已确认摄影兴趣" in prompt
    # 苟蛋人格、员工列表绝不注入员工会话。
    assert "兴趣陪伴" not in prompt
    assert "运营专员" not in prompt


async def test_employee_session_proposes_draft_and_waits_approval(chat_repository, cipher, user_context, db_session):
    """An employee can propose a memory/interest/task draft (HITL): the run ends in
    waiting_approval with a document.draft event, like a 苟蛋 session."""
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee_repository = EmployeeRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee = await employee_repository.create(user_context, "苏曼", "秘书", "负责安排日程")
    employee_session = await chat_repository.create_session(user_context, "[秘书]苏曼", employee.id)
    plan_response = json.dumps(
        {
            "intent": "plan_interest",
            "needs_more_info": False,
            "draft": {"type": "interest", "title": "学插花", "body": "每周一次花艺课"},
            "document_id": None,
        }
    )
    chat = _build_employee_service(
        chat_repository,
        planning_repository,
        employee_repository,
        cipher,
        complete_response=plan_response,
    )
    events = await _collect_events(chat, user_context, employee_session.id, "帮我记一个兴趣", "k-emp-draft")

    kinds = [e.kind for e in events]
    assert kinds[0] == "run.started"
    assert kinds[-1] == "document.draft"
    assert "run.completed" not in kinds
    payload = json.loads(events[-1].payload)
    assert payload["type"] == "interest"
    assert payload["title"] == "学插花"

    run = await chat_repository.get_run(user_context, events[0].run_id)
    assert run is not None and run.status == "waiting_approval"


async def test_employee_session_rejects_skill_proposal(chat_repository, cipher, user_context, db_session):
    """An employee never proposes skill documents (they need a JSON manifest)."""
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee_repository = EmployeeRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee = await employee_repository.create(user_context, "苏曼", "秘书", "负责安排日程")
    employee_session = await chat_repository.create_session(user_context, "[秘书]苏曼", employee.id)
    skill_response = json.dumps(
        {
            "intent": "plan_interest",
            "needs_more_info": False,
            "draft": {"type": "skill", "title": "不该出现", "body": "不该出现"},
            "document_id": None,
        }
    )
    chat = _build_employee_service(
        chat_repository,
        planning_repository,
        employee_repository,
        cipher,
        complete_response=skill_response,
    )
    events = await _collect_events(chat, user_context, employee_session.id, "建个技能", "k-emp-skill")

    kinds = [e.kind for e in events]
    assert kinds[-1] == "run.completed"
    assert "document.draft" not in kinds


async def test_employee_session_ignores_manage_employee(chat_repository, cipher, user_context, db_session):
    """An employee never manages other employees: employee_action is always ignored."""
    planning_repository = DocumentRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee_repository = EmployeeRepository(session_factory=chat_repository._session_factory, cipher=cipher)
    employee = await employee_repository.create(user_context, "苏曼", "秘书", "负责安排日程")
    employee_session = await chat_repository.create_session(user_context, "[秘书]苏曼", employee.id)
    manage_response = json.dumps(
        {
            "intent": "manage_employee",
            "needs_more_info": False,
            "employee_action": {"action": "fire", "employee_id": str(employee.id)},
        }
    )
    chat = _build_employee_service(
        chat_repository,
        planning_repository,
        employee_repository,
        cipher,
        complete_response=manage_response,
    )
    events = await _collect_events(chat, user_context, employee_session.id, "解雇个同事", "k-emp-mgmt")

    kinds = [e.kind for e in events]
    assert kinds[-1] == "run.completed"
    assert "document.draft" not in kinds
    assert "employee.action" not in kinds
