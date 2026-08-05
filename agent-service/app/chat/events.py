"""Named SSE event encoding for durable run streams.

Each event is emitted as a named SSE frame with an ``id:`` line carrying the event's
persisted sequence number, so clients can resume after a ``Last-Event-ID`` cursor.
"""

from collections.abc import AsyncIterator
from typing import Any

from app.repositories.chat import StreamEvent


def format_sse(event: StreamEvent) -> str:
    """Encode a stream event as a named SSE frame:
    ``id: <sequence>\nevent: <kind>\ndata: <json>\n\n``.
    """
    return f"id: {event.sequence}\nevent: {event.kind}\ndata: {event.public_payload()}\n\n"


async def event_stream(source: Any) -> AsyncIterator[str]:
    """Format an async iterator of ``StreamEvent`` into SSE frames."""
    async for event in source:
        yield format_sse(event)
