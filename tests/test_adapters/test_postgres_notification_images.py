"""Notification image SQL with a mocked asyncpg boundary (no database)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from niuu.domain.notifications import NotificationImageRef
from tests.test_adapters.test_postgres_notifications import (
    OWNER,
    _candidate,
    _fetch_by_sql,
    _notification_row,
    _pool,
)
from volundr.adapters.outbound.postgres_notifications import (
    CLAIM_PENDING_IMAGES_SQL,
    FEED_ORDER_LOCK_SQL,
    INSERT_IMAGE_SQL,
    MARK_IMAGE_FAILED_SQL,
    MARK_IMAGE_READY_SQL,
    SELECT_IMAGE_STATES_SQL,
    PostgresNotificationImageRepository,
    PostgresNotificationRepository,
    attach_images,
)
from volundr.domain.notifications import (
    NotificationImage,
    NotificationImageContent,
    NotificationImageState,
    NotificationImageVariant,
    NotificationQuery,
    PendingNotificationImage,
    ProcessedNotificationImage,
)

FILE_ID = "pf_" + "d" * 32
PROCESSED = ProcessedNotificationImage(
    mime="image/png",
    width=10,
    height=20,
    content=b"o",
    thumbnail_mime="image/jpeg",
    thumbnail_width=5,
    thumbnail_height=10,
    thumbnail=b"t",
)


def _image_row(notification_id, state="ready", **overrides) -> dict:
    values = {
        "notification_id": notification_id,
        "state": state,
        "alt": "A",
        "mime": "image/png",
        "width": 10,
        "height": 20,
        "error": None,
    }
    values.update(overrides)
    return values


def _pending(attempts=1) -> PendingNotificationImage:
    return PendingNotificationImage(
        notification_id=uuid4(),
        session_id=uuid4(),
        ref=NotificationImageRef(file_id=FILE_ID),
        attempts=attempts,
    )


class TestProjectWithImage:
    async def test_the_pending_copy_is_written_in_the_same_transaction(self):
        pool, conn = _pool()
        ref = NotificationImageRef(url="https://x.example/a.png", alt="a\x00lt")
        a = _candidate("a", image=ref)
        b = _candidate("b")
        conn.fetchrow.side_effect = [_notification_row(a, 1), _notification_row(b, 2)]

        created = await PostgresNotificationRepository(pool).project([a, b])

        assert created[0].image == NotificationImage(
            state=NotificationImageState.PENDING, alt="a\x00lt"
        )
        assert created[1].image is None
        executed = [call.args for call in conn.execute.await_args_list]
        assert executed[0] == (FEED_ORDER_LOCK_SQL,)
        assert executed[1] == (INSERT_IMAGE_SQL, a.id, "url", "https://x.example/a.png", "a�lt")
        assert len(executed) == 2  # no image row for the candidate without one

    async def test_a_replayed_candidate_writes_no_image(self):
        pool, conn = _pool()
        a = _candidate("a", image=NotificationImageRef(file_id=FILE_ID))
        assert await PostgresNotificationRepository(pool).project([a]) == []
        assert [call.args[0] for call in conn.execute.await_args_list] == [FEED_ORDER_LOCK_SQL]


class TestImageStateOnReads:
    async def test_attach_images_maps_one_query_per_page(self):
        pool, _ = _pool()
        a, b = _candidate("a"), _candidate("b")
        rows = [_notification_row(a, 2), _notification_row(b, 1)]
        _fetch_by_sql(pool, rows, [_image_row(a.id, state="failed", mime=None, error="nope")])
        page = await PostgresNotificationRepository(pool).list_feed(
            OWNER, NotificationQuery(limit=5), read_through_seq=0
        )
        assert page.items[0].image == NotificationImage(
            state=NotificationImageState.FAILED,
            alt="A",
            width=10,
            height=20,
            error="nope",
        )
        assert page.items[1].image is None
        assert pool.fetch.await_args_list[1].args == (SELECT_IMAGE_STATES_SQL, [a.id, b.id])

    async def test_get_carries_the_stored_image(self):
        pool, _ = _pool()
        a = _candidate("a")
        pool.fetchrow.return_value = _notification_row(a, 1)
        _fetch_by_sql(pool, [], [_image_row(a.id)])
        notification = await PostgresNotificationRepository(pool).get(a.id)
        assert notification.image.state is NotificationImageState.READY
        assert notification.wire()["image"]["mime"] == "image/png"

    async def test_an_empty_page_needs_no_image_query(self):
        pool, _ = _pool()
        assert await attach_images(pool, []) == []
        pool.fetch.assert_not_awaited()


class TestImageRepository:
    async def test_claim_maps_rows_to_pending_copies(self):
        pool, _ = _pool()
        nid, sid = uuid4(), uuid4()
        pool.fetch.return_value = [
            {
                "notification_id": nid,
                "source_kind": "file_id",
                "source_ref": FILE_ID,
                "alt": "UI",
                "attempts": 3,
                "session_id": sid,
            },
            {
                "notification_id": uuid4(),
                "source_kind": "url",
                "source_ref": "https://x.example/a.png",
                "alt": None,
                "attempts": 1,
                "session_id": None,
            },
        ]
        repo = PostgresNotificationImageRepository(pool, max_error_chars=5)
        first, second = await repo.claim_pending(limit=4, lease_seconds=30)
        assert pool.fetch.await_args.args == (CLAIM_PENDING_IMAGES_SQL, 4, 30.0)
        assert (first.notification_id, first.session_id, first.attempts) == (nid, sid, 3)
        assert first.ref == NotificationImageRef(file_id=FILE_ID, alt="UI")
        assert second.ref.url == "https://x.example/a.png" and second.session_id is None

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [({"limit": 0, "lease_seconds": 1}, "limit"), ({"limit": 1, "lease_seconds": 0}, "lease")],
    )
    async def test_claim_rejects_impossible_arguments(self, kwargs, message):
        pool, _ = _pool()
        with pytest.raises(ValueError, match=message):
            await PostgresNotificationImageRepository(pool, max_error_chars=5).claim_pending(
                **kwargs
            )

    def test_error_bound_must_be_positive(self):
        with pytest.raises(ValueError, match="max_error_chars"):
            PostgresNotificationImageRepository(_pool()[0], max_error_chars=0)

    async def test_settles_are_fenced_by_attempt(self):
        pool, _ = _pool()
        repo = PostgresNotificationImageRepository(pool, max_error_chars=5)
        pending = _pending(attempts=2)
        pool.execute.return_value = "UPDATE 1"
        assert await repo.mark_ready(pending, PROCESSED) is True
        assert pool.execute.await_args.args == (
            MARK_IMAGE_READY_SQL,
            pending.notification_id,
            2,
            "image/png",
            10,
            20,
            b"o",
            "image/jpeg",
            5,
            10,
            b"t",
        )
        pool.execute.return_value = "UPDATE 0"
        assert await repo.mark_failed(pending, error="bad\x00 things happened") is False
        assert pool.execute.await_args.args == (
            MARK_IMAGE_FAILED_SQL,
            pending.notification_id,
            2,
            "bad� ",
        )

    async def test_content_by_variant(self):
        pool, _ = _pool()
        repo = PostgresNotificationImageRepository(pool, max_error_chars=5)
        nid = uuid4()
        assert await repo.get_content(nid, NotificationImageVariant.ORIGINAL) is None
        pool.fetchrow.return_value = {"mime": "image/jpeg", "content": memoryview(b"thumb")}
        content = await repo.get_content(nid, NotificationImageVariant.THUMB)
        assert content == NotificationImageContent(mime="image/jpeg", content=b"thumb")
        sql, arg = pool.fetchrow.await_args.args
        assert "thumbnail_mime AS mime, thumbnail AS content" in sql and arg == nid
        await repo.get_content(nid, NotificationImageVariant.ORIGINAL)
        assert pool.fetchrow.await_args.args[0].startswith("SELECT mime, content")
        assert "state = 'ready'" in pool.fetchrow.await_args.args[0]
