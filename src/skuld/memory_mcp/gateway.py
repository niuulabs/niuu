"""The memory gateway as the broker calls it for the built-in ``memory`` MCP server.

``GET {gateway_url}/memory/tools`` answers ``{"instructions", "tools"}`` and
``POST {gateway_url}/memory/tools/{name}`` runs one tool and answers an MCP
``CallToolResult``. Both carry ``Authorization: Bearer <the session's forge_session
credential>``: the credential lives here, in the broker, and never reaches the agent.
``GET {gateway_url}/memory/context-pack`` answers the session's launch context
(``{"text", "budget_tokens", "used_tokens", "omitted"}``), fetched once per broker start.
Every failure is a typed error naming the gateway and what went wrong; nothing here
turns a failure into an empty tool list or an empty result.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

TOOLS_SEGMENT = "memory/tools"
CONTEXT_PACK_SEGMENT = "memory/context-pack"


class MemoryGatewayError(Exception):
    """The gateway could not be reached, refused the call or answered nonsense."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class MemoryCredentialRefusedError(MemoryGatewayError):
    """The gateway answered 401 or 403: it did not accept the session's credential."""


class UnknownMemoryToolError(MemoryGatewayError):
    """The gateway answered 404 for a tool name."""


class MemoryContextPackError(MemoryGatewayError):
    """The session's context pack could not be fetched; the broker must not start without it."""


@dataclass(frozen=True)
class ContextPack:
    """What the gateway chose to tell a session at launch.

    ``omitted`` lists what did not fit the budget. An empty ``text`` with ``omitted``
    entries is a real pack (everything was too big for the budget), and an empty
    ``text`` with nothing omitted is a project the gateway holds nothing about yet.
    """

    text: str
    budget_tokens: int
    used_tokens: int
    omitted: tuple[Any, ...]


def _detail(response: httpx.Response) -> str:
    """The gateway's own words for a failure: its ``detail`` field, else the body."""
    text = response.text.strip()
    try:
        body = response.json()
    except ValueError:
        return text
    if isinstance(body, dict) and body.get("detail"):
        return str(body["detail"])
    return text


class MemoryGatewayClient:
    """Calls the memory gateway as one session (its credential, its timeout)."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout_s = timeout_s
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return self._base_url

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        tool: str = "",
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}/{path}"
        try:
            response = await self._http().request(
                method,
                url,
                json=body,
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                timeout=httpx.USE_CLIENT_DEFAULT if timeout_s is None else timeout_s,
            )
        except httpx.HTTPError as exc:
            raise MemoryGatewayError(
                f"the memory gateway at {self._base_url} could not be reached "
                f"({type(exc).__name__}: {exc}); check memory.gateway_url "
                "(SKULD__MEMORY__GATEWAY_URL) and that the gateway is running"
            ) from exc
        if response.status_code in (401, 403):
            raise MemoryCredentialRefusedError(
                f"the memory gateway at {self._base_url} refused this session's memory "
                f"credential (HTTP {response.status_code}): {_detail(response)}. The "
                "credential may lack the forge:memory scope, or its session or launch ended",
                status=response.status_code,
            )
        if response.status_code == 404 and tool:
            raise UnknownMemoryToolError(f"unknown memory tool: {tool}", status=404)
        if response.status_code >= 400:
            raise MemoryGatewayError(
                f"the memory gateway at {self._base_url} answered "
                f"{response.status_code}: {_detail(response)}",
                status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MemoryGatewayError(
                f"the memory gateway at {self._base_url} sent invalid JSON: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise MemoryGatewayError(
                f"the memory gateway at {self._base_url} sent a {type(payload).__name__}, "
                "expected a JSON object"
            )
        return payload

    async def list_tools(self) -> dict[str, Any]:
        """``{"instructions": str, "tools": [...]}``; a listing without both is an error."""
        payload = await self._request("GET", TOOLS_SEGMENT)
        instructions = payload.get("instructions")
        tools = payload.get("tools")
        if not isinstance(instructions, str) or not instructions.strip():
            raise MemoryGatewayError(
                f"the memory gateway at {self._base_url} listed its tools without instructions"
            )
        if not isinstance(tools, list) or not tools:
            raise MemoryGatewayError(
                f"the memory gateway at {self._base_url} listed no tools; a gateway with "
                "nothing to offer is a broken gateway, not an empty memory"
            )
        return {"instructions": instructions, "tools": tools}

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """The gateway's MCP ``CallToolResult`` for one tool call."""
        quoted = urllib.parse.quote(name, safe="")
        payload = await self._request(
            "POST", f"{TOOLS_SEGMENT}/{quoted}", body=arguments, tool=name
        )
        if not isinstance(payload.get("content"), list):
            raise MemoryGatewayError(
                f"the memory gateway at {self._base_url} answered tool {name!r} without a "
                "content list"
            )
        return payload

    async def context_pack(self, *, timeout_s: float) -> ContextPack:
        """The session's launch context; a pack missing a field is an error, not a pack."""
        payload = await self._request("GET", CONTEXT_PACK_SEGMENT, timeout_s=timeout_s)
        text = payload.get("text")
        omitted = payload.get("omitted")
        budget = payload.get("budget_tokens")
        used = payload.get("used_tokens")
        if not isinstance(text, str):
            raise MemoryGatewayError(
                f"the memory gateway at {self._base_url} answered the context pack without "
                "a text string"
            )
        if not isinstance(omitted, list):
            raise MemoryGatewayError(
                f"the memory gateway at {self._base_url} answered the context pack without "
                "an omitted list, so what was left out cannot be reported"
            )
        for name, value in (("budget_tokens", budget), ("used_tokens", used)):
            if isinstance(value, bool) or not isinstance(value, int):
                raise MemoryGatewayError(
                    f"the memory gateway at {self._base_url} answered the context pack "
                    f"without an integer {name}"
                )
        return ContextPack(
            text=text,
            budget_tokens=budget,
            used_tokens=used,
            omitted=tuple(omitted),
        )
