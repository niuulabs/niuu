"""GET /api/v1/forge/guild on a Forge node, and the Guild instance on its own feed."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from niuu.config_models import GuildMembershipConfig
from niuu.domain.guild_membership import GuildInstance, GuildMembership
from niuu.domain.notifications import NotificationDraft, build_notification_turn
from tests.conftest import InMemorySessionRepository, MockEventBroadcaster, MockPodManager
from tests.support.notifications import InMemoryNotificationStore
from volundr.adapters.inbound.rest_guild import create_guild_router
from volundr.adapters.inbound.rest_notifications import create_notifications_router
from volundr.config import Settings
from volundr.domain.models import EventType, Session, SessionLogEntry
from volundr.domain.notifications import NotificationSinkInfo
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.session import SessionService

PREFIX = "/api/v1/forge"
GUILD_URL = "https://yggdrasil.niuu.world"
VALHALLA = GuildInstance(
    id="ae40cfda-426a-46b4-a080-1ad668000859", name="Valhalla", slug="valhalla"
)
INSTANCE_FIELDS = {
    "instance_id": "ae40cfda-426a-46b4-a080-1ad668000859",
    "instance_name": "Valhalla",
    "instance_slug": "valhalla",
}
HEADERS = {
    "x-auth-user-id": "owner-a",
    "x-auth-tenant": "tenant-a",
    "x-auth-roles": "volundr:developer",
}


def _guild_client(membership: GuildMembership | None) -> TestClient:
    app = FastAPI()
    app.state.identity = EnvoyHeaderAuthenticationAdapter()
    app.include_router(create_guild_router(membership, prefix=PREFIX))
    return TestClient(app)


class TestGuildRoute:
    def test_joined_node_names_its_guild_and_its_instance_there(self):
        client = _guild_client(GuildMembership(guild_url=GUILD_URL, instance=VALHALLA))

        response = client.get(f"{PREFIX}/guild", headers=HEADERS)

        assert response.status_code == 200
        assert response.json() == {"guild_url": GUILD_URL, **INSTANCE_FIELDS}

    def test_unjoined_node_answers_404(self):
        response = _guild_client(None).get(f"{PREFIX}/guild", headers=HEADERS)

        assert response.status_code == 404
        assert response.json()["detail"] == "This Forge node is not joined to a Guild"

    def test_a_node_that_knows_its_guild_but_not_its_instance_answers_503(self):
        response = _guild_client(GuildMembership(guild_url=GUILD_URL)).get(
            f"{PREFIX}/guild", headers=HEADERS
        )

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert GUILD_URL in detail
        assert "niuu guild heartbeat --once" in detail
        assert "guild.instanceId" in detail

    def test_is_authenticated_like_every_forge_route(self):
        client = _guild_client(GuildMembership(guild_url=GUILD_URL, instance=VALHALLA))

        assert client.get(f"{PREFIX}/guild").status_code == 401


class TestNodeMembershipConfig:
    def test_settings_read_the_guild_section(self):
        settings = Settings(
            guild={
                "url": f"{GUILD_URL}/",
                "instance_id": VALHALLA.id,
                "instance_name": "Valhalla",
                "instance_slug": "valhalla",
                # `niuu join` keeps its node id in the same section.
                "node_id": "node-1",
            }
        )

        assert settings.guild.membership() == GuildMembership(
            guild_url=GUILD_URL, instance=VALHALLA
        )

    def test_no_guild_url_means_no_membership(self):
        assert Settings().guild.membership() is None

    def test_a_guild_url_without_an_instance_still_loads(self):
        """The node keeps running; only GET /guild reports the missing instance."""
        assert Settings(guild={"url": GUILD_URL}).guild.membership() == GuildMembership(
            guild_url=GUILD_URL
        )

    @pytest.mark.parametrize(
        "section",
        [
            {"url": GUILD_URL, "instance_id": VALHALLA.id},
            {"instance_id": VALHALLA.id, "instance_name": "Valhalla", "instance_slug": "v"},
            {"url": "yggdrasil.niuu.world"},
        ],
    )
    def test_rejects_an_incomplete_or_malformed_section(self, section):
        with pytest.raises(ValueError):
            GuildMembershipConfig(**section)


async def _notification_env(guild_instance: GuildInstance | None):
    store = InMemoryNotificationStore()
    sessions = InMemorySessionRepository()
    broadcaster = MockEventBroadcaster()
    session_service = SessionService(sessions, MockPodManager())
    service = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        broadcaster=broadcaster,
        reply_ready_enabled=False,
        reply_title_chars=80,
        reply_body_chars=100,
        sinks=[NotificationSinkInfo(name="ops", label="Ops")],
        guild_instance=guild_instance,
    )
    app = FastAPI()
    app.state.identity = EnvoyHeaderAuthenticationAdapter()
    app.include_router(
        create_notifications_router(
            service, session_service, prefix=PREFIX, default_page_size=5, max_page_size=5
        )
    )
    session = Session(name="fix-auth", owner_id="owner-a", tenant_id="tenant-a")
    await sessions.create(session)
    turn = build_notification_turn(
        NotificationDraft(kind="milestone", title="Tests green"),
        turn_id="t1",
        session_id=session.id,
        created_at="2026-10-16T10:00:00Z",
    )
    await service.project_log_entries(
        session,
        [
            SessionLogEntry(
                session_id=session.id,
                seq=1,
                kind="conversation.turn",
                payload={"type": "conversation.turn", "turn": turn},
                ts=datetime.now(UTC),
            )
        ],
    )
    return TestClient(app), broadcaster, session


class TestNodeFeedInstanceFields:
    async def test_feed_items_name_the_nodes_guild_instance(self):
        client, _, session = await _notification_env(VALHALLA)

        feed = client.get(f"{PREFIX}/notifications", headers=HEADERS).json()
        [item] = feed["items"]
        assert {key: item[key] for key in INSTANCE_FIELDS} == INSTANCE_FIELDS

        [session_item] = client.get(
            f"{PREFIX}/sessions/{session.id}/notifications", headers=HEADERS
        ).json()
        assert {key: session_item[key] for key in INSTANCE_FIELDS} == INSTANCE_FIELDS

    async def test_live_events_name_the_nodes_guild_instance(self):
        _, broadcaster, _ = await _notification_env(VALHALLA)

        [event] = broadcaster.events
        assert event.type is EventType.SESSION_NOTIFICATION
        assert {key: event.data[key] for key in INSTANCE_FIELDS} == INSTANCE_FIELDS

    async def test_a_node_without_a_guild_serves_null_instance_fields(self):
        client, broadcaster, _ = await _notification_env(None)

        [item] = client.get(f"{PREFIX}/notifications", headers=HEADERS).json()["items"]
        assert {key: item[key] for key in INSTANCE_FIELDS} == dict.fromkeys(INSTANCE_FIELDS)
        [event] = broadcaster.events
        assert not set(INSTANCE_FIELDS) & set(event.data)
