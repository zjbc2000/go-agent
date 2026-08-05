"""LangGraph main assistant: intent recognition -> context assembly -> reply
generation -> document-draft persistence (HITL).

The graph runs inside ``ChatService._generate``. Nodes communicate a minimal,
JSON-safe state; runtime dependencies that are per-run (the SSE ``asyncio.Queue``,
the bound ``create_document_draft`` writer, the ``RequestContext``) travel through
``config["configurable"]`` so the state schema stays plain data (langgraph 0.6
requires an explicit ``state_schema`` and would otherwise try to serialize them).

Intent detection and draft extraction share ONE non-streaming ``complete`` call
(``{intent, needs_more_info, draft}``); the reply is streamed separately. A
"draft" intent routes to ``draft_persist``, which creates a pending approval
linked to the run; ``ChatService`` then emits the ``document.draft`` SSE event and
marks the run ``waiting_approval`` (the durable HITL resume point). A "plain"
intent ends with the ordinary ``run.completed``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.chat.provider import ModelProvider, ProviderMessage
from app.core.context import RequestContext
from app.core.errors import ApiError
from app.planning.schemas import DraftApproval
from app.repositories.planning import Document, DocumentRepository

INTENT_LITERALS: tuple[str, ...] = ("chitchat", "plan_interest", "manage_document", "feedback")
DOC_TYPES: tuple[str, ...] = ("memory", "interest", "task", "skill")
_INTENT_PROMPT = (
    '你是"苟蛋"，兴趣陪伴与规划助手。请仅输出 JSON，判断用户最近一条消息的意图。'
    '意图取值："chitchat"（闲聊/倾诉/问候）、"plan_interest"（表达新兴趣或想尝试的方向）、'
    '"manage_document"（对已有兴趣/任务/记忆文档的调整或更新）、"feedback"（对已生效计划的进展反馈）。'
    '输出形如：{"intent":"...","needs_more_info":true,"draft":{"type":"interest|task|memory|skill",'
    '"title":"...","body":"..."},"document_id":null}。当信息不足无法成稿时 needs_more_info 为 true 且 draft 为 null，'
    "此时可继续提问而非成稿。不要写入正式文档。"
)


@dataclass(frozen=True)
class DraftProposal:
    """A document the assistant proposes the user confirm (never auto-applied)."""

    type: str
    title: str
    body: str
    document_id: str | None = None


@dataclass(frozen=True)
class IntentResult:
    """Parsed result of the intent-completion call."""

    intent: str = "chitchat"
    needs_more_info: bool = False
    proposal: DraftProposal | None = None


class DraftWriter(Protocol):
    """Bound ``PlanningService.create_document_draft`` (idempotent per run)."""

    async def __call__(
        self,
        context: RequestContext,
        type: str,
        title: str,
        body: str,
        *,
        document_id: Any,
        run_id: Any,
    ) -> DraftApproval: ...


class AssistantState(TypedDict, total=False):
    history: list[dict]  # [{"role", "content"}] completed turns
    messages: list[dict]  # system prompt + history, built by assemble_context
    intent: str
    needs_more_info: bool
    proposal: dict | None
    draft_approval: dict | None
    reply_text: str


def parse_intent(text: str) -> IntentResult:
    """Parse the intent JSON; any malformed input degrades to chitchat (never fails a run)."""
    try:
        data = json.loads(text)
    except ValueError:
        return IntentResult()
    if not isinstance(data, dict):
        return IntentResult()
    intent = data.get("intent", "chitchat")
    if intent not in INTENT_LITERALS:
        return IntentResult()
    proposal: DraftProposal | None = None
    draft = data.get("draft")
    if isinstance(draft, dict) and draft.get("type") in DOC_TYPES and draft.get("title") and draft.get("body"):
        proposal = DraftProposal(
            type=draft["type"],
            title=str(draft["title"]),
            body=str(draft["body"]),
            document_id=str(draft["document_id"]) if draft.get("document_id") else None,
        )
    return IntentResult(
        intent=intent,
        needs_more_info=bool(data.get("needs_more_info", False)),
        proposal=proposal,
    )


def build_context_prompt(docs: list[Document]) -> str:
    """System prompt with the user's confirmed documents (never pending drafts)."""
    labels: dict[str, str] = {"interest": "兴趣", "task": "任务", "memory": "记忆"}
    lines: list[str] = []
    for doc_type in ("interest", "task", "memory"):
        for doc in [d for d in docs if d.type == doc_type][:5]:
            body = doc.body.replace("\n", " ").strip()
            lines.append(f"- {labels[doc_type]}：《{doc.title}》— {body}")
    prompt = (
        '你是"苟蛋"，用户的兴趣陪伴与规划助手。先倾听用户的情绪、近况和口语化表达，'
        "再在合适时机把用户的兴趣逐步转化为可确认的兴趣/任务草稿；未经用户确认不得写入正式文档。信息不足时先追问。\n"
        "以下是与用户已确认的文档（仅注入已确认内容，未确认草稿绝不注入）：\n"
        + ("\n".join(lines) if lines else "（暂无已确认文档）")
    )
    return prompt[:6000]


