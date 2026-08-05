"""Chat orchestration: durable run streaming with replay-and-resume.

A run stream is a sequence of persisted, encrypted events. Every delta is appended
BEFORE it is yielded, so a client that reconnects with ``Last-Event-ID`` can replay
the events it missed and resume generation without duplicating the user message.

Resuming an interrupted generation restarts the provider from the conversation
history and skips deltas that were already persisted (deterministic in tests; for a
real LLM this yields a fresh continuation, which is acceptable for the MVP).
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.chat.provider import ModelProvider, ProviderMessage
from app.core.context import RequestContext
from app.core.errors import ApiError
from app.repositories.chat import ChatRepository, ChatSession, CreatedRun, Message, StreamEvent

logger = logging.getLogger(__name__)


class ChatService:
    """Coordinates run creation, model streaming, persistence, and replay."""

    def __init__(
        self,
        repo: ChatRepository,
        provider: ModelProvider,
        retention: timedelta,
    ) -> None:
        self._repo = repo
        self._provider = provider
        self._retention = retention
        # Per-run in-process generation locks. Only one generator runs per run at a
        # time, so overlapping same-key requests can never double-generate; a reconnect
        # acquires the lock after the interrupted generator was cancelled and resumes.
        self._generation_locks: dict[UUID, asyncio.Lock] = {}

    def _lock_for(self, run_id: UUID) -> asyncio.Lock:
        lock = self._generation_locks.get(run_id)
        if lock is None:
            lock = asyncio.Lock()
            self._generation_locks[run_id] = lock
        return lock

    async def create_run(
        self, context: RequestContext, session_id: UUID, content: str, idempotency_key: str
    ) -> CreatedRun:
        """Create (or return) a run, enforcing session ownership first."""
        if not await self._repo.session_exists(context, session_id):
            raise ApiError("NOT_FOUND", "Session not found.", False)
        return await self._repo.create_run(context, session_id, content, idempotency_key)

    async def require_run(self, context: RequestContext, run_id: UUID) -> None:
        """Raise 404 unless the caller owns ``run_id``. Called before streaming starts."""
        if await self._repo.get_run(context, run_id) is None:
            raise ApiError("NOT_FOUND", "Run not found.", False)

    async def stream_run(
        self, context: RequestContext, run_id: UUID, after: int
    ) -> AsyncIterator[StreamEvent]:
        """Yield a run's stream: replay missed events, then follow live generation.

        Generation is guarded by a ``queued -> streaming`` DB claim plus an in-process
        per-run lock, so two overlapping same-key requests can never run two generators:
        only the claim winner (or a reconnect that waits for the owner) runs ``_generate``.
        """
        run = await self._repo.get_run(context, run_id)
        if run is None:
            raise ApiError("NOT_FOUND", "Run not found.", False)
        if run.status in ("completed", "failed"):
            for event in await self._repo.list_events(context, run_id, after):
                yield event
            return
        # Replay whatever is already persisted beyond the client's cursor.
        cursor = after
        for event in await self._repo.list_events(context, run_id, cursor):
            yield event
            cursor = max(cursor, event.sequence)
        async with self._lock_for(run_id):
            run = await self._repo.get_run(context, run_id)
            if run is None:
                raise ApiError("NOT_FOUND", "Run not found.", False)
            if run.status in ("completed", "failed"):
                for event in await self._repo.list_events(context, run_id, cursor):
                    yield event
                return
            if run.status == "queued":
                if not await self._repo.claim_streaming(context, run_id):
                    # A concurrent same-key request won the claim; it owns generation.
                    return
                started = await self._repo.append_event(
                    run_id,
                    "run.started",
                    json.dumps({"messageId": str(run.assistant_message_id), "runId": str(run_id)}),
                )
                yield started
                cursor = max(cursor, started.sequence)
            # Replay anything persisted while we waited for the lock, then follow live.
            for event in await self._repo.list_events(context, run_id, cursor):
                yield event
                cursor = max(cursor, event.sequence)
            persisted_deltas = await self._repo.count_stream_events(context, run_id, "message.delta")
            async for event in self._generate(context, run_id, persisted_deltas):
                yield event

    async def replay(
        self, context: RequestContext, run_id: UUID, after: int
    ) -> AsyncIterator[StreamEvent]:
        """Replay only the persisted events after ``after`` (no live generation)."""
        run = await self._repo.get_run(context, run_id)
        if run is None:
            raise ApiError("NOT_FOUND", "Run not found.", False)
        for event in await self._repo.list_events(context, run_id, after):
            yield event

    async def list_sessions(self, context: RequestContext) -> list[ChatSession]:
        return await self._repo.list_sessions(context)

    async def list_messages(self, context: RequestContext, session_id: UUID) -> list[Message]:
        return await self._repo.list_messages(context, session_id)

    async def purge_expired_events(self) -> int:
        """Erase replay events older than the retention period (terminal runs only)."""
        before = datetime.now(UTC) - self._retention
        return await self._repo.purge_expired_events(before)

    async def _generate(
        self, context: RequestContext, run_id: UUID, skip: int
    ) -> AsyncIterator[StreamEvent]:
        run = await self._repo.get_run(context, run_id)
        if run is None:
            raise ApiError("NOT_FOUND", "Run not found.", False)
        message_id = run.assistant_message_id
        history = [
            ProviderMessage(role=m.role, content=m.content)
            for m in await self._repo.list_messages(context, run.session_id)
            if m.status == "completed"
        ]
        delta_index = 0
        try:
            async for delta in self._provider.stream(history):
                if delta_index < skip:
                    delta_index += 1
                    continue
                event = await self._repo.append_event(
                    run_id,
                    "message.delta",
                    json.dumps({"messageId": str(message_id), "text": delta.text}),
                )
                yield event
                delta_index += 1
            content = await self._collect_text(context, run_id)
            await self._repo.finalize_message(context, message_id, content)
            await self._repo.update_run_status(context, run_id, "completed")
            done = await self._repo.append_event(
                run_id, "run.completed", json.dumps({"messageId": str(message_id)})
            )
            yield done
        except ApiError as exc:
            async for event in self._fail(context, run_id, message_id, exc.code, exc.message):
                yield event
        except Exception as exc:  # noqa: BLE001 - surfaced to the client as a run.failed event
            logger.exception("run %s generation failed", run_id)
            async for event in self._fail(
                context, run_id, message_id, "STREAM_INTERRUPTED", str(exc)
            ):
                yield event

    async def _collect_text(self, context: RequestContext, run_id: UUID) -> str:
        events = await self._repo.list_events(context, run_id, after=0)
        parts: list[str] = []
        for event in events:
            if event.kind != "message.delta":
                continue
            try:
                text = json.loads(event.payload).get("text")
            except ValueError:
                continue
            if text:
                parts.append(text)
        return "".join(parts)

    async def _fail(
        self, context: RequestContext, run_id: UUID, message_id: UUID, code: str, detail: str
    ) -> AsyncIterator[StreamEvent]:
        await self._repo.finalize_message(context, message_id, "", status="failed")
        await self._repo.update_run_status(context, run_id, "failed")
        event = await self._repo.append_event(
            run_id,
            "run.failed",
            json.dumps(
                {
                    "messageId": str(message_id),
                    "error": {"code": code, "message": detail},
                }
            ),
        )
        yield event
