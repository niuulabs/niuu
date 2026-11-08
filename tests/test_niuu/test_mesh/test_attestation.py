"""Mesh sender attestation: signing, verification, binding, membership, revocation."""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from niuu.adapters.mesh.attested import (
    REVOCATION_TOPIC,
    AttestedMesh,
    bind_event,
    refusal_counts,
    reset_refusal_counts,
)
from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from niuu.domain.services.mesh_certificate import MeshCertificateError, MeshCertificateIssuer
from niuu.domain.services.workload_identity import WorkloadIdentityService
from niuu.mesh.attestation import (
    KIND_AUTHORITY,
    KIND_RESIDENT,
    KIND_SESSION,
    PURPOSE_EVENT,
    AttestationReason,
    AttestationRefusedError,
    AttestationUnavailableError,
    IssuedMeshCertificate,
    MeshAttestationVerifier,
    MeshSigner,
    b64url,
    public_key_bytes,
)
from niuu.mesh.events import MeshEvent, MeshEventType
from sleipnir.adapters.in_process import InProcessBus

ISSUER = "niuu-test"
TTL_S = 900


def _signing_service(issuer: str = ISSUER, key_id: str = "k1") -> WorkloadIdentityService:
    config = SimpleNamespace(enabled=True, issuer=issuer, audiences=["volundr-api"], key_id=key_id)
    return WorkloadIdentityService(config)


class Authority:
    """Forge's issuing side, in process: what the real route does with a verified session."""

    def __init__(self, signing: WorkloadIdentityService | None = None, ttl: int = TTL_S) -> None:
        self.issuer = MeshCertificateIssuer(signing or _signing_service(), ttl_seconds=ttl)
        self.issued: list[dict[str, Any]] = []

    def source(
        self,
        *,
        session_id: str = "",
        launch_id: str = "launch-1",
        memberships: tuple[str, ...] = ("tenant:t1",),
        kind: str = KIND_SESSION,
    ) -> _Source:
        return _Source(self, session_id, launch_id, memberships, kind)


class _Source:
    def __init__(
        self,
        authority: Authority,
        session_id: str,
        launch_id: str,
        memberships: tuple[str, ...],
        kind: str,
    ) -> None:
        self._authority = authority
        self.session_id = session_id
        self.launch_id = launch_id
        self.memberships = memberships
        self.kind = kind
        self.fail: Exception | None = None
        self.calls = 0

    async def issue(self, public_key: bytes, peer_id: str) -> IssuedMeshCertificate:
        self.calls += 1
        if self.fail is not None:
            raise self.fail
        return self._authority.issuer.issue(
            peer_id=peer_id,
            kind=self.kind,
            public_key=public_key,
            memberships=self.memberships,
            session_id=self.session_id,
            launch_id=self.launch_id,
            tenant_id="t1",
        )


class Peer:
    def __init__(self, mesh: AttestedMesh, signer: MeshSigner, inner: SleipnirMeshAdapter) -> None:
        self.mesh = mesh
        self.signer = signer
        self.inner = inner
        self.events: list[MeshEvent] = []
        self.requests: list[dict] = []
        self.refusals: list[tuple[AttestationRefusedError, str, str]] = []


async def make_peer(
    bus: InProcessBus,
    authority: Authority,
    peer_id: str,
    *,
    session_id: str | None = None,
    memberships: tuple[str, ...] = ("tenant:t1",),
    kind: str = KIND_SESSION,
    launch_id: str = "launch-1",
    topic: str | None = None,
    start: bool = True,
) -> Peer:
    inner = SleipnirMeshAdapter(
        publisher=bus,
        subscriber=bus,
        own_peer_id=peer_id,
        rpc_timeout_s=2.0,
        rpc_reply_cache_size=16,
        manage_transport_lifecycle=False,
    )
    sid = (
        session_id
        if session_id is not None
        else (f"sess-{peer_id}" if kind == KIND_SESSION else "")
    )
    source = authority.source(
        session_id=sid, launch_id=launch_id, memberships=memberships, kind=kind
    )
    signer = MeshSigner(source, peer_id)
    await signer.start()
    verifier = MeshAttestationVerifier(
        lambda: signer.jwks,
        issuer=signer.issuer,
        own_memberships=signer.memberships,
        revocation_retain_s=3600,
    )
    peer = Peer.__new__(Peer)
    mesh = AttestedMesh(
        inner, signer, verifier, own_peer_id=peer_id, on_refusal=lambda *a: peer.refusals.append(a)
    )
    Peer.__init__(peer, mesh, signer, inner)

    async def on_request(message: dict) -> dict:
        peer.requests.append(message)
        return {"status": "accepted", "echo": message.get("type")}

    async def on_event(event: MeshEvent) -> None:
        peer.events.append(event)

    mesh.set_rpc_handler(on_request)
    if start:
        await mesh.start()
    if topic:
        await mesh.subscribe(topic, on_event)
    return peer


def _event(source: str, *, payload: dict | None = None, session_id: str = "") -> MeshEvent:
    return MeshEvent(
        type=MeshEventType.OUTCOME,
        source=source,
        payload=payload or {"prompt": "do it"},
        timestamp=datetime.now(UTC),
        urgency=0.5,
        correlation_id="c1",
        session_id=session_id,
    )


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)
    await asyncio.sleep(0.02)


@pytest.fixture(autouse=True)
def _reset_counts() -> None:
    reset_refusal_counts()


@pytest.fixture
def bus() -> InProcessBus:
    return InProcessBus()


