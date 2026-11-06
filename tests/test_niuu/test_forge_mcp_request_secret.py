"""The ``request_secret`` Forge MCP tool: schema, dispatch, REST paths and the hosted refusal."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from niuu.forge_mcp.models import ForgeApiError, SecretRequestError
from niuu.forge_mcp.rest_client import RestForgeClient
from niuu.forge_mcp.tools import TRANSCRIPT_ONLY_TOOLS, UnknownToolError
from tests.test_niuu.test_forge_mcp_tools import SELF, FakeHost, _feed_toolbox, _toolbox

REQUEST = {"name": "GITLAB_TOKEN", "purpose": "clone the repo", "target": "gitlab.example.com"}


class SecretHost(FakeHost):
    def __init__(self, error: Exception | None = None) -> None:
        super().__init__()
        self.error = error
        self.asked: list[tuple[str, str, str | None]] = []

    async def request_secret(self, name: str, purpose: str, target: str | None) -> dict[str, Any]:
        self.asked.append((name, purpose, target))
        if self.error is not None:
            raise self.error
        return {"status": "ready", "name": name, "path": f"/rt/secrets/{name}"}


def _tool() -> dict[str, Any]:
    toolbox, _, _ = _toolbox()
    return next(tool for tool in toolbox.list_tools() if tool["name"] == "request_secret")


class TestSchema:
    def test_name_pattern_and_required_fields(self) -> None:
        schema = _tool()["inputSchema"]
        assert schema["required"] == ["name", "purpose"]
        assert schema["properties"]["name"]["pattern"] == "^[A-Z_][A-Z0-9_]*$"
        assert schema["properties"]["name"]["maxLength"] == 255
        assert set(schema["properties"]) == {"name", "purpose", "target"}
        assert schema["additionalProperties"] is False

    def test_the_description_teaches_the_safe_use(self) -> None:
        description = _tool()["description"]
        assert "instead of ever asking them to paste a secret into the chat" in description
        assert "path" in description and "$(cat <path>)" in description
        assert "Never echo" in description
        assert "call again with the same name" in description


class TestDispatch:
    async def test_the_host_gets_the_arguments_and_the_result_is_a_path(self) -> None:
        toolbox, _, _ = _toolbox()
        host = SecretHost()
        toolbox._host = host

        result = await toolbox.call("request_secret", REQUEST)

        assert not result.is_error
        assert host.asked == [("GITLAB_TOKEN", "clone the repo", "gitlab.example.com")]
        assert result.structured == {
            "status": "ready",
            "name": "GITLAB_TOKEN",
            "path": "/rt/secrets/GITLAB_TOKEN",
        }

    async def test_target_is_optional(self) -> None:
        toolbox, _, _ = _toolbox()
        host = SecretHost()
        toolbox._host = host
        await toolbox.call("request_secret", {"name": "A_KEY", "purpose": "p"})
        assert host.asked == [("A_KEY", "p", None)]

    @pytest.mark.parametrize(
        "arguments",
        [
            {"name": "lower", "purpose": "p"},
            {"name": "1ABC", "purpose": "p"},
            {"name": "A-B", "purpose": "p"},
            {"name": "A" * 256, "purpose": "p"},
            {"name": "A_KEY", "purpose": ""},
            {"name": "A_KEY"},
            {"purpose": "p"},
            {"name": "A_KEY", "purpose": "p", "value": "oops"},
        ],
    )
    async def test_invalid_arguments_are_a_tool_error(self, arguments) -> None:
        toolbox, _, _ = _toolbox()
        host = SecretHost()
        toolbox._host = host
        result = await toolbox.call("request_secret", arguments)
        assert result.is_error and host.asked == []

    async def test_a_name_of_the_maximum_length_is_accepted(self) -> None:
        toolbox, _, _ = _toolbox()
        toolbox._host = SecretHost()
        result = await toolbox.call("request_secret", {"name": "A" * 255, "purpose": "p"})
        assert not result.is_error

    async def test_declined_or_late_is_a_tool_error_with_the_text(self) -> None:
        toolbox, _, _ = _toolbox()
        toolbox._host = SecretHost(SecretRequestError("the user declined to provide A_KEY."))
        result = await toolbox.call("request_secret", {"name": "A_KEY", "purpose": "p"})
        assert result.is_error
        assert result.text == "request_secret failed: the user declined to provide A_KEY."

    async def test_forge_errors_are_mapped(self) -> None:
        toolbox, _, _ = _toolbox()
        toolbox._host = SecretHost(ForgeApiError("no durable log", status=503))
        result = await toolbox.call("request_secret", {"name": "A_KEY", "purpose": "p"})
        assert result.is_error and "503" in result.text


class TestHostedRefusal:
    async def test_the_default_host_refuses_with_501(self) -> None:
        toolbox, _, _ = _toolbox()  # FakeHost keeps the port's default request_secret
        result = await toolbox.call("request_secret", REQUEST)
        assert result.is_error
        assert "session's own MCP" in result.text
        with pytest.raises(ForgeApiError) as raised:
            await FakeHost().request_secret("A_KEY", "p", None)
        assert raised.value.status == 501

    async def test_forges_hosted_endpoint_does_not_offer_it(self) -> None:
        toolbox, _ = _feed_toolbox()
        assert "request_secret" in TRANSCRIPT_ONLY_TOOLS
        assert "request_secret" not in [tool["name"] for tool in toolbox.list_tools()]
        with pytest.raises(UnknownToolError):
            await toolbox.call("request_secret", REQUEST)


class TestRestClientPaths:
    def _client(self, seen: list[httpx.Request]) -> RestForgeClient:
        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"id": "r-1", "status": "pending"})

        async def factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://f")

        return RestForgeClient(
            http_client=factory, headers={"Authorization": "Bearer t"}, timeout_s=5
        )

    async def test_each_call_hits_its_secret_requests_path(self) -> None:
        seen: list[httpx.Request] = []
        client = self._client(seen)
        base = f"/api/v1/forge/sessions/{SELF}/secret-requests"

        await client.create_secret_request(SELF, {"name": "A_KEY", "purpose": "p"})
        await client.get_secret_request(SELF, "r-1")
        await client.claim_secret_request(SELF, "r-1")
        await client.cancel_secret_request(SELF, "r-1")

        assert [(r.method, r.url.path) for r in seen] == [
            ("POST", base),
            ("GET", f"{base}/r-1"),
            ("POST", f"{base}/r-1/claim"),
            ("POST", f"{base}/r-1/cancel"),
        ]
        assert json.loads(seen[0].content) == {"name": "A_KEY", "purpose": "p"}
        assert all(r.headers["authorization"] == "Bearer t" for r in seen)

    async def test_ids_cannot_escape_the_path(self) -> None:
        seen: list[httpx.Request] = []
        await self._client(seen).get_secret_request(SELF, "../claim")
        assert "/../" not in seen[0].url.raw_path.decode()

    async def test_an_http_error_is_a_forge_error_with_its_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"detail": "request is not fulfilled"})

        async def factory() -> httpx.AsyncClient:
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://f")

        client = RestForgeClient(http_client=factory, headers={}, timeout_s=5)
        with pytest.raises(ForgeApiError) as raised:
            await client.claim_secret_request(SELF, "r-1")
        assert raised.value.status == 409 and "not fulfilled" in raised.value.detail
