"""Tests for the resident-owned ToolPort HTTP proxy."""

from __future__ import annotations

import httpx
import pytest
import respx

from ravn.adapters.tools.resident_proxy import load_resident_tools


@pytest.mark.asyncio
@respx.mock
async def test_load_resident_tools_proxies_metadata_and_execution() -> None:
    respx.get("http://127.0.0.1:7477/internal/tools").mock(
        return_value=httpx.Response(
            200,
            json={
                "tools": [
                    {
                        "name": "flock_status",
                        "description": "List flock members.",
                        "input_schema": {"type": "object", "properties": {}},
                        "required_permission": "tool:flock",
                        "parallelisable": False,
                    }
                ]
            },
        )
    )
    execute_route = respx.post("http://127.0.0.1:7477/internal/tools/flock_status").mock(
        return_value=httpx.Response(
            200,
            json={"tool_call_id": "call-1", "content": "members", "is_error": False},
        )
    )

    tools = await load_resident_tools(
        base_url="http://127.0.0.1:7477",
        connect_timeout_s=3.0,
    )
    result = await tools[0].execute({})

    assert tools[0].name == "flock_status"
    assert tools[0].description == "List flock members."
    assert tools[0].required_permission == "tool:flock"
    assert tools[0].parallelisable is False
    assert result.content == "members"
    assert result.tool_call_id == "call-1"
    assert result.is_error is False
    assert execute_route.calls.last.request.content == b'{"input":{}}'


@pytest.mark.asyncio
@respx.mock
async def test_resident_tool_proxy_returns_tool_error_on_http_failure() -> None:
    respx.get("http://127.0.0.1:7477/internal/tools").mock(
        return_value=httpx.Response(
            200,
            json={
                "tools": [
                    {
                        "name": "flock_status",
                        "description": "List flock members.",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ]
            },
        )
    )
    respx.post("http://127.0.0.1:7477/internal/tools/flock_status").mock(
        return_value=httpx.Response(503)
    )
    tools = await load_resident_tools(
        base_url="http://127.0.0.1:7477",
        connect_timeout_s=3.0,
    )

    result = await tools[0].execute({})

    assert result.is_error is True
    assert "Resident tool 'flock_status' failed" in result.content
