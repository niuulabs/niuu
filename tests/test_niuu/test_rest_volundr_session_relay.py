"""LEX-1710: a session messages its parent or project on another Guild node.

The session's own node verifies its credential and relays the message without it,
attesting the sender in the body; the owning node checks the relation and delivers.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from identity.adapters.identity import AllowAllIdentityAdapter
from niuu.adapters.inbound.rest_volundr import create_volundr_router
from niuu.domain.guild_membership import GuildMembership
from tests.support.forge_session import OWNER, TENANT, bearer, build_forge_app
from tests.test_niuu.test_rest_volundr import StubInstanceService, _instance
from volundr.domain.projects import SessionCoordination

REMOTE = "https://remote.example"
GUILD = "https://guild.example"
PROJECT = uuid4()
TARGET = "00000000-0000-4000-8000-0000000000aa"
DELIVERED = {"status": "delivered", "delivery": "delivered", "request_id": "r1"}


async def _guild(
    *,
    membership: GuildMembership | None = None,
    with_remote: bool = True,
    feedless_recheck_seconds: float | None = None,
):
    forge = build_forge_app()
    local = _instance(
        "local",
        base_url="embedded://local-forge",
        tenant_id=TENANT,
        is_default=True,
        config={"transport": "embedded"},
    )
    instances = [local]
    if with_remote:
        instances.append(_instance("remote", base_url=REMOTE, tenant_id=TENANT))
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_volundr_router(
            StubInstanceService(instances),
            embedded_forge_app=forge.app,
            guild_membership=membership,
            feedless_recheck_seconds=feedless_recheck_seconds,
        )
    )
    sender = await forge.session(
        "voice",
        workload_config={"forge_mcp": {"grants": ["message"]}},
        coordination=SessionCoordination(project_id=PROJECT, role="worker"),
    )
    credential = await forge.start(sender)
    return TestClient(app), sender, bearer(credential.token)


class TestRelayToAnotherNode:
    @respx.mock
    async def test_a_registry_node_gets_the_relay_without_the_session_credential(self):
        client, sender, auth = await _guild()
        respx.get(f"{REMOTE}/api/v1/forge/sessions/{TARGET}").mock(
            return_value=Response(200, json={"id": TARGET})
        )
        relayed = respx.post(f"{REMOTE}/api/v1/forge/sessions/{TARGET}/messages/relayed").mock(
            return_value=Response(200, json=DELIVERED)
        )

        response = client.post(
            f"/api/v1/forge/sessions/{TARGET}/messages",
            json={"content": "hotfix landed", "request_id": "r1"},
            headers=auth,
        )

        assert response.status_code == 200 and response.json()["delivery"] == "delivered"
        request = relayed.calls.last.request
        assert "authorization" not in request.headers
        body = json.loads(request.content)
        assert body["content"] == "hotfix landed" and body["request_id"] == "r1"
        assert body["sender"] == {
            "session_id": str(sender.id),
            "session_name": "voice",
            "instance_name": "Instance local",
            "project_id": str(PROJECT),
            "parent_session_id": None,
        }

    @respx.mock
    async def test_naming_the_owning_node_relays_there(self):
        """The tool's instance_id names the target's node; it is not a local-scope clash."""
        client, _, auth = await _guild()
        relayed = respx.post(f"{REMOTE}/api/v1/forge/sessions/{TARGET}/messages/relayed").mock(
            return_value=Response(200, json=DELIVERED)
        )
        respx.get(f"{REMOTE}/api/v1/forge/sessions/{TARGET}").mock(
            return_value=Response(200, json={"id": TARGET})
        )

        response = client.post(
            f"/api/v1/forge/sessions/{TARGET}/messages",
            params={"instance_id": "remote"},
            json={"content": "hi"},
            headers=auth,
        )

        assert response.status_code == 200 and response.json()["delivery"] == "delivered"
        assert relayed.called

    async def test_naming_an_unknown_node_is_a_clear_404(self):
        client, _, auth = await _guild()

        response = client.post(
            f"/api/v1/forge/sessions/{TARGET}/messages",
            params={"instance_id": "elsewhere"},
            json={"content": "hi"},
            headers=auth,
        )

        assert response.status_code == 404
        assert "elsewhere" in response.json()["detail"]

    @respx.mock
    async def test_a_session_no_registry_node_knows_goes_to_the_hosts_guild(self):
        membership = GuildMembership(guild_url=GUILD, instance=None)
        client, _, auth = await _guild(membership=membership, with_remote=False)
        relayed = respx.post(f"{GUILD}/api/v1/forge/sessions/{TARGET}/messages/relayed").mock(
            return_value=Response(200, json=DELIVERED)
        )

        response = client.post(
            f"/api/v1/forge/sessions/{TARGET}/messages", json={"content": "hi"}, headers=auth
        )

        assert response.status_code == 200
        assert "authorization" not in relayed.calls.last.request.headers

    async def test_without_a_guild_the_answer_says_how_to_reach_other_nodes(self):
        client, _, auth = await _guild(with_remote=False)

        response = client.post(
            f"/api/v1/forge/sessions/{TARGET}/messages", json={"content": "hi"}, headers=auth
        )

        assert response.status_code == 404
        assert "set guild.url" in response.json()["detail"]

    @respx.mock
    async def test_the_owning_nodes_refusal_keeps_its_reason(self):
        client, _, auth = await _guild()
        respx.get(f"{REMOTE}/api/v1/forge/sessions/{TARGET}").mock(
            return_value=Response(200, json={"id": TARGET})
        )
        reason = "Session x is not in the sender's project (p) and is not its parent"
        respx.post(f"{REMOTE}/api/v1/forge/sessions/{TARGET}/messages/relayed").mock(
            return_value=Response(403, json={"detail": reason})
        )

        response = client.post(
            f"/api/v1/forge/sessions/{TARGET}/messages", json={"content": "hi"}, headers=auth
        )

        assert response.status_code == 403
        assert reason in response.json()["detail"]

    @respx.mock
    async def test_a_node_that_requires_a_login_is_refused_clearly(self):
        client, _, auth = await _guild()
        respx.get(f"{REMOTE}/api/v1/forge/sessions/{TARGET}").mock(
            return_value=Response(401, json={"detail": "Not authenticated"})
        )

        response = client.post(
            f"/api/v1/forge/sessions/{TARGET}/messages", json={"content": "hi"}, headers=auth
        )

        assert response.status_code == 403
        assert "requires a login" in response.json()["detail"]
        assert "node credential" in response.json()["detail"]

    async def test_a_session_credential_cannot_call_the_relay_route(self):
        client, _, auth = await _guild()

        response = client.post(
            f"/api/v1/forge/sessions/{TARGET}/messages/relayed",
            json={"content": "hi", "sender": {}},
            headers=auth,
        )

        assert response.status_code == 403


class TestMalformedInstanceId:
    """A malformed instance_id is the caller's mistake: 400 with the reason, not a 500."""

    async def test_a_malformed_instance_id_answers_400(self):
        from niuu.ports.instances import InvalidInstanceIdError

        class Registry(StubInstanceService):
            async def get_visible(self, principal, instance_id):
                raise InvalidInstanceIdError(f"Invalid instance id {instance_id!r}")

        forge = build_forge_app()
        local = _instance(
            "local",
            base_url="embedded://local-forge",
            tenant_id=TENANT,
            is_default=True,
            config={"transport": "embedded"},
        )
        app = FastAPI()
        app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
        app.include_router(create_volundr_router(Registry([local]), embedded_forge_app=forge.app))

        response = TestClient(app).post(
            f"/api/v1/forge/sessions/{TARGET}/messages",
            params={"instance_id": "c3d1d72f-mini-m6"},
            json={"content": "hi"},
        )

        assert response.status_code == 400
        assert "c3d1d72f-mini-m6" in response.json()["detail"]