@pytest.fixture
def authority() -> Authority:
    return Authority()


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------


class TestCertificateIssuing:
    def test_certificate_is_addressed_to_the_mesh_and_speaks_for_no_person(
        self, authority: Authority
    ) -> None:
        import jwt

        issued = authority.issuer.issue(
            peer_id="p1",
            kind=KIND_SESSION,
            public_key=public_key_bytes(Ed25519PrivateKey.generate()),
            memberships=["tenant:t1"],
            session_id="s1",
            launch_id="l1",
            tenant_id="t1",
        )
        claims = jwt.decode(issued.token, options={"verify_signature": False})
        assert claims["aud"] == ["niuu-mesh"]
        assert claims["token_use"] == "mesh_peer"
        assert claims["sub"] == "mesh-peer:p1"
        assert "scopes" not in claims and "resource_access" not in claims
        assert "roles" not in claims

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"kind": "bogus"},
            {"peer_id": " "},
            {"public_key": b"short"},
            {"kind": KIND_SESSION, "session_id": ""},
        ],
    )
    def test_refuses_what_it_cannot_certify(self, authority: Authority, kwargs: dict) -> None:
        args: dict[str, Any] = {
            "peer_id": "p1",
            "kind": KIND_SESSION,
            "public_key": public_key_bytes(Ed25519PrivateKey.generate()),
            "memberships": [],
            "session_id": "s1",
            "launch_id": "l1",
        }
        args.update(kwargs)
        with pytest.raises(MeshCertificateError):
            authority.issuer.issue(**args)

    def test_ttl_must_be_usable(self) -> None:
        with pytest.raises(ValueError, match="ttl_seconds"):
            MeshCertificateIssuer(_signing_service(), ttl_seconds=1)


# ---------------------------------------------------------------------------
# Signer
# ---------------------------------------------------------------------------


class TestSigner:
    async def test_start_is_fatal_when_forge_cannot_issue(self, authority: Authority) -> None:
        source = authority.source(session_id="s1")
        source.fail = RuntimeError("forge down")
        signer = MeshSigner(source, "p1")
        with pytest.raises(RuntimeError, match="forge down"):
            await signer.start()

    async def test_refuses_a_certificate_for_another_key(self, authority: Authority) -> None:
        class Swapping(_Source):
            async def issue(self, public_key: bytes, peer_id: str) -> IssuedMeshCertificate:
                return await super().issue(public_key_bytes(Ed25519PrivateKey.generate()), peer_id)

        source = Swapping(authority, "s1", "l1", ("tenant:t1",), KIND_SESSION)
        with pytest.raises(AttestationUnavailableError, match="different key"):
            await MeshSigner(source, "p1").start()

    async def test_refuses_a_certificate_for_another_peer(self, authority: Authority) -> None:
        class OtherPeer(_Source):
            async def issue(self, public_key: bytes, peer_id: str) -> IssuedMeshCertificate:
                return await super().issue(public_key, "someone-else")

        with pytest.raises(AttestationUnavailableError, match="someone-else"):
            await MeshSigner(OtherPeer(authority, "s1", "l1", (), KIND_SESSION), "p1").start()

    def test_fractions_are_validated(self, authority: Authority) -> None:
        with pytest.raises(ValueError, match="renew_fraction"):
            MeshSigner(authority.source(), "p", renew_fraction=1.5)
        with pytest.raises(ValueError, match="retry_fraction"):
            MeshSigner(authority.source(), "p", retry_fraction=0.0)

    async def test_certificate_requires_start(self, authority: Authority) -> None:
        with pytest.raises(AttestationUnavailableError, match="start the signer"):
            MeshSigner(authority.source(), "p").certificate  # noqa: B018

    async def test_renews_at_half_the_lifetime_and_keeps_the_key(self) -> None:
        authority = Authority(ttl=2)
        source = authority.source(session_id="s1")
        signer = MeshSigner(source, "p1", renew_fraction=0.25)
        await signer.start()
        first = signer.certificate
        renewed: list[bool] = []
        signer.add_renewal_listener(lambda: renewed.append(True))
        try:
            await asyncio.wait_for(_until(lambda: renewed), timeout=5)
        finally:
            await signer.stop()
        assert source.calls >= 2
        assert signer.certificate.token != first.token
        assert signer.certificate.public_key == first.public_key

    async def test_failed_renewal_is_retried_and_counted_then_signing_fails_loudly(self) -> None:
        authority = Authority(ttl=2)
        source = authority.source(session_id="s1")
        now = {"t": time.time()}
        signer = MeshSigner(
            source, "p1", renew_fraction=0.2, retry_fraction=0.2, clock=lambda: now["t"]
        )
        await signer.start()
        source.fail = RuntimeError("forge unreachable")
        try:
            await asyncio.wait_for(_until(lambda: source.calls >= 3), timeout=5)
            now["t"] += 10
            await asyncio.wait_for(_until(lambda: source.calls >= 4), timeout=5)
            with pytest.raises(AttestationUnavailableError, match="expired"):
                signer.attest("rpc", {"x": 1})
        finally:
            await signer.stop()

    async def test_stop_without_start_is_a_noop(self, authority: Authority) -> None:
        await MeshSigner(authority.source(), "p").stop()


