"""McpClient tests: the sandbox-side typed MCP client.

Plan's verbatim:
- test_disabled_tool_cannot_be_invoked
Plus: valid invoke passes grant + returns ToolResult.
"""

import json
from unittest.mock import patch

import pytest
from worker.mcp_client import ApiError, McpClient

# --- Plan's verbatim ---

def test_disabled_tool_cannot_be_invoked():
    """Plan's verbatim: disabled tool → MCP_TOOL_DISABLED."""
    client = McpClient("http://broker:8000", "valid-grant")

    with patch("worker.mcp_client.urlopen") as mock_urlopen:
        # Simulate a 403 response with MCP_TOOL_DISABLED.
        from urllib.error import HTTPError
        resp = type("FakeResp", (), {
            "read": lambda self: json.dumps({
                "error": {"code": "MCP_TOOL_DISABLED", "message": "Tool is disabled."}
            }).encode("utf-8"),
        })()
        mock_urlopen.side_effect = HTTPError(
            "http://broker:8000/internal/v1/sandbox/tools/invoke",
            403, "Forbidden", None, resp,
        )

        with pytest.raises(ApiError) as exc_info:
            client.invoke("test-server", "delete_everything", {}, "valid-grant")
        assert exc_info.value.code == "MCP_TOOL_DISABLED"


def test_valid_invoke_returns_tool_result():
    """A valid MCP tool call returns a ToolResult."""
    client = McpClient("http://broker:8000", "valid-grant")

    with patch("worker.mcp_client.urlopen") as mock_urlopen:
        resp = type("FakeResp", (), {
            "read": lambda self: json.dumps(
                {"data": {"echo": "hello"}}
            ).encode("utf-8"),
        })()
        mock_urlopen.return_value.__enter__.return_value = resp

        result = client.invoke("test-server", "echo.readonly", {"msg": "hi"}, "valid-grant")
        assert result.success
        assert result.data == {"echo": "hello"}


def test_mcp_client_uses_grant_header():
    """The client passes the supplied grant in X-Tool-Grant header."""
    client = McpClient("http://broker:8000", "default-grant")

    with patch("worker.mcp_client.urlopen") as mock_urlopen:
        resp = type("FakeResp", (), {
            "read": lambda self: json.dumps({"data": "ok"}).encode("utf-8"),
        })()
        mock_urlopen.return_value.__enter__.return_value = resp

        # Use a specific grant argument.
        client.invoke("s", "t", {}, "override-grant")
        call_args = mock_urlopen.call_args[0][0]
        assert call_args.headers["X-tool-grant"] == "override-grant"
