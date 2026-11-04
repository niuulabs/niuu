"""Two facades never ask each other in a loop, and a node that cannot answer is named
(the promotion-twelve rollback, 2026-10-31)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import AllowAllIdentityAdapter
from niuu.adapters.inbound.rest_volundr import OWNER_PROBE_HEADER, create_volundr_router
from niuu.domain.guild_membership import GuildMembership
from tests.support.forge_session import OWNER, build_forge_app
from tests.test_niuu.test_rest_volundr import StubInstanceService, _instance

HUB = "https://hub.example"
MEMBER = "https://member.example"
SPARK = "https://spark.example"
ZERO = "00000000-0000-4000-8000-000000000000"
# Mini mode: one tenant, and a facade-to-facade probe (bearer only) is "default" too.
TENANT_ALL = "default"
USER = {"x-auth-user-id": OWNER, "x-auth-tenant": TENANT_ALL}
MEMBERSHIP = GuildMembership(guild_url=HUB, instance=None)
LOOP_LIMIT = 3


def _local(name: str):
    return _instance(
        name,
        base_url=f"embedded://{name}",
        tenant_id=TENANT_ALL,
        is_default=True,
        config={"transport": "embedded"},
    )


def _facade(instances, *, membership: GuildMembership | None = None) -> FastAPI:
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_volundr_router(
            StubInstanceService(instances),
            embedded_forge_app=build_forge_app().app,
            guild_membership=membership,
            session_owner_ttl_seconds=3600.0,
        )
    )
    return app


def _route_to(app: FastAPI):
    """A respx side effect that serves the request from another in-process facade."""

    calls = 0

    async def serve(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        # Fail fast instead of hanging if two facades start asking each other.
        assert calls <= LOOP_LIMIT, f"facades loop: {calls} calls to {request.url.host}"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=str(request.url.copy_with(path="/"))
        ) as client:
            return await client.send(
                httpx.Request(
                    request.method, request.url, headers=request.headers, content=request.content
                )
            )

    return serve


class TestNoLoop:
    """Hub and member list each other, the case where a probe that fanned out would loop."""

    def _pair(self):
        hub = _facade([_local("hub"), _instance("member", base_url=MEMBER, tenant_id=TENANT_ALL)])
        member = _facade(
            [_local("member"), _instance("hub", base_url=HUB, tenant_id=TENANT_ALL)],
            membership=MEMBERSHIP,
        )
        to_member = respx.route(host="member.example").mock(side_effect=_route_to(member))
        to_hub = respx.route(host="hub.example").mock(side_effect=_route_to(hub))
        return hub, member, to_member, to_hub

    @respx.mock
    async def test_from_the_hub_an_unknown_session_is_one_probe(self) -> None:
        hub, _, to_member, to_hub = self._pair()

        response = TestClient(hub).get(f"/api/v1/forge/sessions/{ZERO}", headers=USER)

        assert response.status_code == 404
        assert response.json()["detail"] == f"Session not found: {ZERO}"
        # One probe of the member, marked; the member answers from its own store and
        # neither probes back nor asks the Guild (the hub) who owns it.
        assert to_member.call_count == 1
        assert to_member.calls.last.request.headers[OWNER_PROBE_HEADER] == "1"
        assert not to_hub.called

    @respx.mock
    async def test_from_the_member_it_is_one_probe_and_one_owner_question(self) -> None:
        _, member, to_member, to_hub = self._pair()

        response = TestClient(member).get(f"/api/v1/forge/sessions/{ZERO}", headers=USER)

        assert response.status_code == 404
        # The member's probe of the hub and its owner question to the Guild (the hub),
        # both marked; the hub answers each from its own store and never asks back.
        assert to_hub.call_count == 2
        assert all(call.request.headers[OWNER_PROBE_HEADER] == "1" for call in to_hub.calls)
        assert not to_member.called


class TestUnansweredNode:
    @respx.mock
    async def test_an_unreachable_node_keeps_the_answer_502_and_is_named(self) -> None:
        hub = _facade([_local("hub"), _instance("spark", base_url=SPARK, tenant_id=TENANT_ALL)])
        respx.route(host="spark.example").mock(side_effect=httpx.ConnectError("refused"))

        response = TestClient(hub).get(f"/api/v1/forge/sessions/{ZERO}", headers=USER)

        # A sleeping node must never make a live session read as gone.
        assert response.status_code == 502
        (failure,) = json.loads(response.headers["X-Niuu-Source-Failures"])
        assert failure["instanceId"] == "spark"

    @respx.mock
    async def test_a_node_that_rejects_us_is_named_as_rejected(self) -> None:
        hub = _facade([_local("hub"), _instance("spark", base_url=SPARK, tenant_id=TENANT_ALL)])
        respx.route(host="spark.example").mock(return_value=httpx.Response(401))

        response = TestClient(hub).get(f"/api/v1/forge/sessions/{ZERO}", headers=USER)

        assert response.status_code == 502
        (failure,) = json.loads(response.headers["X-Niuu-Source-Failures"])
        assert (failure["status"], failure["error"]) == ("rejected", "HTTP 401")