async def _until(predicate) -> None:
    while not predicate():
        await asyncio.sleep(0.02)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class TestEvents:
    async def test_a_signed_event_reaches_a_member(self, bus, authority) -> None:
        sender = await make_peer(bus, authority, "a")
        receiver = await make_peer(bus, authority, "b", topic="code.requested")
        await sender.mesh.publish(_event("a", session_id="sess-a"), "code.requested")
        await _settle()
        assert [e.source for e in receiver.events] == ["a"]
        assert receiver.refusals == []

    async def test_skuld_prefixed_source_is_the_signing_peer(self, bus, authority) -> None:
        sender = await make_peer(bus, authority, "a")
        receiver = await make_peer(bus, authority, "b", topic="code.changed")
        await sender.mesh.publish(_event("skuld:a"), "code.changed")
        await _settle()
        assert len(receiver.events) == 1

    async def test_unsigned_event_is_refused_with_a_reason_and_counted(
        self, bus, authority
    ) -> None:
        receiver = await make_peer(bus, authority, "b", topic="code.requested")
        attacker = await make_peer(bus, authority, "evil", start=False)
        # Straight onto the transport, as a member that does not sign.
        await attacker.inner.publish(_event("a"), "code.requested")
        await _settle()
        assert receiver.events == []
        assert [r[0].reason for r in receiver.refusals] == [AttestationReason.MISSING]
        assert refusal_counts() == {"event.missing": 1}

    async def test_event_claiming_another_source_is_refused(self, bus, authority) -> None:
        evil = await make_peer(bus, authority, "evil")
        receiver = await make_peer(bus, authority, "b", topic="code.requested")
        await evil.mesh.publish(_event("a"), "code.requested")
        await _settle()
        assert receiver.events == []
        assert receiver.refusals[0][0].reason is AttestationReason.BINDING_MISMATCH

    async def test_event_claiming_another_session_is_refused(self, bus, authority) -> None:
        evil = await make_peer(bus, authority, "evil")
        receiver = await make_peer(bus, authority, "b", topic="code.requested")
        await evil.mesh.publish(_event("evil", session_id="sess-victim"), "code.requested")
        await _settle()
        assert receiver.events == []
        assert receiver.refusals[0][0].reason is AttestationReason.BINDING_MISMATCH

    async def test_publisher_block_naming_another_session_is_refused(self, bus, authority) -> None:
        evil = await make_peer(bus, authority, "evil")
        receiver = await make_peer(bus, authority, "b", topic="review.done")
        payload = {"prompt": "x", "publisher": {"session_id": "sess-victim", "peer_id": "evil"}}
        await evil.mesh.publish(_event("evil", payload=payload), "review.done")
        await _settle()
        assert receiver.events == []
        assert "publisher.session_id" in str(receiver.refusals[0][0])

    async def test_tampered_payload_fails_the_signature(self, bus, authority) -> None:
        sender = await make_peer(bus, authority, "a")
        receiver = await make_peer(bus, authority, "b", topic="code.requested")
        original = _event("a")
        attestation = sender.signer.attest(
            PURPOSE_EVENT,
            __import__("niuu.adapters.mesh.attested", fromlist=["event_subject"]).event_subject(
                original, "code.requested"
            ),
            extra={"topic": "code.requested"},
        )
        forged = replace(original, payload={"prompt": "rm -rf"}, attestation=attestation)
        await sender.inner.publish(forged, "code.requested")
        await _settle()
        assert receiver.events == []
        assert receiver.refusals[0][0].reason is AttestationReason.SIGNATURE_INVALID

    async def test_event_replayed_on_another_topic_is_refused(self, bus, authority) -> None:
        sender = await make_peer(bus, authority, "a")
        receiver = await make_peer(bus, authority, "b", topic="admin.shutdown")
        captured = _event("a")
        signed = replace(
            captured,
            attestation=sender.signer.attest(
                PURPOSE_EVENT,
                __import__("niuu.adapters.mesh.attested", fromlist=["event_subject"]).event_subject(
                    captured, "code.requested"
                ),
                extra={"topic": "code.requested"},
            ),
        )
        await sender.inner.publish(signed, "admin.shutdown")
        await _settle()
        assert receiver.events == []
        assert receiver.refusals[0][0].reason is AttestationReason.BINDING_MISMATCH

    async def test_event_without_a_signed_topic_is_malformed(self, bus, authority) -> None:
        sender = await make_peer(bus, authority, "a")
        receiver = await make_peer(bus, authority, "b", topic="code.requested")
        event = replace(_event("a"), attestation={"v": 1, "cert": "x", "sig": "y", "at": 1})
        await sender.inner.publish(event, "code.requested")
        await _settle()
        assert receiver.refusals[0][0].reason is AttestationReason.MALFORMED

    async def test_an_event_too_old_is_stale(self, bus, authority) -> None:
        sender = await make_peer(bus, authority, "a")
        receiver = await make_peer(bus, authority, "b", topic="code.requested")
        receiver.mesh.verifier._clock = lambda: time.time() + 7200  # noqa: SLF001
        await sender.mesh.publish(_event("a"), "code.requested")
        await _settle()
        assert receiver.events == []
        assert receiver.refusals[0][0].reason is AttestationReason.STALE

    async def test_an_event_from_the_future_is_stale(self, bus, authority) -> None:
        sender = await make_peer(bus, authority, "a")
        receiver = await make_peer(bus, authority, "b", topic="code.requested")
        receiver.mesh.verifier._clock = lambda: time.time() - 7200  # noqa: SLF001
        await sender.mesh.publish(_event("a"), "code.requested")
        await _settle()
        assert receiver.refusals[0][0].reason is AttestationReason.STALE

    async def test_a_sender_outside_every_shared_realm_is_not_a_member(
        self, bus, authority
    ) -> None:
        sender = await make_peer(bus, authority, "a", memberships=("tenant:other",))
        receiver = await make_peer(bus, authority, "b", topic="code.requested")
        await sender.mesh.publish(_event("a"), "code.requested")
        await _settle()
        assert receiver.events == []
        assert receiver.refusals[0][0].reason is AttestationReason.NOT_MEMBER

    async def test_one_shared_flock_is_enough_across_owners_and_tenants(
        self, bus, authority
    ) -> None:
        sender = await make_peer(bus, authority, "a", memberships=("tenant:t1", "flock:f1"))
        receiver = await make_peer(
            bus, authority, "b", memberships=("tenant:t2", "flock:f1"), topic="code.requested"
        )
        await sender.mesh.publish(_event("a"), "code.requested")
        await _settle()
        assert len(receiver.events) == 1

    async def test_a_resident_publishes_under_its_own_peer_id(self, bus, authority) -> None:
        resident = await make_peer(bus, authority, "muninn", kind=KIND_RESIDENT)
        receiver = await make_peer(bus, authority, "b", topic="code.requested")
        await resident.mesh.publish(_event("muninn", session_id="anything"), "code.requested")
        await _settle()
        assert len(receiver.events) == 1


