"""Sandbox-side typed MCP client — the plan's verbatim interface.

``McpClient.invoke(server, tool, input, grant) -> ToolResult`` is the sandbox
container's ONLY path to an MCP tool. It rejects disabled tools with
``ApiError("MCP_TOOL_DISABLED", ...)`` and otherwise calls the broker through
the grant channel (the existing ``POST /internal/v1/sandbox/tools/invoke``).

This mirrors the ``ToolClient`` pattern: the grant in a header, broker URL
from config.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class ToolResult:
    """The typed outcome of an MCP tool invocation."""

    success: bool
    data: Any | None = None
    error_code: str | None = None
    error_message: str | None = None


class ApiError(Exception):
    """A typed API error — the plan's verbatim MCP_TOOL_DISABLED rejection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class McpClient:
    """Calls the broker on behalf of the sandbox container for MCP tools.

    The sandbox has only the grant token — no application credentials.
    """

    def __init__(self, broker_url: str, grant_token: str) -> None:
        self._broker_url = broker_url.rstrip("/") + "/internal/v1/sandbox/tools/invoke"
        self._grant_token = grant_token

    def invoke(
        self, server: str, tool: str, input: dict[str, Any], grant: str,
        *, step_id: str | None = None,
    ) -> ToolResult:
        """Invoke ``tool`` on ``server`` with ``input``, authenticated by the grant.

        ``step_id`` MUST be the plan step id (the id the grant was signed over).
        When not provided, defaults to ``tool`` — compatible with plans where
        the step id equals the tool name (IMPORTANT #2).

        Raises ``ApiError("MCP_TOOL_DISABLED", ...)`` when the tool is
        DISABLED (the plan's verbatim test).
        """
        real_step_id = step_id if step_id is not None else tool
        body = json.dumps(
            {"step_id": real_step_id, "tool_id": tool, "input": input}
        ).encode("utf-8")
        req = Request(
            self._broker_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Tool-Grant": grant or self._grant_token,
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
            code = err.get("code", "SANDBOX_ERROR")
            message = err.get("message", str(exc))
            if code == "MCP_TOOL_DISABLED":
                raise ApiError(code, message)
            return ToolResult(
                success=False, error_code=code, error_message=message,
            )
        except ApiError:
            raise
        except Exception as exc:
            return ToolResult(
                success=False, error_code="SANDBOX_ERROR", error_message=str(exc),
            )
