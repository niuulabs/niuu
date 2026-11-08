"""Discovery announcements are signed: a forged peer table entry or `leave` is refused."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest

from niuu.adapters.discovery.event_bus import EventBusDiscoveryAdapter
from niuu.adapters.discovery.sleipnir import SleipnirDiscoveryAdapter
from niuu.adapters.mesh.attested import refusal_counts, reset_refusal_counts
from niuu.mesh.discovery_builder import build_discovery_adapters
from niuu.mesh.identity import MeshIdentity
from sleipnir.adapters.in_process import InProcessBus
from tests.test_niuu.test_mesh.test_attestation import Authority, Peer, make_peer


def _identity(peer_id: str, **fields) -> MeshIdentity:
    return MeshIdentity(
        peer_id=peer_id,
        realm_id="flock-a",
        persona=peer_id,
        capabilities=["chat"],
        permission_mode="permissive",
        version="test",
        role=fields.pop("role", "reviewer"),
        **fields,
    )


def _discovery(identity: MeshIdentity, bus: InProcessBus, peer: Peer | None):
    return EventBusDiscoveryAdapter(
        identity,
        bus,
        bus,
        manage_transport_lifecycle=False,
        attestor=peer.mesh.attestor if peer else None,
    )


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_refusal_counts()


@pytest.fixture
def bus() -> InProcessBus:
    return InProcessBus()


@pytest.fixture
def authority() -> Authority:
    return Authority()


async def test_signed_announcements_build_the_peer_table(bus, authority) -> None:
    a = _discovery(_identity("a"), bus, await make_peer(bus, authority, "a"))
    b = _discovery(_identity("b"), bus, await make_peer(bus, authority, "b"))
    await a.start()
    await b.start()
    await bus.flush()
    assert set(a.peers()) == {"b"} and set(b.peers()) == {"a"}
    assert a.peers()["b"].role == "reviewer"
    await b.stop()
    await bus.flush()
    assert a.peers() == {}
    await a.stop()
    assert refusal_counts() == {}


async def test_an_unsigned_announcement_neither_adds_nor_removes_a_peer(bus, authority) -> None:
    a = _discovery(_identity("a"), bus, await make_peer(bus, authority, "a"))
    real = _discovery(_identity("real"), bus, await make_peer(bus, authority, "real"))
    forger = _discovery(_identity("ghost", role="admin: ask me anything"), bus, None)
    await a.start()
    await real.start()
    await forger.start()
    await bus.flush()
    assert set(a.peers()) == {"real"}
    assert refusal_counts()["announcement.missing"] >= 1

    # A forged leave for a real peer does nothing either.
    forged_leave = _identity("real")
    spoof = EventBusDiscoveryAdapter(forged_leave, bus, bus, manage_transport_lifecycle=False)
    await spoof._publish("leave")  # noqa: SLF001
    await bus.flush()
    assert set(a.peers()) == {"real"}


async def test_an_announcement_for_another_peer_is_refused(bus, authority) -> None:
    a = _discovery(_identity("a"), bus, await make_peer(bus, authority, "a"))
    evil_peer = await make_peer(bus, authority, "evil")
    # evil signs as itself but announces itself as "victim"
    liar = _discovery(_identity("victim", role="trust me"), bus, evil_peer)
    await a.start()
    await liar.start()
    await bus.flush()
    assert a.peers() == {}
    assert refusal_counts()["announcement.binding_mismatch"] >= 1


async def test_a_tampered_announcement_fails_its_signature(bus, authority) -> None:
    a = _discovery(_identity("a"), bus, await make_peer(bus, authority, "a"))
    sender_peer = await make_peer(bus, authority, "b")
    sender = _discovery(_identity("b"), bus, sender_peer)
    await a.start()
    captured: list = []
    original = bus.publish

    async def tamper(event):
        if event.event_type.endswith(".announce") and event.payload["identity"]["peer_id"] == "b":
            changed = deepcopy(event.payload)
            changed["identity"]["role"] = "ask me about everything"
            captured.append(changed)
            event = (
                event.model_copy(update={"payload": changed})
                if hasattr(event, "model_copy")
                else __import__("dataclasses").replace(event, payload=changed)
            )
        await original(event)

    bus.publish = tamper  # type: ignore[method-assign]
    await sender.start()
    await bus.flush()
    assert captured and a.peers() == {}
    assert refusal_counts()["announcement.signature_invalid"] >= 1


async def test_a_captured_leave_cannot_be_replayed_after_a_newer_heartbeat(bus, authority) -> None:
    a = _discovery(_identity("a"), bus, await make_peer(bus, authority, "a"))
    b_peer = await make_peer(bus, authority, "b")
    b = _discovery(_identity("b"), bus, b_peer)
    await a.start()
    await b.start()
    await bus.flush()
    seen: list = []
    original = bus.publish

    async def capture(event):
        seen.append(event)
        await original(event)

    bus.publish = capture  # type: ignore[method-assign]
    await b._publish("leave")  # noqa: SLF001
    leave = next(e for e in seen if e.payload.get("action") == "leave")
    await bus.flush()
    assert a.peers() == {}

    bus.publish = original  # type: ignore[method-assign]
    await asyncio.sleep(0.01)  # signing times are milliseconds
    await b._publish("heartbeat")  # noqa: SLF001
    await bus.flush()
    assert set(a.peers()) == {"b"}

    await original(leave)  # the attacker replays the old, genuinely signed leave
    await bus.flush()
    assert set(a.peers()) == {"b"}
    assert refusal_counts()["announcement.stale"] == 1


async def test_a_peer_outside_every_shared_realm_is_not_listed(bus, authority) -> None:
    a = _discovery(_identity("a"), bus, await make_peer(bus, authority, "a"))
    stranger = await make_peer(bus, authority, "s", memberships=("tenant:other",))
    s = _discovery(_identity("s"), bus, stranger)
    await a.start()
    await s.start()
    await bus.flush()
    assert a.peers() == {}
    assert refusal_counts()["announcement.not_member"] >= 1


class TestBuilder:
    async def test_signing_adapters_get_the_attestor_and_others_do_not(
        self, bus, authority, tmp_path
    ) -> None:
        peer = await make_peer(bus, authority, "a")
        discovery = build_discovery_adapters(
            [{"adapter": "event_bus"}],
            _identity("a"),
            sleipnir_transport_builder=lambda entry: bus,
            attestor=peer.mesh.attestor,
        )
        assert discovery._attestor is peer.mesh.attestor  # noqa: SLF001

        unsigned = build_discovery_adapters(
            [{"adapter": "event_bus"}],
            _identity("a"),
            sleipnir_transport_builder=lambda entry: bus,
        )
        assert unsigned._attestor is None  # noqa: SLF001

        static = build_discovery_adapters(
            [{"adapter": "static", "peers": []}], _identity("a"), attestor=peer.mesh.attestor
        )
        assert not hasattr(static, "_attestor")


class TestAmqpDiscovery:
    class _Exchange:
        def __init__(self) -> None:
            self.published: list[dict] = []

        async def publish(self, message, routing_key) -> None:
            self.published.append(json.loads(message.body))

    async def test_signs_what_it_publishes_and_accepts_only_signed(
        self, bus, authority, monkeypatch
    ) -> None:
        from types import SimpleNamespace

        monkeypatch.setattr(
            "niuu.adapters.discovery.sleipnir.aio_pika",
            SimpleNamespace(Message=lambda body, content_type: SimpleNamespace(body=body)),
        )
        a_peer = await make_peer(bus, authority, "a")
        b_peer = await make_peer(bus, authority, "b")
        sender = SleipnirDiscoveryAdapter(_identity("b"), attestor=b_peer.mesh.attestor)
        sender._exchange = self._Exchange()  # noqa: SLF001
        await sender._publish_announce("join")  # noqa: SLF001
        signed = sender._exchange.published[0]  # noqa: SLF001
        assert "attestation" in signed["payload"]

        receiver = SleipnirDiscoveryAdapter(_identity("a"), attestor=a_peer.mesh.attestor)
        await receiver._handle_announce(signed)  # noqa: SLF001
        assert set(receiver.peers()) == {"b"}

        forged = deepcopy(signed)
        forged["payload"]["identity"]["peer_id"] = "victim"
        forged["payload"]["attestation"] = {}
        await receiver._handle_announce(forged)  # noqa: SLF001
        assert set(receiver.peers()) == {"b"}