class TestEventBindingUnit:
    def test_bind_event_accepts_matching_publisher(self) -> None:
        from niuu.mesh.attestation import VerifiedSender

        sender = VerifiedSender("p", KIND_SESSION, "s", "l", ("t",), 0)
        bind_event(_event("p", payload={"publisher": {"session_id": "s", "peer_id": "p"}}), sender)
        bind_event(_event("p", payload={"publisher": "not-a-block"}), sender)


# ---------------------------------------------------------------------------
# RPC
# ---------------------------------------------------------------------------


def _envelope(sender_kind: str, sender_id: str, **extra: Any) -> dict:
    envelope = {
        "version": 1,
        "message_id": "m1",
        "sender": {"kind": sender_kind, "id": sender_id, "name": "", "node": ""},
        "recipient": {"kind": "session", "id": "sess-b", "name": "", "node": ""},
        "sent_at": datetime.now(UTC).isoformat(),
        "content": "hello",
        "reply": None,
        "in_reply_to": "",
        "room_id": "",
        "relayed_by": None,
        "urgent": False,
    }
    envelope.update(extra)
    return envelope


class TestRpc:
    async def test_signed_request_and_reply_round_trip(self, bus, authority) -> None:
        a = await make_peer(bus, authority, "a")
        b = await make_peer(bus, authority, "b")
        reply = await a.mesh.send("b", {"type": "peer_state"})
        assert reply == {"status": "accepted", "echo": "peer_state"}
        # The handler is told who was verified, over anything the sender wrote.
        assert b.requests == [
            {"type": "peer_state", "verified_sender": {"peer_id": "a", "session_id": "sess-a"}}
        ]
        assert "attestation" not in reply

    async def test_a_session_envelope_and_mesh_reply_route_are_bound(self, bus, authority) -> None:
        a = await make_peer(bus, authority, "a")
        b = await make_peer(bus, authority, "b")
        envelope = _envelope("session", "sess-a", reply={"kind": "mesh", "target": "a", "node": ""})
        reply = await a.mesh.send("b", {"type": "peer_message", "envelope": envelope})
        assert reply["status"] == "accepted"
        assert b.requests[0]["envelope"]["sender"]["id"] == "sess-a"

    async def test_an_envelope_naming_another_session_is_refused_and_signed(
        self, bus, authority
    ) -> None:
        evil = await make_peer(bus, authority, "evil")
        b = await make_peer(bus, authority, "b")
        envelope = _envelope("session", "sess-victim")
        reply = await evil.mesh.send("b", {"type": "peer_message", "envelope": envelope})
        assert reply["status"] == "refused"
        assert reply["reason"] == "binding_mismatch"
        assert b.requests == []
        assert b.refusals[0][1] == "request"

    @pytest.mark.parametrize(
        ("message", "detail"),
        [
            ({"type": "work_request", "source_peer_id": "victim", "prompt": "p"}, "source_peer_id"),
            (
                {"type": "peer_message", "envelope": _envelope("mesh_peer", "victim")},
                "envelope sender",
            ),
            (
                {"type": "peer_message", "envelope": _envelope("person", "u1")},
                "envelope sender",
            ),
            (
                {
                    "type": "peer_message",
                    "envelope": _envelope(
                        "session", "sess-evil", reply={"kind": "mesh", "target": "victim"}
                    ),
                },
                "reply route",
            ),
            (
                {
                    "type": "peer_message",
                    "envelope": _envelope(
                        "session", "sess-evil", reply={"kind": "room", "target": "r"}
                    ),
                },
                "reply route",
            ),
            ({"type": "peer_message", "envelope": "nope"}, "not an object"),
            (
                {"type": "peer_message", "envelope": _envelope("session", "sess-evil", reply="x")},
                "reply",
            ),
            (
                {
                    "type": "peer_message",
                    "envelope": _envelope(
                        "session", "sess-evil", relayed_by={"kind": "session", "id": "other"}
                    ),
                },
                "relayed_by",
            ),
            ({"type": "peer_message", "envelope": {"sender": None}}, "no sender"),
        ],
    )
    async def test_claims_about_the_sender_must_match_the_certificate(
        self, bus, authority, message, detail
    ) -> None:
        evil = await make_peer(bus, authority, "evil")
        b = await make_peer(bus, authority, "b")
        reply = await evil.mesh.send("b", message)
        assert reply["status"] == "refused"
        assert detail in reply["error"]
        assert b.requests == []

    async def test_a_relaying_host_may_vouch_for_the_speaker(self, bus, authority) -> None:
        host = await make_peer(bus, authority, "host")
        b = await make_peer(bus, authority, "b")
        envelope = _envelope(
            "person",
            "u1",
            relayed_by={"kind": "session", "id": "sess-host"},
            reply={"kind": "room", "target": "r1"},
        )
        reply = await host.mesh.send("b", {"type": "peer_message", "envelope": envelope})
        assert reply["status"] == "accepted"
        assert len(b.requests) == 1

    async def test_unsigned_request_is_refused_by_name(self, bus, authority) -> None:
        b = await make_peer(bus, authority, "b")
        raw = await make_peer(bus, authority, "raw", start=False)
        # A member that talks to the transport directly and signs nothing.
        reply = await raw.inner.send("b", {"type": "peer_state"})
        assert reply["status"] == "refused"
        assert reply["reason"] == "missing"
        assert b.requests == []

    async def test_a_request_for_another_peer_cannot_be_replayed_here(self, bus, authority) -> None:
        a = await make_peer(bus, authority, "a")
        b = await make_peer(bus, authority, "b")
        c = await make_peer(bus, authority, "c")
        captured = {"type": "peer_state"}
        attestation = a.signer.attest("rpc", {"to": "b", "message": captured})
        reply = await a.inner.send("c", {**captured, "attestation": attestation})
        assert reply["status"] == "refused"
        assert reply["reason"] == "signature_invalid"
        assert c.requests == [] and b.requests == []

    async def test_a_stale_request_is_refused(self, bus, authority) -> None:
        a = await make_peer(bus, authority, "a")
        b = await make_peer(bus, authority, "b")
        b.mesh.verifier._clock = lambda: time.time() + 3600  # noqa: SLF001
        reply = await a.mesh.send("b", {"type": "peer_state"})
        assert reply["reason"] == "stale"

    async def test_a_handler_that_raises_answers_with_a_signed_error(self, bus, authority) -> None:
        a = await make_peer(bus, authority, "a")
        b = await make_peer(bus, authority, "b")

        async def boom(message: dict) -> dict:
            raise RuntimeError("kaput")

        b.mesh.set_rpc_handler(boom)
        assert await a.mesh.send("b", {"type": "peer_state"}) == {"error": "kaput"}

    async def test_an_impostor_reply_is_refused(self, bus, authority) -> None:
        """C hears a request for B on the bus and answers first with a forged 'accepted'."""
        a = await make_peer(bus, authority, "a")
        c = await make_peer(bus, authority, "c", start=False)
        # No B is running: only the impostor answers, signing as itself.
        request_seen: list[Any] = []

        async def impostor(event: Any) -> None:
            request_seen.append(event)
            payload = event.payload
            request = payload["rpc_request"]
            body = {key: value for key, value in request.items() if key != "attestation"}
            from niuu.mesh.attestation import digest

            subject = {"to": "b", "message": body}
            response = {"status": "accepted"}
            attestation = c.signer.attest(
                "rpc_reply", {"request": digest(subject), "response": response}
            )
            from sleipnir.domain.events import SleipnirEvent

            await bus.publish(
                SleipnirEvent(
                    event_type=payload["reply_topic"],
                    source="ravn:b",
                    payload={"rpc_response": {**response, "attestation": attestation}},
                    summary="forged",
                    urgency=0.5,
                    domain="code",
                    timestamp=datetime.now(UTC),
                    correlation_id=event.correlation_id,
                )
            )

        await bus.subscribe(["mesh.rpc.b"], impostor)
        with pytest.raises(AttestationRefusedError) as raised:
            await a.mesh.send("b", {"type": "peer_state"})
        assert raised.value.reason is AttestationReason.BINDING_MISMATCH
        assert "c" in str(raised.value)
        assert refusal_counts()["reply.binding_mismatch"] == 1

    async def test_an_unsigned_reply_is_refused_and_shows_what_it_said(
        self, bus, authority
    ) -> None:
        a = await make_peer(bus, authority, "a")
        plain = SleipnirMeshAdapter(
            publisher=bus,
            subscriber=bus,
            own_peer_id="b",
            rpc_reply_cache_size=4,
            manage_transport_lifecycle=False,
        )

        async def unsigned(message: dict) -> dict:
            return {"status": "accepted"}

        plain.set_rpc_handler(unsigned)
        await plain.start()
        with pytest.raises(AttestationRefusedError) as raised:
            await a.mesh.send("b", {"type": "peer_state"})
        assert raised.value.reason is AttestationReason.MISSING
        assert "unsigned reply" in str(raised.value)

    async def test_a_reply_replayed_for_another_request_is_refused(self, bus, authority) -> None:
        a = await make_peer(bus, authority, "a")
        await make_peer(bus, authority, "b")
        first = await a.mesh.send("b", {"type": "one"})
        assert first["echo"] == "one"
        # Verifying the earlier reply against a different request digest fails.
        with pytest.raises(AttestationRefusedError):
            a.mesh._verify_reply({"status": "accepted"}, "b", "0" * 64)  # noqa: SLF001

    async def test_a_non_object_reply_is_malformed(self, bus, authority) -> None:
        a = await make_peer(bus, authority, "a")
        with pytest.raises(AttestationRefusedError) as raised:
            a.mesh._verify_reply("nope", "b", "x")  # noqa: SLF001
        assert raised.value.reason is AttestationReason.MALFORMED


