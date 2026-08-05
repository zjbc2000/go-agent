"""Minimal MCP-like server exposing one read-only tool: echo.readonly.

This file is the TEST FIXTURE source — it is NOT built or run in this
resource-constrained environment. The manifest.json declares the tool and
its schemas; the registry tests use the manifest as the admission fixture.
"""

import json
import sys


def handle_request(request: dict) -> dict:
    method = request.get("method", "")
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {
                "tools": [
                    {
                        "name": "echo.readonly",
                        "description": "Returns a fixed echo response.",
                        "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}}},
                    }
                ]
            },
        }
    if method == "tools/call":
        params = request.get("params", {})
        tool_name = params.get("name", "")
        if tool_name == "echo.readonly":
            return {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": {"content": [{"type": "text", "text": "echo: read-only"}]},
            }
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "error": {"code": -32601, "message": "Method not found"},
    }


def main() -> None:
    """Simple stdio MCP-like loop: read one line, process, write one line."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_request(req)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