def _proposal_to_dict(proposal: DraftProposal | None) -> dict | None:
    if proposal is None:
        return None
    return {
        "type": proposal.type,
        "title": proposal.title,
        "body": proposal.body,
        "document_id": proposal.document_id,
    }


def _approval_to_dict(approval: DraftApproval) -> dict:
    return {
        "approval_id": str(approval.approval_id),
        "draft_id": str(approval.draft_id),
        "document_id": str(approval.document_id) if approval.document_id else None,
        "type": approval.type,
        "title": approval.title,
        "body": approval.body,
    }


def _to_provider_messages(rows: list[dict]) -> list[ProviderMessage]:
    return [ProviderMessage(role=m["role"], content=m["content"]) for m in rows]


def build_assistant_graph(
    provider: ModelProvider, documents: DocumentRepository
):
    """Build the compiled LangGraph app for the main assistant."""

    async def intent(state: AssistantState, config: RunnableConfig) -> dict:
        messages = [ProviderMessage("system", _INTENT_PROMPT), *_to_provider_messages(state.get("history", []))]
        text = await provider.complete(messages, json_schema=True)
        result = parse_intent(text)
        return {
            "intent": result.intent,
            "needs_more_info": result.needs_more_info,
            "proposal": _proposal_to_dict(result.proposal),
        }

    async def assemble_context(state: AssistantState, config: RunnableConfig) -> dict:
        ctx: RequestContext = config["configurable"]["context"]
        docs = await documents.list_active(ctx)
        prompt = build_context_prompt(docs)
        return {"messages": [{"role": "system", "content": prompt}, *state.get("history", [])]}

    async def generate(state: AssistantState, config: RunnableConfig) -> dict:
        queue: Any = config["configurable"]["queue"]
        messages = _to_provider_messages(state.get("messages", []))
        parts: list[str] = []
        async for delta in provider.stream(messages):
            parts.append(delta.text)
            await queue.put(delta.text)
        return {"reply_text": "".join(parts)}

    async def draft_persist(state: AssistantState, config: RunnableConfig) -> dict:
        proposal = state.get("proposal")
        if not proposal:
            return {"draft_approval": None}
        cfg: dict = config["configurable"]
        writer = cfg.get("draft_writer")
        if writer is None:
            raise ApiError("INTERNAL_ERROR", "Draft writer is not configured.", False)
        approval = await writer(
            cfg["context"],
            proposal["type"],
            proposal["title"],
            proposal["body"],
            document_id=proposal.get("document_id"),
            run_id=cfg["run_id"],
        )
        return {"draft_approval": _approval_to_dict(approval)}

    def route(state: AssistantState) -> str:
        if (
            state.get("intent") in ("plan_interest", "manage_document")
            and state.get("proposal")
            and not state.get("needs_more_info")
        ):
            return "draft_persist"
        return END

    graph = StateGraph(AssistantState)
    graph.add_node("intent", intent)
    graph.add_node("assemble_context", assemble_context)
    graph.add_node("generate", generate)
    graph.add_node("draft_persist", draft_persist)
    graph.add_edge(START, "intent")
    graph.add_edge("intent", "assemble_context")
    graph.add_edge("assemble_context", "generate")
    graph.add_conditional_edges("generate", route)
    graph.add_edge("draft_persist", END)
    return graph.compile()