# ---------------------------------------------------------------------------
# Verifier failure reasons
# ---------------------------------------------------------------------------


class TestVerifierReasons:
    async def _setup(self, authority: Authority):
        signer = MeshSigner(authority.source(session_id="s1"), "p1")
        await signer.start()
        verifier = MeshAttestationVerifier(
            lambda: signer.jwks, issuer=signer.issuer, own_memberships=lambda: ("tenant:t1",)
        )
        return signer, verifier

    async def test_malformed_attestations(self, authority) -> None:
        signer, verifier = await self._setup(authority)
        good = signer.attest("rpc", {"a": 1})
        for bad in (
            "string",
            {**good, "v": 2},
            {**good, "cert": 5},
            {**good, "at": "now"},
            {**good, "at": True},
            {k: v for k, v in good.items() if k != "sig"},
        ):
            with pytest.raises(AttestationRefusedError) as raised:
                verifier.verify(bad, "rpc", {"a": 1})
            assert raised.value.reason is AttestationReason.MALFORMED
        with pytest.raises(AttestationRefusedError) as raised:
            verifier.verify(None, "rpc", {"a": 1})
        assert raised.value.reason is AttestationReason.MISSING

    async def test_certificate_that_is_not_a_jwt(self, authority) -> None:
        signer, verifier = await self._setup(authority)
        good = signer.attest("rpc", {"a": 1})
        with pytest.raises(AttestationRefusedError) as raised:
            verifier.verify({**good, "cert": "garbage"}, "rpc", {"a": 1})
        assert raised.value.reason is AttestationReason.MALFORMED

    async def test_certificate_from_an_unknown_issuer_key_is_unknown_key(self, authority) -> None:
        signer, verifier = await self._setup(authority)
        stranger = Authority(_signing_service(key_id="other-kid"))
        foreign = MeshSigner(stranger.source(session_id="s9"), "p9")
        await foreign.start()
        with pytest.raises(AttestationRefusedError) as raised:
            verifier.verify(foreign.attest("rpc", {"a": 1}), "rpc", {"a": 1})
        assert raised.value.reason is AttestationReason.UNKNOWN_KEY

    async def test_certificate_signed_by_another_key_with_the_same_kid_is_invalid(
        self, authority
    ) -> None:
        signer, verifier = await self._setup(authority)
        stranger = Authority(_signing_service())  # same issuer and kid, different RSA key
        foreign = MeshSigner(stranger.source(session_id="s9"), "p9")
        await foreign.start()
        with pytest.raises(AttestationRefusedError) as raised:
            verifier.verify(foreign.attest("rpc", {"a": 1}), "rpc", {"a": 1})
        assert raised.value.reason is AttestationReason.CERT_INVALID

    async def test_empty_trust_is_trust_unavailable(self, authority) -> None:
        signer, _ = await self._setup(authority)
        verifier = MeshAttestationVerifier(
            lambda: {"keys": [{"kty": "bogus"}]},
            issuer=signer.issuer,
            own_memberships=lambda: (),
        )
        with pytest.raises(AttestationRefusedError) as raised:
            verifier.verify(signer.attest("rpc", {}), "rpc", {})
        assert raised.value.reason is AttestationReason.TRUST_UNAVAILABLE

    async def test_wrong_issuer_name_is_invalid(self, authority) -> None:
        signer, _ = await self._setup(authority)
        verifier = MeshAttestationVerifier(
            lambda: signer.jwks, issuer="someone-else", own_memberships=lambda: ()
        )
        with pytest.raises(AttestationRefusedError) as raised:
            verifier.verify(signer.attest("rpc", {}), "rpc", {})
        assert raised.value.reason is AttestationReason.CERT_INVALID

    async def test_expired_certificate(self, authority) -> None:
        signer, _ = await self._setup(authority)
        verifier = MeshAttestationVerifier(
            lambda: signer.jwks,
            issuer=signer.issuer,
            own_memberships=lambda: ("tenant:t1",),
            clock=lambda: time.time() + TTL_S + 60,
            clock_skew_s=10_000,
        )
        # Signed while valid, but judged at signing time it is fine: an RPC signed now is
        # checked at "now", which this verifier believes is past expiry.
        attestation = signer.attest("rpc", {})
        attestation["at"] = int((time.time() + TTL_S + 30) * 1000)
        with pytest.raises(AttestationRefusedError) as raised:
            verifier.verify(attestation, "rpc", {})
        assert raised.value.reason is AttestationReason.CERT_EXPIRED
        assert raised.value.claimed == "p1"

    async def test_a_jwt_that_is_not_a_mesh_certificate(self, authority) -> None:
        signer, verifier = await self._setup(authority)
        signing = authority.issuer._signing  # noqa: SLF001
        now = int(time.time())
        token = signing.sign_claims(
            {
                "iss": signer.issuer,
                "aud": ["niuu-mesh"],
                "iat": now,
                "exp": now + 60,
                "token_use": "forge_session",
            }
        )
        with pytest.raises(AttestationRefusedError) as raised:
            verifier.read_certificate(token)
        assert raised.value.reason is AttestationReason.CERT_INVALID

    async def test_certificate_without_the_mesh_claims(self, authority) -> None:
        signer, verifier = await self._setup(authority)
        signing = authority.issuer._signing  # noqa: SLF001
        now = int(time.time())
        token = signing.sign_claims(
            {
                "iss": signer.issuer,
                "aud": ["niuu-mesh"],
                "iat": now,
                "exp": now + 60,
                "token_use": "mesh_peer",
            }
        )
        with pytest.raises(AttestationRefusedError, match="lacks a peer id"):
            verifier.read_certificate(token)

    async def test_certificate_with_a_key_that_is_not_ed25519(self, authority) -> None:
        signer, verifier = await self._setup(authority)
        signing = authority.issuer._signing  # noqa: SLF001
        now = int(time.time())
        token = signing.sign_claims(
            {
                "iss": signer.issuer,
                "aud": ["niuu-mesh"],
                "iat": now,
                "exp": now + 60,
                "token_use": "mesh_peer",
                "workload_peer_id": "p",
                "workload_kind": "session",
                "workload_memberships": [],
                "workload_key": b64url(b"short"),
            }
        )
        with pytest.raises(AttestationRefusedError, match="not an Ed25519"):
            verifier.read_certificate(token)

    async def test_signed_before_the_certificate_existed(self, authority) -> None:
        signer, verifier = await self._setup(authority)
        attestation = signer.attest("rpc", {})
        attestation["at"] = int((signer.certificate.issued_at - 3600) * 1000)
        with pytest.raises(AttestationRefusedError) as raised:
            verifier.verify(attestation, "rpc", {}, max_age_ms=10**9)
        # The signature covers `at`, so rewriting it is also a bad signature; either way
        # it is refused.
        assert raised.value.reason in {
            AttestationReason.SIGNATURE_INVALID,
            AttestationReason.STALE,
        }

    async def test_the_wrong_purpose_does_not_verify(self, authority) -> None:
        signer, verifier = await self._setup(authority)
        attestation = signer.attest("rpc", {"a": 1})
        with pytest.raises(AttestationRefusedError) as raised:
            verifier.verify(attestation, "event", {"a": 1})
        assert raised.value.reason is AttestationReason.SIGNATURE_INVALID

    async def test_a_signature_that_is_not_base64(self, authority) -> None:
        signer, verifier = await self._setup(authority)
        attestation = {**signer.attest("rpc", {}), "sig": "***"}
        with pytest.raises(AttestationRefusedError) as raised:
            verifier.verify(attestation, "rpc", {})
        assert raised.value.reason is AttestationReason.SIGNATURE_INVALID


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


