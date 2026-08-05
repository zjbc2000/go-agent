"""Internal MCP registry API routes.

Reachable only by the BFF proxy (``X-Internal-Token`` + user JWT).
- ``POST /internal/v1/mcp/servers`` — register an MCP server.
- ``GET /internal/v1/mcp/servers/{id}/tools`` — list the user's enabled tools.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.chat.deps import get_request_context, require_internal_token
from app.core.context import RequestContext
from app.core.errors import ApiError
from app.mcp.registry import McpRegistry

router = APIRouter()


def get_mcp_registry(request: Request) -> McpRegistry:
    return request.app.state.mcp_registry


@router.post("/internal/v1/mcp/servers")
async def register_server(
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    registry: McpRegistry = Depends(get_mcp_registry),
) -> dict:
    """Register an MCP server from a manifest."""
    try:
        body = await request.json()
    except ValueError:
        raise ApiError("VALIDATION_FAILED", "Invalid JSON body.", False) from None
    if not isinstance(body, dict):
        raise ApiError("VALIDATION_FAILED", "Request body must be an object.", False)
    result = await registry.register_mcp(context, body)
    return {"server": result}


@router.get("/internal/v1/mcp/servers/{server_id}/tools")
async def discover_tools(
    server_id: UUID,
    request: Request,
    _: None = Depends(require_internal_token),
    context: RequestContext = Depends(get_request_context),
    registry: McpRegistry = Depends(get_mcp_registry),
) -> dict:
    """List the user's enabled tools for a server."""
    tools = await registry.discover_tools(context, server_id)
    return {"tools": tools}
