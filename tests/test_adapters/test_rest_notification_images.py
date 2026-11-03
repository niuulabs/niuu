"""REST for notification images: the item's ``image`` object and the image route.

The route must admit exactly the readers who see the item in their feed, on every
identity path Forge supports (trusted headers, scoped tokens, and the allow-all
identity a tailnet-only deployment runs with, where the feed needs no token).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import (
    AllowAllIdentityAdapter,
    EnvoyHeaderAuthenticationAdapter,
)
from niuu.domain.notifications import NotificationDraft, build_notification_turn
from tests.conftest import InMemorySessionRepository, MockPodManager
from tests.support.notifications import InMemoryNotificationStore
from tests.test_adapters.test_rest_notifications import OwnerOnly, headers
from tests.test_domain.test_notification_images import PROCESSED, FakeFetcher, FakeProcessor
from volundr.adapters.inbound.rest_notifications import create_notifications_router
from volundr.domain.models import Session, SessionLogEntry
from volundr.domain.notifications import NotificationImageError
from volundr.domain.services.notification_images import ImageWorkerSettings, NotificationImageWorker
from volundr.domain.services.notifications import NotificationService
from volundr.domain.services.session import SessionService

PREFIX = "/api/v1/forge"
CACHE_MAX_AGE = 86_400
FILE_ID = "pf_" + "e" * 32
WORKER_SETTINGS = ImageWorkerSettings(
    poll_interval_seconds=1,
    batch_size=10,
    lease_seconds=60,
    fetch_timeout_seconds=1,
    max_concurrent_fetches=2,
)


class Env:
    def __init__(self, identity: object) -> None:
        self.store = InMemoryNotificationStore()
        self.sessions = InMemorySessionRepository()
        session_service = SessionService(self.sessions, MockPodManager(), authorization=OwnerOnly())
        self.service = NotificationService(
            self.store.feed,
            self.store.rule_repo,
            self.store.outbox,
            image_repository=self.store.images,
            image_scheduler=self.store.image_scheduler,
            reply_ready_enabled=False,
            reply_title_chars=80,
            reply_body_chars=100,
            sinks=[],
        )
        app = FastAPI()
        app.state.identity = identity
        app.include_router(
            create_notifications_router(
                self.service,
                session_service,
                prefix=PREFIX,
                default_page_size=10,
                max_page_size=20,
                image_cache_max_age_seconds=CACHE_MAX_AGE,
            )
        )
        self.client = TestClient(app)

    async def notify(self, owner: str, tenant: str, title: str, image: dict | None) -> str:
        session = Session(name=f"{owner}-s", owner_id=owner, tenant_id=tenant)
        await self.sessions.create(session)
        turn = build_notification_turn(
            NotificationDraft(kind="milestone", title=title, image=image),
            turn_id=f"nt-{title}",
            session_id=session.id,
            created_at="2026-09-30T10:00:00Z",
        )
        entry = SessionLogEntry(
            session_id=session.id,
            seq=1,
            kind="conversation.turn",
            payload={"type": "conversation.turn", "turn": turn},
            ts=datetime.now(UTC),
        )
        [created] = await self.service.project_log_entries(session, [entry])
        return str(created.id)

    async def copy_images(self, fetcher=None) -> None:
        worker = NotificationImageWorker(
            self.store.images,
            self.store.feed,
            fetcher or FakeFetcher(),
            FakeProcessor(),
            WORKER_SETTINGS,
            broadcaster=None,
        )
        await worker.run_once()


@pytest.fixture
def env() -> Env:
    return Env(identity=EnvoyHeaderAuthenticationAdapter())  # trusts forwarded x-auth-*


def _items(client: TestClient, **kwargs) -> dict[str, dict]:
    response = client.get(f"{PREFIX}/notifications", **kwargs)
    if response.status_code != 200:
        return {}
    return {item["id"]: item for item in response.json()["items"]}


class TestItemShape:
    async def test_ready_image_object_and_states(self, env):
        ready = await env.notify("owner-a", "tenant-a", "ready", {"file_id": FILE_ID, "alt": "UI"})
        await env.copy_images()
        pending = await env.notify("owner-a", "tenant-a", "pending", {"file_id": FILE_ID})
        plain = await env.notify("owner-a", "tenant-a", "plain", None)

        items = _items(env.client, headers=headers())
        assert items[ready]["image"] == {
            "url": f"/api/v1/forge/notifications/{ready}/image",
            "thumbnail_url": f"/api/v1/forge/notifications/{ready}/image?variant=thumb",
            "mime": "image/png",
            "width": 1200,
            "height": 800,
            "alt": "UI",
        }
        assert (items[ready]["image_state"], items[ready]["image_error"]) == ("ready", None)
        assert (items[pending]["image"], items[pending]["image_state"]) == (None, "pending")
        assert (items[plain]["image"], items[plain]["image_state"]) == (None, None)

    async def test_failed_image_is_reported_on_the_item(self, env):
        failed = await env.notify("owner-a", "tenant-a", "f", {"url": "https://x.example/a.png"})
        await env.copy_images(FakeFetcher(NotificationImageError("x.example answered HTTP 404")))
        item = _items(env.client, headers=headers())[failed]
        assert item["image"] is None
        assert (item["image_state"], item["image_error"]) == (
            "failed",
            "x.example answered HTTP 404",
        )

    async def test_session_list_and_submit_carry_the_image_fields(self, env):
        nid = await env.notify("owner-a", "tenant-a", "s", {"file_id": FILE_ID})
        await env.copy_images()
        session_id = env.store.image_rows[UUID(nid)].session_id
        [item] = env.client.get(
            f"{PREFIX}/sessions/{session_id}/notifications", headers=headers()
        ).json()
        assert item["image"]["url"].endswith(f"/notifications/{nid}/image")
        submitted = env.client.post(
            f"{PREFIX}/sessions/{session_id}/notifications",
            json={
                "kind": "info",
                "title": "chart",
                "idempotency_key": "k1",
                "image": {"url": "https://x.example/c.png", "alt": "Chart"},
            },
            headers=headers(),
        )
        assert submitted.status_code == 201
        body = submitted.json()
        assert (body["image"], body["image_state"]) == (None, "pending")
        bad = env.client.post(
            f"{PREFIX}/sessions/{session_id}/notifications",
            json={"kind": "info", "title": "x", "idempotency_key": "k2", "image": {}},
            headers=headers(),
        )
        assert bad.status_code == 422


class TestImageRoute:
    async def test_serves_the_image_and_its_thumbnail_with_long_caching(self, env):
        nid = await env.notify("owner-a", "tenant-a", "r", {"file_id": FILE_ID})
        await env.copy_images()
        url = f"{PREFIX}/notifications/{nid}/image"

        original = env.client.get(url, headers=headers())
        assert original.status_code == 200
        assert original.content == PROCESSED.content
        assert original.headers["content-type"] == "image/png"
        assert original.headers["cache-control"] == (f"private, max-age={CACHE_MAX_AGE}, immutable")
        assert original.headers["x-content-type-options"] == "nosniff"

        thumb = env.client.get(url, params={"variant": "thumb"}, headers=headers())
        assert (thumb.status_code, thumb.content) == (200, PROCESSED.thumbnail)
        assert thumb.headers["content-type"] == "image/jpeg"
        explicit = env.client.get(url, params={"variant": "original"}, headers=headers())
        assert explicit.content == PROCESSED.content
        assert env.client.get(url, params={"variant": "huge"}, headers=headers()).status_code == 422

    async def test_404_without_a_stored_image(self, env):
        pending = await env.notify("owner-a", "tenant-a", "p", {"file_id": FILE_ID})
        plain = await env.notify("owner-a", "tenant-a", "n", None)
        for nid in (pending, plain, str(uuid4())):
            response = env.client.get(f"{PREFIX}/notifications/{nid}/image", headers=headers())
            assert response.status_code == 404
            assert "cache-control" not in response.headers
        bad = env.client.get(f"{PREFIX}/notifications/not-a-uuid/image", headers=headers())
        assert bad.status_code == 422


class TestAuthParity:
    """The image route admits exactly the callers that see the item in their feed."""

    @staticmethod
    def _assert_parity(client: TestClient, ids: list[str], **kwargs) -> dict[str, int]:
        feed = client.get(f"{PREFIX}/notifications", **kwargs)
        visible = _items(client, **kwargs)
        statuses = {}
        for nid in ids:
            status = client.get(f"{PREFIX}/notifications/{nid}/image", **kwargs).status_code
            statuses[nid] = status
            if feed.status_code != 200:
                assert status == feed.status_code
            else:
                assert (status == 200) == (nid in visible), (nid, status)
        return statuses

    async def test_header_identities(self, env):
        mine = await env.notify("owner-a", "tenant-a", "a", {"file_id": FILE_ID})
        theirs = await env.notify("owner-b", "tenant-a", "b", {"file_id": FILE_ID})
        await env.copy_images()
        ids = [mine, theirs]

        owner = self._assert_parity(env.client, ids, headers=headers("owner-a"))
        assert owner == {mine: 200, theirs: 404}
        admin = self._assert_parity(env.client, ids, headers=headers("root", admin=True))
        assert admin == {mine: 200, theirs: 200}
        foreign = {**headers("root", admin=True), "x-auth-tenant": "tenant-z"}
        assert self._assert_parity(env.client, ids, headers=foreign) == {mine: 404, theirs: 404}
        # No identity at all: refused exactly like the feed.
        assert self._assert_parity(env.client, ids) == {mine: 401, theirs: 401}

    async def test_scoped_session_tokens_need_the_read_scope_like_the_feed(self, env):
        nid = await env.notify("owner-a", "tenant-a", "a", {"file_id": FILE_ID})
        await env.copy_images()
        token = jwt.encode(
            {"sub": "owner-a", "token_use": "forge_session", "scopes": ["forge:notify"]},
            "test-only-signing-key-32-bytes-long!",
            algorithm="HS256",
        )
        auth = {**headers(), "Authorization": f"Bearer {token}"}
        assert self._assert_parity(env.client, [nid], headers=auth) == {nid: 403}

    async def test_allow_all_identity_reads_like_the_tokenless_feed(self):
        """A Forge without an identity block (Thor's tailnet deployment) serves the feed
        to requests with no credentials as its local admin; images follow suit, and
        still only for the items that reader's feed contains."""
        env = Env(identity=AllowAllIdentityAdapter(user_repository=MagicMock()))
        local = await env.notify("dev-user", "default", "local", {"file_id": FILE_ID})
        other_tenant = await env.notify("owner-a", "tenant-a", "other", {"file_id": FILE_ID})
        await env.copy_images()
        statuses = self._assert_parity(env.client, [local, other_tenant])
        assert statuses == {local: 200, other_tenant: 404}
        response = env.client.get(f"{PREFIX}/notifications/{local}/image")
        assert response.content == PROCESSED.content