class TestRevocation:
    async def _revocation(self, authority, bus, session_id: str, launch_id: str) -> None:
        forge = await make_peer(bus, authority, "forge", kind=KIND_AUTHORITY, start=False)
        event = MeshEvent(
            type=MeshEventType.OUTCOME,
            source="forge",
            payload={"session_id": session_id, "launch_id": launch_id, "peer_id": "a"},
            timestamp=datetime.now(UTC),
            urgency=0.9,
            correlation_id=session_id,
            session_id="",
        )
        await forge.mesh.publish(event, REVOCATION_TOPIC)

    async def test_a_stopped_session_is_dropped_at_once(self, bus, authority) -> None:
        a = await make_peer(bus, authority, "a")
        b = await make_peer(bus, authority, "b", topic="code.requested")
        await a.mesh.publish(_event("a"), "code.requested")
        await _settle()
        assert len(b.events) == 1

        await self._revocation(authority, bus, "sess-a", "launch-1")
        await _settle()
        assert ("sess-a", "launch-1") in b.mesh.verifier.revoked()

        await a.mesh.publish(_event("a"), "code.requested")
        reply = await a.mesh.send("b", {"type": "peer_state"})
        await _settle()
        assert len(b.events) == 1
        assert reply["reason"] == "revoked"
        assert b.refusals[-1][0].reason is AttestationReason.REVOKED

    async def test_a_restarted_session_is_not_dropped_for_its_old_launch(
        self, bus, authority
    ) -> None:
        old = await make_peer(bus, authority, "a", launch_id="launch-1", start=False)
        new = await make_peer(bus, authority, "a2", session_id="sess-a", launch_id="launch-2")
        b = await make_peer(bus, authority, "b", topic="code.requested")
        await self._revocation(authority, bus, "sess-a", "launch-1")
        await _settle()
        await new.mesh.publish(_event("a2"), "code.requested")
        await _settle()
        assert len(b.events) == 1
        del old

    async def test_only_forge_can_revoke(self, bus, authority) -> None:
        a = await make_peer(bus, authority, "a")
        b = await make_peer(bus, authority, "b")
        mallory = await make_peer(bus, authority, "mallory", start=False)
        event = MeshEvent(
            type=MeshEventType.OUTCOME,
            source="mallory",
            payload={"session_id": "sess-a", "launch_id": "launch-1"},
            timestamp=datetime.now(UTC),
            urgency=0.9,
            correlation_id="x",
            session_id="",
        )
        await mallory.mesh.publish(event, REVOCATION_TOPIC)
        await _settle()
        assert b.mesh.verifier.revoked() == {}
        assert b.refusals[-1][0].reason is AttestationReason.NOT_AUTHORITY
        assert b.refusals[-1][1] == "revocation"
        assert (await a.mesh.send("b", {"type": "peer_state"}))["status"] == "accepted"

    async def test_an_unsigned_or_malformed_revocation_is_refused(self, bus, authority) -> None:
        b = await make_peer(bus, authority, "b")
        raw = await make_peer(bus, authority, "raw", start=False)
        await raw.inner.publish(
            _event("raw", payload={"session_id": "s", "launch_id": "l"}), REVOCATION_TOPIC
        )
        await _settle()
        assert b.refusals[-1][0].reason is AttestationReason.MISSING

        forge = await make_peer(bus, authority, "forge", kind=KIND_AUTHORITY, start=False)
        await forge.mesh.publish(_event("forge", payload={"session_id": "s"}), REVOCATION_TOPIC)
        await _settle()
        assert b.refusals[-1][0].reason is AttestationReason.MALFORMED
        assert b.mesh.verifier.revoked() == {}

    async def test_a_revocation_is_forgotten_when_its_certificates_cannot_matter(self) -> None:
        now = {"t": 1000.0}
        verifier = MeshAttestationVerifier(
            lambda: {"keys": []},
            issuer="i",
            own_memberships=lambda: (),
            revocation_retain_s=60,
            clock=lambda: now["t"],
        )
        verifier.revoke("s", "l")
        assert verifier.revoked() == {("s", "l"): 1_060_000}
        now["t"] += 61
        assert verifier.revoked() == {}


