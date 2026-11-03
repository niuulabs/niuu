"""One image through every real layer: a present-file id in a notify turn, projected,
copied from the session's broker, decoded by Pillow, served by the REST route."""

from __future__ import annotations

import io
from datetime import UTC, datetime

import httpx
from PIL import Image

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from niuu.domain.notifications import NotificationDraft, build_notification_turn
from tests.conftest import MockEventBroadcaster
from tests.test_adapters.test_rest_notification_images import PREFIX, Env
from tests.test_adapters.test_rest_notifications import headers
from volundr.adapters.outbound.notification_image_fetcher import HttpNotificationImageFetcher
from volundr.adapters.outbound.pillow_notification_images import (
    PillowNotificationImageProcessor,
)
from volundr.domain.models import Session, SessionLogEntry
from volundr.domain.services.notification_images import (
    ImageWorkerSettings,
    NotificationImageWorker,
)

FILE_ID = "pf_" + "9" * 32


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (30, 90, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


async def test_present_file_image_reaches_readers_through_every_layer():
    env = Env(identity=EnvoyHeaderAuthenticationAdapter())
    broadcaster = MockEventBroadcaster()
    original = _png(1600, 900)
    broker_requests: list[str] = []

    def broker(request: httpx.Request) -> httpx.Response:
        broker_requests.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "image/png"}, content=original)

    session = Session(
        name="ui", owner_id="owner-a", tenant_id="tenant-a", chat_endpoint="ws://broker:9/session"
    )
    await env.sessions.create(session)
    turn = build_notification_turn(
        NotificationDraft(
            kind="milestone",
            title="Dashboard redesign done",
            image={"file_id": FILE_ID, "alt": "The new dashboard"},
        ),
        turn_id="nt_flow",
        session_id=session.id,
        created_at="2026-09-30T10:00:00Z",
    )
    entry = SessionLogEntry(
        session_id=session.id,
        seq=4,
        kind="conversation.turn",
        payload={"type": "conversation.turn", "turn": turn},
        ts=datetime.now(UTC),
    )
    [created] = await env.service.project_log_entries(session, [entry])

    worker = NotificationImageWorker(
        env.store.images,
        env.store.feed,
        HttpNotificationImageFetcher(
            session_lookup=env.sessions.get,
            gateway_url="http://gateway.invalid",
            timeout_seconds=5,
            max_redirects=0,
            allow_private_networks=False,
            transport=httpx.MockTransport(broker),
        ),
        PillowNotificationImageProcessor(thumbnail_max_side=320, thumbnail_jpeg_quality=85),
        ImageWorkerSettings(
            poll_interval_seconds=1,
            batch_size=5,
            lease_seconds=30,
            fetch_timeout_seconds=5,
            max_concurrent_fetches=1,
        ),
        broadcaster=broadcaster,
    )
    assert await worker.run_once() == 1
    assert broker_requests == [f"http://broker:9/api/files/presented/{FILE_ID}"]

    [event] = broadcaster.events
    item = next(
        i
        for i in env.client.get(f"{PREFIX}/notifications", headers=headers()).json()["items"]
        if i["id"] == str(created.id)
    )
    expected_image = {
        "url": f"/api/v1/forge/notifications/{created.id}/image",
        "thumbnail_url": f"/api/v1/forge/notifications/{created.id}/image?variant=thumb",
        "mime": "image/png",
        "width": 1600,
        "height": 900,
        "alt": "The new dashboard",
    }
    assert item["image"] == expected_image and event.data["image"] == expected_image

    full = env.client.get(item["image"]["url"], headers=headers())
    assert (full.status_code, full.content) == (200, original)
    thumb = env.client.get(item["image"]["thumbnail_url"], headers=headers())
    decoded = Image.open(io.BytesIO(thumb.content))
    assert (thumb.headers["content-type"], decoded.format, decoded.size) == (
        "image/jpeg",
        "JPEG",
        (320, 180),
    )
