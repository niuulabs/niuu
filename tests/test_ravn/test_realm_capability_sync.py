"""RealmCapabilitySync: mirror evolution events into the realm capability ledger.

Event-driven Self-Model bookkeeping: an installed learned tool shows as
PRESENT, a rolled-back one shows as GAP, and every failure degrades to a
WARNING — the ledger writer must never break the signal path.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import pytest

from ravn.adapters.realm.capability_sync import (
    CAPABILITY_GAP,
    CAPABILITY_KIND_TOOL,
    CAPABILITY_PRESENT,
    EVOLUTION_ACTIVATED_EVENT,
    EVOLUTION_ROLLED_BACK_EVENT,
    RealmCapabilitySync,
)
from ravn.cli.commands import _build_realm_capability_sync
from ravn.config import Settings
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent

REALM = "payments"


class _FakeRealmClient:
    """Records record_capability calls; scripted success/failure."""

    def __init__(self, *, result: bool = True, raise_error: Exception | None = None) -> None:
        self._result = result
        self._raise_error = raise_error
        self.calls: list[dict[str, Any]] = []

    async def record_capability(
        self,
        realm_slug: str,
        *,
        name: str,
        kind: str = "tool",
        status: str,
        trust_level: int = 0,
        notes: str = "",
    ) -> bool:
        self.calls.append(
            {
                "realm_slug": realm_slug,
                "name": name,
                "kind": kind,
                "status": status,
                "trust_level": trust_level,
                "notes": notes,
            }
        )
        if self._raise_error is not None:
            raise self._raise_error
        return self._result


def _event(event_type: str, payload: dict[str, Any]) -> SleipnirEvent:
    return SleipnirEvent(
        event_type=event_type,
        source="valkyrie:test",
        payload=payload,
        summary="test event",
        urgency=0.2,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
    )


async def _started_sync(
    client: _FakeRealmClient | None = None,
) -> tuple[RealmCapabilitySync, InProcessBus, _FakeRealmClient]:
    bus = InProcessBus()
    fake = client if client is not None else _FakeRealmClient()
    sync = RealmCapabilitySync(client=fake, realm_slug=REALM, subscriber=bus)  # type: ignore[arg-type]
    await sync.start()
    return sync, bus, fake


async def _deliver(bus: InProcessBus, event: SleipnirEvent) -> None:
    await bus.publish(event)
    await bus.flush()


# ---------------------------------------------------------------------------
# activation -> present
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activation_records_capability_present() -> None:
    _, bus, client = await _started_sync()

    await _deliver(
        bus,
        _event(
            EVOLUTION_ACTIVATED_EVENT,
            {"skill_name": "parse_invoice", "learning_id": "lrn-1"},
        ),
    )

    assert client.calls == [
        {
            "realm_slug": REALM,
            "name": "parse_invoice",
            "kind": CAPABILITY_KIND_TOOL,
            "status": CAPABILITY_PRESENT,
            "trust_level": 0,
            "notes": "installed learning lrn-1",
        }
    ]


@pytest.mark.asyncio
async def test_self_registration_records_capability_present_from_title() -> None:
    _, bus, client = await _started_sync()

    await _deliver(
        bus,
        _event(
            registry.FLOCK_LEARNING_PROPOSED,
            {
                "learning_id": "artifact-9",
                "title": "classify_alert",
                "learned_tool_manifest": {"name": "classify_alert"},
                "review_outcome": "self_registered",
            },
        ),
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["name"] == "classify_alert"
    assert call["status"] == CAPABILITY_PRESENT
    assert call["notes"] == "self-registered learning artifact-9"


@pytest.mark.asyncio
async def test_self_registration_falls_back_to_manifest_name() -> None:
    _, bus, client = await _started_sync()

    await _deliver(
        bus,
        _event(
            registry.FLOCK_LEARNING_PROPOSED,
            {
                "learning_id": "artifact-9",
                "learned_tool_manifest": {"name": "manifest_tool"},
                "review_outcome": "self_registered",
            },
        ),
    )

    assert [call["name"] for call in client.calls] == ["manifest_tool"]


@pytest.mark.asyncio
async def test_non_self_registered_proposal_is_ignored() -> None:
    # A mere flock proposal from a peer is not an installed capability.
    _, bus, client = await _started_sync()

    await _deliver(
        bus,
        _event(
            registry.FLOCK_LEARNING_PROPOSED,
            {"learning_id": "lrn-2", "title": "peer_tool", "review_outcome": ""},
        ),
    )

    assert client.calls == []


# ---------------------------------------------------------------------------
# rollback -> gap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_records_capability_gap_with_reason() -> None:
    _, bus, client = await _started_sync()

    await _deliver(
        bus,
        _event(
            EVOLUTION_ROLLED_BACK_EVENT,
            {
                "skill_name": "parse_invoice",
                "learning_id": "lrn-1",
                "rationale": "3 consecutive failures",
            },
        ),
    )

    assert client.calls == [
        {
            "realm_slug": REALM,
            "name": "parse_invoice",
            "kind": CAPABILITY_KIND_TOOL,
            "status": CAPABILITY_GAP,
            "trust_level": 0,
            "notes": "3 consecutive failures rolled back learning lrn-1",
        }
    ]


# ---------------------------------------------------------------------------
# degraded paths: warning, no crash, no record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activation_without_skill_name_warns_and_skips(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, bus, client = await _started_sync()

    with caplog.at_level(logging.WARNING, logger="ravn.adapters.realm.capability_sync"):
        await _deliver(bus, _event(EVOLUTION_ACTIVATED_EVENT, {"learning_id": "lrn-1"}))

    assert client.calls == []
    assert "no skill_name" in caplog.text


@pytest.mark.asyncio
async def test_rollback_without_skill_name_warns_and_skips(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, bus, client = await _started_sync()

    with caplog.at_level(logging.WARNING, logger="ravn.adapters.realm.capability_sync"):
        await _deliver(bus, _event(EVOLUTION_ROLLED_BACK_EVENT, {"rationale": "regressed"}))

    assert client.calls == []
    assert "no skill_name" in caplog.text


@pytest.mark.asyncio
async def test_self_registration_without_any_name_warns_and_skips(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, bus, client = await _started_sync()

    with caplog.at_level(logging.WARNING, logger="ravn.adapters.realm.capability_sync"):
        await _deliver(
            bus,
            _event(
                registry.FLOCK_LEARNING_PROPOSED,
                {"review_outcome": "self_registered", "learned_tool_manifest": "not-a-dict"},
            ),
        )

    assert client.calls == []
    assert "no title or manifest name" in caplog.text


@pytest.mark.asyncio
async def test_client_returning_false_warns_and_does_not_crash(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, bus, client = await _started_sync(_FakeRealmClient(result=False))

    with caplog.at_level(logging.WARNING, logger="ravn.adapters.realm.capability_sync"):
        await _deliver(
            bus,
            _event(EVOLUTION_ACTIVATED_EVENT, {"skill_name": "parse_invoice"}),
        )

    assert len(client.calls) == 1
    assert "could not be recorded" in caplog.text


@pytest.mark.asyncio
async def test_client_raising_is_contained_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Even a client bug must not propagate into the bus consumer.
    _, bus, client = await _started_sync(_FakeRealmClient(raise_error=RuntimeError("client bug")))

    with caplog.at_level(logging.WARNING, logger="ravn.adapters.realm.capability_sync"):
        await _deliver(
            bus,
            _event(EVOLUTION_ACTIVATED_EVENT, {"skill_name": "parse_invoice"}),
        )

    assert len(client.calls) == 1
    assert "capability sync failed" in caplog.text


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_is_idempotent_and_stop_unsubscribes() -> None:
    sync, bus, client = await _started_sync()
    assert sync.is_running

    await sync.start()  # second start must not double-subscribe
    await _deliver(bus, _event(EVOLUTION_ACTIVATED_EVENT, {"skill_name": "t"}))
    assert len(client.calls) == 1

    await sync.stop()
    assert not sync.is_running
    await sync.stop()  # idempotent

    await _deliver(bus, _event(EVOLUTION_ACTIVATED_EVENT, {"skill_name": "t"}))
    assert len(client.calls) == 1


def test_constructor_requires_realm_slug() -> None:
    with pytest.raises(ValueError, match="requires a realm_slug"):
        RealmCapabilitySync(
            client=_FakeRealmClient(),  # type: ignore[arg-type]
            realm_slug="",
            subscriber=InProcessBus(),
        )


# ---------------------------------------------------------------------------
# daemon wiring (_build_realm_capability_sync)
# ---------------------------------------------------------------------------


def test_wiring_returns_none_without_realm_slug() -> None:
    assert _build_realm_capability_sync(Settings(), subscriber=InProcessBus()) is None


def test_wiring_returns_none_without_subscriber(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings(
        resident_evolution={
            "realm_slug": REALM,
            "realm_api_base_url": "http://volundr",
        }
    )

    with caplog.at_level(logging.WARNING, logger="ravn.cli.commands"):
        assert _build_realm_capability_sync(settings, subscriber=None) is None

    assert "no bus subscriber" in caplog.text


def test_wiring_returns_none_when_realm_client_is_unbuildable() -> None:
    # realm_slug set but no base_url anywhere -> _realm_client_for degrades to None.
    settings = Settings(resident_evolution={"realm_slug": REALM})

    assert _build_realm_capability_sync(settings, subscriber=InProcessBus()) is None


def test_wiring_builds_sync_when_realm_is_configured() -> None:
    settings = Settings(
        resident_evolution={
            "realm_slug": REALM,
            "realm_api_base_url": "http://volundr",
            "realm_api_kwargs": {"external_token_env": "REALM_TOKEN"},
        }
    )

    sync = _build_realm_capability_sync(settings, subscriber=InProcessBus())

    assert isinstance(sync, RealmCapabilitySync)
    assert not sync.is_running
