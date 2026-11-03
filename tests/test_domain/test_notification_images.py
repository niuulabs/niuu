"""Notification images in the domain: projection, the copy worker and image reads."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from niuu.domain.guild_membership import GuildInstance
from niuu.domain.models import Principal
from niuu.domain.notifications import NotificationDraft, NotificationImageRef
from tests.conftest import MockEventBroadcaster
from tests.support.notifications import InMemoryNotificationStore
from tests.test_domain.test_notification_service import _service, _session, _turn_entry
from volundr.domain.models import EventType
from volundr.domain.notification_ports import NotificationImageFetcher, NotificationImageProcessor
from volundr.domain.notifications import (
    FetchedImage,
    NotificationImage,
    NotificationImageError,
    NotificationImageState,
    NotificationImageVariant,
    NotificationNotFoundError,
    PendingNotificationImage,
    ProcessedNotificationImage,
)
from volundr.domain.services.notification_images import (
    ImageWorkerSettings,
    NotificationImageWorker,
    notification_event,
)

OWNER = Principal(user_id="owner-a", email="", tenant_id="tenant-a", roles=["volundr:developer"])
OTHER = Principal(user_id="owner-b", email="", tenant_id="tenant-a", roles=["volundr:developer"])
ADMIN = Principal(user_id="admin", email="", tenant_id="tenant-a", roles=["volundr:admin"])
FOREIGN_ADMIN = Principal(user_id="x", email="", tenant_id="tenant-z", roles=["volundr:admin"])
FILE_ID = "pf_" + "b" * 32
PROCESSED = ProcessedNotificationImage(
    mime="image/png",
    width=1200,
    height=800,
    content=b"original-png",
    thumbnail_mime="image/jpeg",
    thumbnail_width=320,
    thumbnail_height=213,
    thumbnail=b"thumb-jpeg",
)
SETTINGS = ImageWorkerSettings(
    poll_interval_seconds=0.01,
    batch_size=2,
    lease_seconds=60,
    fetch_timeout_seconds=1,
    max_concurrent_fetches=2,
)


class FakeFetcher(NotificationImageFetcher):
    def __init__(self, outcome: FetchedImage | BaseException | None = None) -> None:
        self.outcome = outcome or FetchedImage(content=b"raw", content_type="image/png")
        self.calls: list[PendingNotificationImage] = []

    async def fetch(self, pending: PendingNotificationImage) -> FetchedImage:
        self.calls.append(pending)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class HangingFetcher(NotificationImageFetcher):
    async def fetch(self, pending: PendingNotificationImage) -> FetchedImage:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class FakeProcessor(NotificationImageProcessor):
    def __init__(self, outcome: ProcessedNotificationImage | BaseException = PROCESSED) -> None:
        self.outcome = outcome
        self.seen: list[FetchedImage] = []

    def process(self, fetched: FetchedImage) -> ProcessedNotificationImage:
        self.seen.append(fetched)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _worker(
    store, *, fetcher=None, processor=None, broadcaster=None, settings=SETTINGS, guild=None
):
    return NotificationImageWorker(
        store.images,
        store.feed,
        fetcher or FakeFetcher(),
        processor or FakeProcessor(),
        settings,
        broadcaster=broadcaster,
        guild_instance=guild,
    )


async def _project_image(service, *, turn_id="nt_img", image=None, seq=1):
    session = _session()
    entry = _turn_entry(session, seq, turn_id, image=image or {"file_id": FILE_ID, "alt": "UI"})
    [created] = await service.project_log_entries(session, [entry])
    return session, created


async def test_settled_image_announcement_carries_the_guild_instance():
    broadcaster = MockEventBroadcaster()
    service, store = _service()
    await _project_image(service)
    guild = GuildInstance(id="i-thor", name="Thor", slug="thor")
    worker = _worker(store, broadcaster=broadcaster, guild=guild)
    assert await worker.run_once() == 1
    [event] = broadcaster.events
    assert event.data["image_state"] == "ready"
    assert {key: event.data[key] for key in guild.fields()} == guild.fields()


class TestProjectionWithImage:
    async def test_image_is_pending_and_the_live_event_waits_for_it(self):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        session, created = await _project_image(service)

        assert created.image == NotificationImage(state=NotificationImageState.PENDING, alt="UI")
        row = store.image_rows[created.id]
        assert (row.session_id, row.ref) == (
            session.id,
            NotificationImageRef(file_id=FILE_ID, alt="UI"),
        )
        assert broadcaster.events == []
        assert store.image_scheduler.wakes == 1
        wire = created.wire()
        assert (wire["image"], wire["image_state"], wire["image_error"]) == (None, "pending", None)

    async def test_replay_creates_no_second_image_and_wakes_nobody(self):
        service, store = _service()
        session = _session()
        entry = _turn_entry(session, 1, "nt_r", image={"url": "https://x.example/a.png"})
        await service.project_log_entries(session, [entry])
        assert await service.project_log_entries(session, [entry]) == []
        assert len(store.image_rows) == 1
        assert store.image_scheduler.wakes == 1

    async def test_notifications_without_image_still_broadcast_at_once(self):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        session = _session()
        entries = [
            _turn_entry(session, 1, "nt_plain"),
            _turn_entry(session, 2, "nt_pic", image={"file_id": FILE_ID}),
        ]
        created = await service.project_log_entries(session, entries)
        assert [n.image is None for n in created] == [True, False]
        [event] = broadcaster.events
        assert event.data["id"] == str(created[0].id)
        assert event.data["image_state"] is None
        assert store.image_scheduler.wakes == 1

    async def test_direct_submit_records_a_pending_image(self):
        service, store = _service()
        draft = NotificationDraft(
            kind="milestone", title="Chart", image={"url": "https://x.example/c.png"}
        )
        notification, created = await service.submit(_session(), OWNER, draft, idempotency_key="k1")
        assert created and notification.image.state is NotificationImageState.PENDING
        assert store.image_rows[notification.id].ref.url == "https://x.example/c.png"


class TestImageReads:
    async def test_visibility_matches_the_item(self):
        service, store = _service()
        _, created = await _project_image(service)
        worker = _worker(store)
        await worker.run_once()

        original = await service.image_content(OWNER, created.id, NotificationImageVariant.ORIGINAL)
        assert (original.mime, original.content) == ("image/png", b"original-png")
        thumb = await service.image_content(ADMIN, created.id, NotificationImageVariant.THUMB)
        assert (thumb.mime, thumb.content) == ("image/jpeg", b"thumb-jpeg")
        for reader in (OTHER, FOREIGN_ADMIN):
            with pytest.raises(NotificationNotFoundError, match="Notification not found"):
                await service.image_content(reader, created.id, NotificationImageVariant.ORIGINAL)

    async def test_no_stored_image_is_not_found(self):
        service, _ = _service()
        _, created = await _project_image(service)
        with pytest.raises(NotificationNotFoundError, match="no stored image"):
            await service.image_content(OWNER, created.id, NotificationImageVariant.ORIGINAL)
        session = _session()
        [plain] = await service.project_log_entries(session, [_turn_entry(session, 5, "nt_x")])
        with pytest.raises(NotificationNotFoundError, match="no stored image"):
            await service.image_content(OWNER, plain.id, NotificationImageVariant.THUMB)
        with pytest.raises(NotificationNotFoundError, match="Notification not found"):
            await service.image_content(OWNER, UUID(int=1), NotificationImageVariant.THUMB)


class TestWorker:
    async def test_stores_the_image_and_announces_it_once(self):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        session, created = await _project_image(service)
        fetcher, processor = FakeFetcher(), FakeProcessor()
        worker = _worker(store, fetcher=fetcher, processor=processor, broadcaster=broadcaster)

        assert await worker.run_once() == 1
        assert await worker.run_once() == 0

        [pending] = fetcher.calls
        assert (pending.notification_id, pending.session_id, pending.attempts) == (
            created.id,
            session.id,
            1,
        )
        assert processor.seen == [FetchedImage(content=b"raw", content_type="image/png")]
        [event] = broadcaster.events
        assert event.type is EventType.SESSION_NOTIFICATION
        assert event.data["image"] == {
            "url": f"/api/v1/forge/notifications/{created.id}/image",
            "thumbnail_url": f"/api/v1/forge/notifications/{created.id}/image?variant=thumb",
            "mime": "image/png",
            "width": 1200,
            "height": 800,
            "alt": "UI",
        }
        assert event.data["image_state"] == "ready"
        feed_item = (await store.feed.get(created.id)).wire()
        assert feed_item == event.data
        assert (worker.stats.claimed, worker.stats.ready, worker.stats.failed) == (1, 1, 0)

    @pytest.mark.parametrize(
        ("fetcher", "processor", "reason"),
        [
            (
                FakeFetcher(NotificationImageError("the session broker answered HTTP 404")),
                FakeProcessor(),
                "the session broker answered HTTP 404",
            ),
            (
                FakeFetcher(),
                FakeProcessor(NotificationImageError("the bytes are not a decodable image")),
                "the bytes are not a decodable image",
            ),
            (HangingFetcher(), FakeProcessor(), "fetching the image timed out after 0.05s"),
        ],
    )
    async def test_a_failed_copy_is_explicit_and_still_announced(
        self, fetcher, processor, reason, caplog
    ):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        _, created = await _project_image(service)
        settings = ImageWorkerSettings(
            poll_interval_seconds=1,
            batch_size=4,
            lease_seconds=60,
            fetch_timeout_seconds=0.05,
            max_concurrent_fetches=1,
        )
        worker = _worker(
            store, fetcher=fetcher, processor=processor, broadcaster=broadcaster, settings=settings
        )
        with caplog.at_level(logging.WARNING):
            await worker.run_once()

        [event] = broadcaster.events
        assert event.data["image"] is None
        assert (event.data["image_state"], event.data["image_error"]) == ("failed", reason)
        assert store.image_rows[created.id].state is NotificationImageState.FAILED
        assert reason in caplog.text and worker.stats.failed == 1
        with pytest.raises(NotificationNotFoundError):
            await service.image_content(OWNER, created.id, NotificationImageVariant.ORIGINAL)

    async def test_a_defect_leaves_the_copy_claimed_for_a_retry(self, caplog):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        _, created = await _project_image(service)
        worker = _worker(store, fetcher=FakeFetcher(RuntimeError("bug")), broadcaster=broadcaster)

        with caplog.at_level(logging.ERROR):
            assert await worker.run_once() == 1

        row = store.image_rows[created.id]
        assert row.state is NotificationImageState.PENDING and row.lease_until is not None
        assert broadcaster.events == [] and worker.stats.loop_errors == 1
        assert "failed unexpectedly" in caplog.text
        # Once the lease expires, a worker claims it again (attempt 2).
        store.clock = lambda: datetime.now(UTC) + timedelta(seconds=120)
        fixed = _worker(store, broadcaster=broadcaster)
        assert await fixed.run_once() == 1
        assert row.attempts == 2 and row.state is NotificationImageState.READY
        assert len(broadcaster.events) == 1

    async def test_a_lost_fence_neither_settles_nor_announces(self):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        _, created = await _project_image(service)
        [pending] = await store.images.claim_pending(limit=1, lease_seconds=60)
        stale = PendingNotificationImage(
            notification_id=pending.notification_id,
            session_id=pending.session_id,
            ref=pending.ref,
            attempts=pending.attempts - 1,
        )
        worker = _worker(store, broadcaster=broadcaster)
        await worker._process(stale, asyncio.Semaphore(1))
        failing = _worker(
            store, fetcher=FakeFetcher(NotificationImageError("x")), broadcaster=broadcaster
        )
        await failing._process(stale, asyncio.Semaphore(1))
        assert store.image_rows[created.id].state is NotificationImageState.PENDING
        assert broadcaster.events == []
        assert worker.stats.ready == 0 and failing.stats.failed == 0

    async def test_without_broadcaster_or_notification_nothing_is_published(self):
        service, store = _service()
        _, created = await _project_image(service)
        assert await _worker(store).run_once() == 1
        broadcaster = MockEventBroadcaster()
        worker = _worker(store, broadcaster=broadcaster)
        await worker._announce(UUID(int=7))
        assert broadcaster.events == []

    async def test_loop_wakes_on_demand_and_stops(self):
        broadcaster = MockEventBroadcaster()
        service, store = _service(broadcaster=broadcaster)
        settings = ImageWorkerSettings(
            poll_interval_seconds=30,
            batch_size=1,
            lease_seconds=60,
            fetch_timeout_seconds=1,
            max_concurrent_fetches=1,
        )
        worker = _worker(store, broadcaster=broadcaster, settings=settings)
        await worker.start()
        await worker.start()  # idempotent
        assert worker.running
        # Two pending copies with batch_size=1: the loop drains both after one wake.
        await _project_image(service, turn_id="nt_a", seq=1)
        await _project_image(service, turn_id="nt_b", seq=2)
        worker.wake()
        for _ in range(200):
            if worker.stats.ready == 2:
                break
            await asyncio.sleep(0.01)
        assert worker.stats.ready == 2 and len(broadcaster.events) == 2
        await worker.stop()
        assert not worker.running
        await worker.stop()  # stopping twice is harmless

    async def test_loop_survives_a_failing_poll(self, caplog):
        store = InMemoryNotificationStore()
        worker = _worker(store)

        async def broken(**_kwargs):
            raise ConnectionError("db down")

        store.images.claim_pending = broken
        with caplog.at_level(logging.ERROR):
            await worker.start()
            for _ in range(100):
                if worker.stats.loop_errors:
                    break
                await asyncio.sleep(0.01)
            await worker.stop()
        assert worker.stats.loop_errors >= 1
        assert "Notification image poll failed" in caplog.text

    async def test_stop_cancels_a_copy_that_outlives_the_grace(self):
        service, store = _service()
        await _project_image(service)
        settings = ImageWorkerSettings(
            poll_interval_seconds=30,
            batch_size=1,
            lease_seconds=60,
            fetch_timeout_seconds=0.05,
            max_concurrent_fetches=1,
        )

        class SlowProcessor(NotificationImageProcessor):
            def process(self, fetched):
                time.sleep(0.2)
                return PROCESSED

        worker = _worker(store, processor=SlowProcessor(), settings=settings)
        await worker.start()
        await asyncio.sleep(0.02)
        await worker.stop()
        assert not worker.running

    def test_settings_require_the_fetch_to_fit_the_lease(self):
        with pytest.raises(ValueError, match="shorter than lease_seconds"):
            ImageWorkerSettings(
                poll_interval_seconds=1,
                batch_size=1,
                lease_seconds=5,
                fetch_timeout_seconds=5,
                max_concurrent_fetches=1,
            )


async def test_notification_event_is_the_wire_item():
    service, _ = _service(InMemoryNotificationStore())
    draft = NotificationDraft(kind="info", title="x")
    notification, _ = await service.submit(_session(), OWNER, draft, idempotency_key="k")
    event = notification_event(notification, {})
    assert event.type is EventType.SESSION_NOTIFICATION
    assert event.data == notification.wire() and event.timestamp == notification.created_at
    instance = {"instance_id": "thor", "instance_name": "Thor", "instance_slug": "thor"}
    assert notification_event(notification, instance).data == {**notification.wire(), **instance}
