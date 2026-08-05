"""Immutable per-request identity context."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

UserRole = Literal["user", "admin"]


@dataclass(frozen=True)
class RequestContext:
    """Immutable identity context attached to every authenticated request.

    ``user_id`` is always derived from the verified JWT ``sub`` claim; it is never
    read from a JSON body or query parameter.
    """

    user_id: UUID
    role: UserRole
    request_id: str


def new_request_id() -> str:
    """Return a short, unique request identifier for tracing and idempotency."""
    return uuid4().hex