class TestMemberToHub:
    """LEX-1738: a member reaches the hub's sessions; the hub answers its own first."""

    @respx.mock
    async def test_the_local_node_answers_for_its_own_session_without_asking_others(self):
        client, sender, _ = await _guild()
        remote = respx.get(f"{REMOTE}/api/v1/forge/sessions/{sender.id}").mock(
            return_value=Response(404, json={"detail": "Session not found"})
        )

        user = {"x-auth-user-id": OWNER, "x-auth-tenant": TENANT}
        response = client.get(f"/api/v1/forge/sessions/{sender.id}", headers=user)

        assert response.status_code == 200
        assert response.json()["instance_id"] == "local"
        assert not remote.called

    @respx.mock
    async def test_naming_a_node_this_registry_lacks_relays_to_the_guild(self):
        membership = GuildMembership(guild_url=GUILD, instance=None)
        client, _, auth = await _guild(membership=membership, with_remote=False)
        relayed = respx.post(f"{GUILD}/api/v1/forge/sessions/{TARGET}/messages/relayed").mock(
            return_value=Response(200, json=DELIVERED)
        )

        response = client.post(
            f"/api/v1/forge/sessions/{TARGET}/messages",
            params={"instance_id": "56499f00-02b3-4d80-8137-09d6d3238056"},
            json={"content": "hi"},
            headers=auth,
        )

        assert response.status_code == 200 and response.json()["delivery"] == "delivered"
        assert relayed.called

    @respx.mock
    async def test_a_members_read_of_a_hub_session_names_its_owner(self):
        membership = GuildMembership(guild_url=GUILD, instance=None)
        client, _, auth = await _guild(membership=membership, with_remote=False)
        respx.get(f"{GUILD}/api/v1/forge/sessions/{TARGET}").mock(
            return_value=Response(
                200, json={"id": TARGET, "instance_id": "hub-1", "instance_name": "Thor"}
            )
        )

        response = client.get(f"/api/v1/forge/sessions/{TARGET}", headers=auth)

        assert response.status_code == 404
        detail = response.json()["detail"]
        assert "owned by Thor (hub-1)" in detail
        assert "send_message" in detail

    @respx.mock
    async def test_a_node_without_the_feed_is_left_out_quietly(self):
        client, _, _ = await _guild()
        respx.get(f"{REMOTE}/api/v1/forge/notifications").mock(
            return_value=Response(404, json={"detail": "Not Found"})
        )

        response = client.get(
            "/api/v1/forge/notifications",
            params={"all_instances": "true"},
            headers={"x-auth-user-id": OWNER, "x-auth-tenant": TENANT},
        )

        assert response.status_code == 200
        assert "remote" not in response.headers.get("X-Forge-Unavailable-Instances", "")
        assert response.headers["X-Forge-Feedless-Instances"] == "remote"

    @respx.mock
    async def test_a_node_without_the_feed_is_not_asked_again_until_its_recheck(self):
        client, _, _ = await _guild(feedless_recheck_seconds=3600.0)
        feed = respx.get(f"{REMOTE}/api/v1/forge/notifications").mock(
            return_value=Response(404, json={"detail": "Not Found"})
        )
        user = {"x-auth-user-id": OWNER, "x-auth-tenant": TENANT}

        for _ in range(3):
            response = client.get(
                "/api/v1/forge/notifications", params={"all_instances": "true"}, headers=user
            )
            assert response.status_code == 200

        assert feed.call_count == 1

    @respx.mock
    async def test_an_unknown_node_named_through_the_guild_is_a_clean_404(self):
        membership = GuildMembership(guild_url=GUILD, instance=None)
        client, _, auth = await _guild(membership=membership, with_remote=False)
        relayed = respx.post(f"{GUILD}/api/v1/forge/sessions/{TARGET}/messages/relayed").mock(
            return_value=Response(404, json={"detail": "Forge instance nowhere not found"})
        )

        response = client.post(
            f"/api/v1/forge/sessions/{TARGET}/messages",
            params={"instance_id": "nowhere"},
            json={"content": "hi"},
            headers=auth,
        )

        assert response.status_code == 404
        assert "nowhere" in response.json()["detail"]
        assert relayed.calls.last.request.url.params["instance_id"] == "nowhere"

    @respx.mock
    async def test_a_members_guild_listing_includes_the_hubs_sessions(self):
        membership = GuildMembership(guild_url=GUILD, instance=None)
        client, sender, auth = await _guild(membership=membership, with_remote=False)
        hub = respx.get(f"{GUILD}/api/v1/forge/sessions").mock(
            return_value=Response(
                200,
                json=[
                    {"id": TARGET, "instance_id": "hub-1", "instance_name": "Thor"},
                    {"id": str(sender.id), "instance_id": "member-1"},
                ],
            )
        )

        response = client.get("/api/v1/forge/sessions", headers=auth)

        assert response.status_code == 200
        by_id = {item["id"]: item["instance_id"] for item in response.json()}
        assert by_id == {str(sender.id): "local", TARGET: "hub-1"}
        assert "authorization" not in hub.calls.last.request.headers

    @respx.mock
    async def test_a_404_that_is_not_a_missing_route_is_an_outage(self):
        client, _, _ = await _guild(feedless_recheck_seconds=3600.0)
        respx.get(f"{REMOTE}/api/v1/forge/notifications").mock(
            return_value=Response(404, json={"detail": "Instance gone"})
        )

        response = client.get(
            "/api/v1/forge/notifications",
            params={"all_instances": "true"},
            headers={"x-auth-user-id": OWNER, "x-auth-tenant": TENANT},
        )

        assert response.status_code == 200
        assert response.headers["X-Forge-Unavailable-Instances"] == "remote"
        assert "X-Forge-Feedless-Instances" not in response.headers

    @respx.mock
    async def test_a_guild_that_refuses_the_owner_question_makes_no_claim(self):
        membership = GuildMembership(guild_url=GUILD, instance=None)
        client, _, auth = await _guild(membership=membership, with_remote=False)
        respx.get(f"{GUILD}/api/v1/forge/sessions/{TARGET}").mock(return_value=Response(401))

        response = client.get(f"/api/v1/forge/sessions/{TARGET}", headers=auth)

        assert response.status_code == 404
        assert "owned by" not in response.json()["detail"]
        assert "another node" not in response.json()["detail"]

    @respx.mock
    async def test_read_state_remembers_a_missing_route_on_its_own(self):
        client, _, _ = await _guild(feedless_recheck_seconds=3600.0)
        read_state = respx.get(f"{REMOTE}/api/v1/forge/notifications/read-state").mock(
            return_value=Response(404, json={"detail": "Not Found"})
        )
        feed = respx.get(f"{REMOTE}/api/v1/forge/notifications").mock(
            return_value=Response(404, json={"detail": "Not Found"})
        )
        user = {"x-auth-user-id": OWNER, "x-auth-tenant": TENANT}
        fleet = {"all_instances": "true"}

        for _ in range(3):
            response = client.get(
                "/api/v1/forge/notifications/read-state", params=fleet, headers=user
            )
            assert response.status_code == 200
            assert response.headers["X-Forge-Feedless-Instances"] == "remote"
            assert "X-Forge-Unavailable-Instances" not in response.headers
        client.get("/api/v1/forge/notifications", params=fleet, headers=user)

        assert read_state.call_count == 1
        # One missing route never hides another: the feed is asked on its own.
        assert feed.call_count == 1

    @respx.mock
    async def test_a_bad_read_state_request_is_the_nodes_answer(self):
        client, _, _ = await _guild()
        respx.get(f"{REMOTE}/api/v1/forge/notifications/read-state").mock(
            return_value=Response(422, json={"detail": "bad filter"})
        )

        response = client.get(
            "/api/v1/forge/notifications/read-state",
            params={"all_instances": "true"},
            headers={"x-auth-user-id": OWNER, "x-auth-tenant": TENANT},
        )

        assert response.status_code == 422
