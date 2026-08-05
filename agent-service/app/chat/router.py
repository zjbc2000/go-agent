"""Internal chat API routes: run creation/streaming, SSE replay, and message listing.

These routes are reachable only by the BFF proxy, which forwards the end-user JWT in
``Authorization`` and the shared internal token in ``X-Internal-Token``. The browser
never calls the Python service directly.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.chat import events
from app.chat.deps import get_chat_service, get_request_context, require_internal_token
from app.chat.service import ChatService
from app.core.context import RequestContext
from app.core.errors import ApiError
from app.repositories.chat import CreatedRun

router = APIRouter()

_STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def _after_cursor(last_event_id: str | None) -> int:
    """Parse the SSE ``Last-Event-ID`` cursor; absent/invalid means replay from zero."""
    if last_event_id is None:
        return 0
    try:
        return int(last_event_id)
    except ValueError:
        return 0


@router.post("/internal/v1/sessions/{session_id}/runs")
async def create_run(
    session_id: UUID,
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    """Create (or resume) a run and stream its events as named SSE frames."""
    try:
        body = await request.json()
    except ValueError:
        raise ApiError("VALIDATION_FAILED", "Invalid JSON body.", False) from None
    content = body.get("content")
    idempotency_key = body.get("idempotency_key")
    if not isinstance(content, str) or not content.strip():
        raise ApiError("VALIDATION_FAILED", "content is required.", False)
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ApiError("VALIDATION_FAILED", "idempotency_key is required.", False)

    created: CreatedRun = await service.create_run(context, session_id, content, idempotency_key)
    after = _after_cursor(request.headers.get("last-event-id"))
    return StreamingResponse(
        events.event_stream(service.stream_run(context, created.run_id, after)),
        media_type="text/event-stream",
        headers=_STREAM_HEADERS,
    )


@router.get("/internal/v1/runs/{run_id}/events")
async def stream_run_events(
    run_id: UUID,
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    """Replay a run's persisted events after the ``Last-Event-ID`` cursor."""
    # Ownership is checked before the stream starts so a 404 surfaces as JSON,
    # not as an error mid-stream.
    await service.require_run(context, run_id)
    after = _after_cursor(request.headers.get("last-event-id"))
    return StreamingResponse(
        events.event_stream(service.replay(context, run_id, after)),
        media_type="text/event-stream",
        headers=_STREAM_HEADERS,
    )


@router.get("/internal/v1/sessions")
async def list_sessions(
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: ChatService = Depends(get_chat_service),
) -> list[dict]:
    """List the caller's sessions, most recently active first."""
    sessions = await service.list_sessions(context)
    return [
        {
            "id": str(s.id),
            "title": s.title,
            "createdAt": s.created_at.isoformat(),
            "lastMessageAt": s.last_message_at.isoformat(),
        }
        for s in sessions
    ]


@router.get("/internal/v1/sessions/{session_id}/messages")
async def list_messages(
    session_id: UUID,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    service: ChatService = Depends(get_chat_service),
) -> list[dict]:
    """List a session's decrypted messages for the caller."""
    messages = await service.list_messages(context, session_id)
    return [
        {
            "id": str(m.id),
            "sessionId": str(m.session_id),
            "role": m.role,
            "content": m.content,
            "status": m.status,
            "createdAt": m.created_at.isoformat(),
        }
        for m in messages
    ]
