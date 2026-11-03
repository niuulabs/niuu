"""NotificationService hooks the artifact hold uses: a system candidate, a post-commit broadcast."""

from __future__ import annotations

from datetime import UTC, datetime

from niuu.domain.notifications import NotificationDraft, notification_id
from tests.conftest import MockEventBroadcaster
from tests.test_domain.test_notification_service import _service, _session
from volundr.domain.models import EventType
from volundr.domain.notifications import Notification
from volundr.domain.projects import SessionCoordination


def test_burst_candidate_is_a_system_notice_for_the_session():
    service, _ = _service()
    session = _session()
    session = session.model_copy(
        update={
            "coordination": SessionCoordination(project_id="4af747c0-2e5e-40c2-8f3f-9de0d14042b5")
        }
    )
    draft = NotificationDraft(
        kind="milestone",
        title="Deck published",
        body=session.name,
        correlation_id="art-burst:b1",
    )
    candidate = service.burst_candidate(
        session, dedupe_key="artifact-burst:b1", draft=draft, metadata={"burst_id": "b1"}
    )
    assert candidate.source.value == "system"
    assert candidate.id == notification_id("artifact-burst:b1")
    assert (candidate.session_id, candidate.session_name) == (session.id, "fix-auth")
    assert (candidate.owner_id, candidate.tenant_id) == ("owner-a", "tenant-a")
    assert candidate.project_id == "4af747c0-2e5e-40c2-8f3f-9de0d14042b5"
    assert candidate.engine == "claude" and candidate.session_seq is None
    assert candidate.correlation_id == "art-burst:b1"
    assert candidate.metadata == {"burst_id": "b1"}


async def test_broadcast_created_publishes_the_committed_row():
    broadcaster = MockEventBroadcaster()
    service, _ = _service(broadcaster=broadcaster)
    session = _session()
    candidate = service.burst_candidate(
        session,
        dedupe_key="artifact-burst:b2",
        draft=NotificationDraft(kind="milestone", title="X published"),
        metadata={},
    )
    notification = Notification(
        id=candidate.id,
        seq=7,
        dedupe_key=candidate.dedupe_key,
        session_id=session.id,
        owner_id=candidate.owner_id,
        kind=candidate.kind,
        severity=candidate.severity,
        source=candidate.source,
        title=candidate.title,
        created_at=datetime(2026, 10, 22, tzinfo=UTC),
    )
    await service.broadcast_created(notification)
    [event] = [e for e in broadcaster.events if e.type is EventType.SESSION_NOTIFICATION]
    assert event.data["id"] == str(candidate.id) and event.data["seq"] == 7


async def test_broadcast_created_without_broadcaster_is_a_no_op():
    service, _ = _service()
    session = _session()
    await service.broadcast_created(
        Notification(
            id=notification_id("artifact-burst:b3"),
            seq=1,
            dedupe_key="artifact-burst:b3",
            session_id=session.id,
            owner_id="owner-a",
            kind="milestone",
            severity="info",
            source="system",
            title="t",
            created_at=datetime(2026, 10, 22, tzinfo=UTC),
        )
    )


async def test_burst_notice_live_event_carries_the_guild_instance():
    """A burst notice is broadcast after its own commit; it still has the feed's shape,
    including the node's Guild instance fields, like every other live notification."""
    from niuu.domain.guild_membership import GuildInstance
    from tests.test_domain.test_notification_service import InMemoryNotificationStore
    from volundr.domain.notifications import NotificationSinkInfo
    from volundr.domain.services.notifications import NotificationService

    broadcaster = MockEventBroadcaster()
    store = InMemoryNotificationStore()
    guild = GuildInstance(id="i-thor", name="Thor", slug="thor")
    service = NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        image_repository=store.images,
        image_scheduler=store.image_scheduler,
        broadcaster=broadcaster,
        reply_ready_enabled=False,
        reply_title_chars=40,
        reply_body_chars=60,
        sinks=[NotificationSinkInfo(name="ops", label="Ops")],
        engine_resolver=lambda session: "claude",
        guild_instance=guild,
    )
    session = _session()
    candidate = service.burst_candidate(
        session,
        dedupe_key="artifact-burst:b4",
        draft=NotificationDraft(kind="milestone", title="Deck published"),
        metadata={"burst_id": "b4"},
    )
    await service.broadcast_created(
        Notification(
            id=candidate.id,
            seq=9,
            dedupe_key=candidate.dedupe_key,
            session_id=session.id,
            owner_id=candidate.owner_id,
            kind=candidate.kind,
            severity=candidate.severity,
            source=candidate.source,
            title=candidate.title,
            metadata=candidate.metadata,
            created_at=datetime(2026, 10, 22, tzinfo=UTC),
        )
    )
    [event] = [e for e in broadcaster.events if e.type is EventType.SESSION_NOTIFICATION]
    assert {key: event.data[key] for key in guild.fields()} == guild.fields()
    assert event.data["source"] == "system"
