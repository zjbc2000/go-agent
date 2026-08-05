"""Sandbox-side HTTP client that calls the FastAPI tool broker.

The sandbox container has ONLY the signed grant — no database credentials, no
application secrets, no KMS keys. Every tool invocation goes through the broker,
which authenticates the grant, reloads the run + approval from Postgres, and
executes the tool as the grant's user. The client maps broker HTTP errors to
typed tool results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ToolResult:
    """The typed outcome of a tool invocation through the broker."""

    success: bool
    data: Any | None = None
    error_code: str | None = None
    error_message: str | None = None


class ToolClient:
    """Calls the broker on behalf of an isolated sandbox container.

    The sandbox has only the grant token — no application credentials.
    """

    def __init__(self, broker_url: str, grant_token: str) -> None:
        self._broker_url = broker_url.rstrip("/") + "/internal/v1/sandbox/tools/invoke"
        self._grant_token = grant_token

    def invoke(self, step_id: str, tool_id: str, input: dict[str, Any]) -> ToolResult:
        """Send a tool invocation to the broker and return the typed result."""
        body = json.dumps({"step_id": step_id, "tool_id": tool_id, "input": input}).encode("utf-8")
        req = Request(
            self._broker_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Tool-Grant": self._grant_token,
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return ToolResult(success=True, data=payload.get("data"))
        except HTTPError as exc:
            try:
                error_body = json.loads(exc.read().decode("utf-8"))
            except Exception:
                error_body = {}
            err = error_body.get("error", {})
            return ToolResult(
                success=False,
                error_code=err.get("code", "SANDBOX_ERROR"),
                error_message=err.get("message", str(exc)),
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                error_code="SANDBOX_ERROR",
                error_message=str(exc),
            )