# ---------------------------------------------------------------------------
# Decorator surface
# ---------------------------------------------------------------------------


class TestDecoratorSurface:
    async def test_unknown_attributes_come_from_the_wrapped_mesh(self, bus, authority) -> None:
        a = await make_peer(bus, authority, "a")
        assert a.mesh.subscriber is bus
        assert a.mesh.inner is a.inner

    async def test_unsubscribe_and_stop_release_everything(self, bus, authority) -> None:
        a = await make_peer(bus, authority, "a", topic="code.requested")
        await a.mesh.unsubscribe("code.requested")
        await a.mesh.stop()

    async def test_verified_subscriber_hands_on_only_verified_mesh_events(
        self, bus, authority
    ) -> None:
        sender = await make_peer(bus, authority, "a")
        listener = await make_peer(bus, authority, "b")
        seen: list[Any] = []

        async def handler(event: Any) -> None:
            seen.append(event)

        subscriber = listener.mesh.verified_subscriber(bus, event_prefix="mesh")
        await subscriber.subscribe(["mesh.*"], handler)

        await sender.mesh.publish(_event("a"), "code.requested")
        await _settle()
        verified = [e for e in seen if e.payload.get("ravn_source") == "a"]
        assert len(verified) == 1
        assert verified[0].source == "ravn:a"

        # An event nobody signed is not handed on, and is counted.
        before = len(seen)
        evil = await make_peer(bus, authority, "evil", start=False)
        await evil.inner.publish(_event("a"), "code.requested")
        await _settle()
        assert len(seen) == before
        assert refusal_counts().get("event.missing", 0) >= 1

    async def test_verified_subscriber_recomputes_the_projection_and_source(
        self, bus, authority
    ) -> None:
        sender = await make_peer(bus, authority, "a")
        listener = await make_peer(bus, authority, "b")
        seen: list[Any] = []

        async def handler(event: Any) -> None:
            seen.append(event)

        await listener.mesh.verified_subscriber(bus, event_prefix="mesh").subscribe(
            ["mesh.*"], handler
        )
        event = _event("a", payload={"text": "hi"})
        event = replace(event, type=MeshEventType.RESPONSE)
        await sender.mesh.publish(event, "chat.said")
        await _settle()
        message = next(e for e in seen if e.payload.get("ravn_source") == "a")
        # The consumer sees the projection computed from the signed event.
        assert message.payload["collaboration_events"]
        assert message.payload["collaboration_events"][0]["content"] == "hi"

    async def test_verified_subscriber_ignores_non_mesh_events(self, bus, authority) -> None:
        listener = await make_peer(bus, authority, "b")
        seen: list[Any] = []

        async def handler(event: Any) -> None:
            seen.append(event)

        await listener.mesh.verified_subscriber(bus, event_prefix="mesh").subscribe(
            ["mesh.*"], handler
        )
        peer = await make_peer(bus, authority, "a")
        await peer.mesh.send("b", {"type": "peer_state"})
        await _settle()
        assert all("ravn_type" in e.payload for e in seen)

    async def test_event_on_a_different_topic_than_signed_is_refused_by_the_subscriber(
        self, bus, authority
    ) -> None:
        sender = await make_peer(bus, authority, "a")
        listener = await make_peer(bus, authority, "b")
        seen: list[Any] = []

        async def handler(event: Any) -> None:
            seen.append(event)

        await listener.mesh.verified_subscriber(bus, event_prefix="mesh").subscribe(
            ["mesh.*"], handler
        )
        from niuu.adapters.mesh.attested import event_subject

        captured = _event("a")
        signed = replace(
            captured,
            attestation=sender.signer.attest(
                PURPOSE_EVENT,
                event_subject(captured, "code.requested"),
                extra={"topic": "code.requested"},
            ),
        )
        await sender.inner.publish(signed, "admin.shutdown")
        await _settle()
        assert not [e for e in seen if e.payload.get("ravn_source") == "a"]
        assert listener.refusals[-1][0].reason is AttestationReason.BINDING_MISMATCH


def test_a_revocation_notice_is_not_projected_into_a_room() -> None:
    from niuu.mesh.projection import project_mesh_event

    event = replace(_event("forge"), type=MeshEventType.REVOCATION)
    assert project_mesh_event(event) == []
