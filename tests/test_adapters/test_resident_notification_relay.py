"""Residents' notifications reach the Forge feed: Volundr's relay against Ravn's real shim.

The resident side is the production code (its outbox and OpenClaw shim on a real
socket); the Forge side is the production controller, relay and notification
service over the in-memory feed. So these prove a resident on either backend
(helmrelease or OpenShell) raises attention, retires it when answered, and
notifies on purpose, into the same feed, rules and live events as a session,
authenticated by nothing but the resident's own shim token.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

import pytest

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.domain.models import Principal
from niuu.domain.notifications import NotificationDraft, NotificationKind
from niuu.domain.resident_notifications import (
    AttentionInputKind,
    ResidentNotificationEntry,
    ResidentNotificationOp,
)
from ravn.adapters.resident_notifications import SqliteResidentNotificationOutbox
from tests.support.notifications import InMemoryNotificationStore
from tests.test_adapters.test_ravn_gateway import TOKEN, _FluxRoutes, _resident_shim
from volundr.adapters.outbound.ravn_gateway import RavnResidentSessionController
from volundr.adapters.outbound.resident_notification_relay import (
    RelaySettings,
    ResidentNotificationRelay,
)
from volundr.config import Settings
from volundr.domain.models import (
    ResidentBackend,
    ResidentCapability,
    ResidentDesiredState,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
)
from volundr.domain.notifications import NotificationQuery, NotificationSinkInfo
from volundr.domain.ports import (
    ResidentNotificationSource,
    ResidentNotificationStream,
    ResidentNotificationsUnavailableError,
)
from volundr.domain.services.notifications import NotificationService
from volundr.main import _create_resident_notification_relay

OWNER = Principal(user_id="user-a", email="", tenant_id="tenant-a", roles=["volundr:developer"])
CHANNEL = UUID("6f1c2b3a-4d5e-4f60-8a9b-0c1d2e3f4a5b")
CHANNEL_KEY = f"agent:main:niuu-{CHANNEL}"
FAST = RelaySettings(
    batch_size=2,
    idle_resync_seconds=0.2,
    reconnect_initial_seconds=0.05,
    reconnect_max_seconds=0.2,
)


class _OpenShellRoutes(_FluxRoutes):
    """The OpenShell runtime controller: the same shim-host port, over its TCP forward."""

    backend = ResidentBackend.OPENSHELL


class _Residents:
    """The resident runtime repository, as far as the relay reads it."""

    def __init__(self, *runtimes: ResidentRuntime) -> None:
        self.runtimes = {runtime.id: runtime for runtime in runtimes}

    async def list_for_reconciliation(self) -> list[ResidentRuntime]:
        return list(self.runtimes.values())

    async def get(self, runtime_id: UUID) -> ResidentRuntime | None:
        return self.runtimes.get(runtime_id)


def _runtime(backend: ResidentBackend = ResidentBackend.HELMRELEASE, **update: Any):
    runtime = ResidentRuntime(
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Muninn",
        model="gpt-5.6-sol",
        backend=backend,
        engine=ResidentEngine.RAVN,
        profile_id=f"ravn-{backend.value}",
        observed_state=ResidentObservedState.ACTIVE,
        capabilities=[ResidentCapability.CHAT, ResidentCapability.SESSION_LIST],
    )
    return runtime.model_copy(update=update)


def _service(store: InMemoryNotificationStore) -> NotificationService:
    return NotificationService(
        store.feed,
        store.rule_repo,
        store.outbox,
        image_repository=store.images,
        image_scheduler=store.image_scheduler,
        reply_ready_enabled=False,
        reply_title_chars=40,
        reply_body_chars=60,
        sinks=[NotificationSinkInfo(name="push", label="Push")],
    )


async def _eventually(check: Callable[[], Any], *, timeout: float = 10.0) -> Any:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        result = await check()
        if result:
            return result
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.05)


@pytest.mark.parametrize(
    "routes", [_FluxRoutes, _OpenShellRoutes], ids=["helmrelease", "openshell"]
)
class TestResidentNotificationsEndToEnd:
    async def test_attention_is_raised_retired_and_notify_lands(
        self, tmp_path, open_outbox, routes
    ) -> None:
        outbox = open_outbox(tmp_path / "state.db", max_pending=100)
        async with _resident_shim(tmp_path, notifications=outbox) as shim:
            port = int(shim.url.rsplit(":", 1)[1])
            host = routes(shim_port=port, running_token=TOKEN)
            controller = RavnResidentSessionController(host, MemoryCredentialStore())
            runtime = _runtime(host.backend)
            store = InMemoryNotificationStore()
            service = _service(store)
            await service.ensure_attention_push_rule("user-a", sink="push")
            relay = ResidentNotificationRelay(_Residents(runtime), [controller], service, FAST)

            # Queued while Forge was not following: drained on connect.
            await outbox.attention(
                request_id="operator:case-1:1",
                group="case-1",
                input_kind=AttentionInputKind.QUESTION,
                prompt="May I restart etcd?",
            )
            await relay.sync()
            try:
                assert relay.following == {runtime.id}

                async def attention():
                    feed = await service.list_feed(OWNER, NotificationQuery(limit=10, unread=True))
                    return feed.items

                [item] = await _eventually(attention)
                assert (item.kind, item.source) == ("attention", "system")
                assert (
                    item.title == "Muninn needs your input" and item.body == "May I restart etcd?"
                )
                assert item.resident_id == str(runtime.id) and item.resident_session_id is None
                assert item.session_id is None
                [delivery] = store.deliveries.values()
                assert delivery.sink == "push", "the owner's Needs-input pushes rule delivers it"

                # Live: the agent notifies from a channel while Forge is following.
                await outbox.notify(
                    draft=NotificationDraft(kind=NotificationKind.MILESTONE, title="Report ready"),
                    idempotency_key="k1",
                    channel=CHANNEL_KEY,
                )

                async def two():
                    feed = await service.list_feed(OWNER, NotificationQuery(limit=10))
                    return len(feed.items) == 2 and feed.items

                newest, _ = await _eventually(two)
                assert (newest.kind, newest.source) == ("milestone", "agent")
                assert newest.resident_session_id == str(CHANNEL)

                # The owner answers: the needs-input item is retired.
                await outbox.resolve(group="case-1")

                async def retired():
                    feed = await service.list_feed(OWNER, NotificationQuery(limit=10, unread=True))
                    return [n.kind for n in feed.items] == ["milestone"]

                await _eventually(retired)
                await _eventually(lambda: _empty(outbox))
            finally:
                await relay.stop()
            assert relay.following == frozenset()


async def _empty(outbox: SqliteResidentNotificationOutbox) -> bool:
    return await outbox.pending(limit=1) == []


class _FakeStream(ResidentNotificationStream):
    def __init__(self, batches: list[list[ResidentNotificationEntry]]) -> None:
        self.batches = batches
        self.acked: list[int] = []
        self.closed = False

    async def pending(self, *, limit: int) -> list[ResidentNotificationEntry]:
        return self.batches.pop(0) if self.batches else []

    async def acknowledge(self, *, through_seq: int) -> None:
        self.acked.append(through_seq)

    async def wait_for_more(self, *, timeout: float) -> None:
        await asyncio.sleep(timeout)

    def resident_session_id(self, channel: str | None) -> UUID | None:
        return CHANNEL if channel == CHANNEL_KEY else None

    async def close(self) -> None:
        self.closed = True


class _FakeSource(ResidentNotificationSource):
    def __init__(self, *, backend=ResidentBackend.HELMRELEASE, stream=None, error=None) -> None:
        self.runtime_backend = backend
        self.engine = ResidentEngine.RAVN
        self.stream = stream
        self.error = error
        self.opened = 0

    async def open_notifications(self, runtime):
        self.opened += 1
        if self.error is not None:
            raise self.error
        return self.stream


def _entry(seq: int, op: str, **fields: Any) -> ResidentNotificationEntry:
    return ResidentNotificationEntry.model_validate(
        {"seq": seq, "op": op, "created_at": "2026-10-16T00:00:00+00:00", **fields}
    )


class TestRelay:
    async def test_follows_only_active_running_residents_with_sessions(self) -> None:
        followed = _runtime()
        relay = ResidentNotificationRelay(
            _Residents(
                followed,
                _runtime(observed_state=ResidentObservedState.DEPLOYING),
                _runtime(desired_state=ResidentDesiredState.SUSPENDED),
                _runtime(capabilities=[ResidentCapability.CHAT]),  # shim switched off
                _runtime(engine=ResidentEngine.HERMES),  # no source for its engine
            ),
            [_FakeSource(stream=_FakeStream([]))],
            _service(InMemoryNotificationStore()),
            FAST,
        )
        await relay.sync()
        try:
            assert relay.following == {followed.id}
        finally:
            await relay.stop()

    async def test_a_resident_that_goes_away_is_let_go(self) -> None:
        runtime = _runtime()
        residents = _Residents(runtime)
        relay = ResidentNotificationRelay(
            residents,
            [_FakeSource(stream=_FakeStream([]))],
            _service(InMemoryNotificationStore()),
            FAST,
        )
        await relay.sync()
        residents.runtimes.clear()
        await relay.sync()
        assert relay.following == frozenset()

    async def test_each_backend_reads_through_its_own_controller(self) -> None:
        flux, openshell = _FakeSource(), _FakeSource(backend=ResidentBackend.OPENSHELL)
        relay = ResidentNotificationRelay(_Residents(), [flux, openshell], None, FAST)  # type: ignore[arg-type]
        assert relay.source_for(_runtime(ResidentBackend.HELMRELEASE)) is flux
        assert relay.source_for(_runtime(ResidentBackend.OPENSHELL)) is openshell
        assert relay.source_for(_runtime(ResidentBackend.LOCAL)) is None

    async def test_drains_in_batches_and_acknowledges_each(self) -> None:
        store = InMemoryNotificationStore()
        service = _service(store)
        stream = _FakeStream(
            [
                [
                    _entry(1, "attention", request_id="q1", input_kind="question", prompt="A?"),
                    _entry(
                        2,
                        "notify",
                        channel=CHANNEL_KEY,
                        draft={"kind": "info", "title": "FYI"},
                        idempotency_key="k",
                    ),
                ],
                [_entry(3, "resolved", resolved_request_ids=["q1"])],
            ]
        )
        relay = ResidentNotificationRelay(_Residents(), [], service, FAST)
        await relay._drain(_runtime(), stream)
        assert stream.acked == [2, 3]
        feed = await service.list_feed(OWNER, NotificationQuery(limit=10))
        assert sorted(n.kind for n in feed.items) == ["attention", "info"]
        unread = await service.list_feed(OWNER, NotificationQuery(limit=10, unread=True))
        assert [n.kind for n in unread.items] == ["info"]

    async def test_a_question_answered_in_the_same_backlog_never_pushes(self) -> None:
        store = InMemoryNotificationStore()
        service = _service(store)
        await service.ensure_attention_push_rule("user-a", sink="push")
        relay = ResidentNotificationRelay(_Residents(), [], service, FAST)
        await relay.apply(
            _runtime(),
            _FakeStream([]),
            [
                _entry(1, "attention", request_id="q1", input_kind="question"),
                _entry(2, "attention", request_id="q1", input_kind="question", channel=CHANNEL_KEY),
                _entry(3, "resolved", resolved_request_ids=["q1"]),
            ],
        )
        feed = await service.list_feed(OWNER, NotificationQuery(limit=10))
        assert [(n.resident_session_id, n.kind) for n in feed.items] == [
            (str(CHANNEL), "attention")
        ]
        assert len(store.deliveries) == 1

    async def test_a_refused_notification_does_not_hold_back_the_rest(self, caplog) -> None:
        service = _service(InMemoryNotificationStore())
        relay = ResidentNotificationRelay(_Residents(), [], service, FAST)
        stream = _FakeStream(
            [
                [
                    _entry(
                        1,
                        "notify",
                        draft={"kind": "reply_ready", "title": "x"},
                        idempotency_key="a",
                    ),
                    _entry(2, "notify", draft={"kind": "info", "title": "y"}, idempotency_key="b"),
                ]
            ]
        )
        await relay._drain(_runtime(), stream)
        assert stream.acked == [2]
        assert "was refused and dropped" in caplog.text
        feed = await service.list_feed(OWNER, NotificationQuery(limit=10))
        assert [n.title for n in feed.items] == ["y"]

    async def test_a_resident_without_an_outbox_is_checked_again_later(self, caplog) -> None:
        source = _FakeSource(error=ResidentNotificationsUnavailableError("no outbox"))
        runtime = _runtime()
        relay = ResidentNotificationRelay(
            _Residents(runtime), [source], _service(InMemoryNotificationStore()), FAST
        )
        with caplog.at_level("INFO"):
            await relay.sync()
            await _eventually(lambda: _opened(source, 2), timeout=5)
        await relay.stop()
        assert "no outbox" in caplog.text

    async def test_a_failing_connection_is_retried(self, caplog) -> None:
        source = _FakeSource(error=OSError("connection refused"))
        relay = ResidentNotificationRelay(
            _Residents(_runtime()), [source], _service(InMemoryNotificationStore()), FAST
        )
        await relay.sync()
        await _eventually(lambda: _opened(source, 3), timeout=5)
        await relay.stop()
        assert "retrying" in caplog.text

    def test_settings_are_validated(self) -> None:
        with pytest.raises(ValueError, match="batch_size"):
            RelaySettings(0, 1, 1, 1)
        with pytest.raises(ValueError, match="positive"):
            RelaySettings(1, 0, 1, 1)
        with pytest.raises(ValueError, match="reconnect_max_seconds"):
            RelaySettings(1, 1, 2, 1)


async def _opened(source: _FakeSource, count: int) -> bool:
    return source.opened >= count


class TestShimStream:
    async def test_an_older_shim_without_an_outbox_is_unavailable_not_broken(self, tmp_path):
        async with _resident_shim(tmp_path) as shim:
            controller = RavnResidentSessionController(
                _FluxRoutes(shim_port=int(shim.url.rsplit(":", 1)[1]), running_token=TOKEN),
                MemoryCredentialStore(),
            )
            with pytest.raises(ResidentNotificationsUnavailableError, match="no notification"):
                await controller.open_notifications(_runtime())

    async def test_channels_map_to_forges_resident_sessions(self, tmp_path, open_outbox):
        outbox = open_outbox(tmp_path / "state.db", max_pending=10)
        async with _resident_shim(tmp_path, notifications=outbox) as shim:
            controller = RavnResidentSessionController(
                _FluxRoutes(shim_port=int(shim.url.rsplit(":", 1)[1]), running_token=TOKEN),
                MemoryCredentialStore(),
            )
            stream = await controller.open_notifications(_runtime())
            try:
                assert stream.resident_session_id(CHANNEL_KEY) == CHANNEL
                assert stream.resident_session_id("agent:main:main") is None
                assert stream.resident_session_id(None) is None
                await outbox.attention(
                    request_id="q", group="g", input_kind=AttentionInputKind.QUESTION, prompt=""
                )
                await asyncio.wait_for(stream.wait_for_more(timeout=5), timeout=6)
                [entry] = await stream.pending(limit=10)
                assert entry.op is ResidentNotificationOp.ATTENTION
                await stream.acknowledge(through_seq=entry.seq)
                assert await outbox.pending(limit=10) == []
                # Nothing new: waiting returns after the timeout.
                await stream.wait_for_more(timeout=0.05)
            finally:
                await stream.close()


class TestComposition:
    def test_on_by_default_with_the_feed(self) -> None:
        relay = _create_resident_notification_relay(
            Settings(), _Residents(), [], _service(InMemoryNotificationStore())
        )
        assert isinstance(relay, ResidentNotificationRelay)

    def test_off_without_the_feed_or_when_switched_off(self) -> None:
        assert _create_resident_notification_relay(Settings(), _Residents(), [], None) is None
        settings = Settings.model_validate(
            {"resident_runtimes": {"notifications": {"enabled": False}}}
        )
        service = _service(InMemoryNotificationStore())
        assert _create_resident_notification_relay(settings, _Residents(), [], service) is None

    def test_every_default_ravn_controller_is_a_source(self) -> None:
        """The controllers #1126 wires on every shim host are what the relay reads with."""
        from tests.test_volundr.test_resident_session_controller_composition import (
            _ravn_settings,
            _ShimHost,
        )
        from volundr.main import _create_resident_session_controllers

        controllers = _create_resident_session_controllers(
            _ravn_settings(),
            [_ShimHost(ResidentBackend.HELMRELEASE), _ShimHost(ResidentBackend.OPENSHELL)],  # type: ignore[list-item]
            MemoryCredentialStore(),
        )
        relay = ResidentNotificationRelay(_Residents(), controllers, None, FAST)  # type: ignore[arg-type]
        for backend in (ResidentBackend.HELMRELEASE, ResidentBackend.OPENSHELL):
            assert isinstance(relay.source_for(_runtime(backend)), RavnResidentSessionController)
