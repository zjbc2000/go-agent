"""McpClient tests: the sandbox-side typed MCP client.

Plan's verbatim:
- test_disabled_tool_cannot_be_invoked
Plus: valid invoke passes grant + returns ToolResult.
IMPORTANT #2: McpClient uses the plan's step_id, not the tool name.
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
            client.invoke("test-server", "delete_everything", {}, "valid-grant",
                          step_id="delete_step")
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

        result = client.invoke("test-server", "echo.readonly", {"msg": "hi"}, "valid-grant",
                               step_id="plan-step-1")
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


# --- IMPORTANT #2: McpClient step_id binding ---

def test_mcp_client_sends_plan_step_id_not_tool_name():
    """IMPORTANT #2: the body must carry the plan's step_id, not the tool name.

    The broker resolves steps by plan step id and the grant is signed over
    step_ids (the plan's actual step ids). Sending tool_name as step_id
    would cause every call to be rejected SANDBOX_GRANT_INVALID.
    """
    client = McpClient("http://broker:8000", "plan-grant")

    with patch("worker.mcp_client.urlopen") as mock_urlopen:
        resp = type("FakeResp", (), {
            "read": lambda self: json.dumps({"data": {"echo": "ok"}}).encode("utf-8"),
        })()
        mock_urlopen.return_value.__enter__.return_value = resp

        client.invoke("example-server", "echo.readonly", {"msg": "hi"},
                      "plan-grant", step_id="mcp-step-3")

        # Verify the body has the plan step_id, not the tool name.
        call_args = mock_urlopen.call_args[0][0]
        body_text = call_args.data.decode("utf-8")
        body = json.loads(body_text)
        assert body["step_id"] == "mcp-step-3", (
            f"Expected step_id='mcp-step-3' (plan step), got {body.get('step_id')!r}"
        )
        assert body["tool_id"] == "echo.readonly", (
            f"Expected tool_id='echo.readonly', got {body.get('tool_id')!r}"
        )


def test_mcp_client_defaults_step_id_to_tool_when_not_provided():
    """When no step_id is given, the client defaults step_id to the tool name
    (backward-compatible with direct-tool-id plans where step_id == tool name).
    """
    client = McpClient("http://broker:8000", "grant")

    with patch("worker.mcp_client.urlopen") as mock_urlopen:
        resp = type("FakeResp", (), {
            "read": lambda self: json.dumps({"data": "ok"}).encode("utf-8"),
        })()
        mock_urlopen.return_value.__enter__.return_value = resp

        client.invoke("s", "echo.readonly", {}, "grant")  # no step_id arg

        call_args = mock_urlopen.call_args[0][0]
        body = json.loads(call_args.data.decode("utf-8"))
        assert body["step_id"] == "echo.readonly"  # defaults to tool name
