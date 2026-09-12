"""MCP executor seam: the broker dispatches MCP tool invocations through this
injectable interface.

``McpExecutor`` is a Protocol so tests can substitute a deterministic fake.
The real executor (for later wiring) runs the pinned MCP image under the
Task-3 ``SandboxContainerConfig`` security policy; the MVP documents the
transport and uses a fake for all tests (resource-constrained environment).

``FakeMcpExecutor`` returns predetermined results keyed by tool_id, suitable
for deterministic broker tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolResult:
    """The typed outcome of a tool invocation through the MCP executor."""

    success: bool
    data: Any | None = None
    error_code: str | None = None
    error_message: str | None = None


class McpExecutor(Protocol):
    """Injectable MCP tool execution seam.

    The broker calls this for steps whose tool is a registered MCP tool.
    The real transport runs the pinned MCP OCI image under the Task-3
    container security policy; the fake returns predetermined results.

    ``registration`` carries the DB lookup result (server_image, schemas,
    mutability, server_id) so the real executor can launch the correct image
    without the broker having to understand transport details (IMPORTANT #2).
    """

    async def execute(
        self,
        tool_id: str,
        input: dict[str, Any],
        registration: dict[str, Any],
    ) -> ToolResult: ...


class FakeMcpExecutor:
    """Deterministic fake returning pre-programmed results keyed by tool_id."""

    def __init__(self, results: dict[str, ToolResult] | None = None) -> None:
        self._results: dict[str, ToolResult] = results or {}

    def set_result(self, tool_id: str, result: ToolResult) -> None:
        """Register a predetermined result for ``tool_id``."""
        self._results[tool_id] = result

    async def execute(
        self,
        tool_id: str,
        input: dict[str, Any],
        registration: dict[str, Any],
    ) -> ToolResult:
        """Return the pre-registered result, or a fallback success."""
        return self._results.get(
            tool_id,
            ToolResult(
                success=True,
                data={"echo": f"fake-result-for-{tool_id}"},
            ),
        )
