"""A session's mesh identity: getting the certificate, failing closed, refusing impostors."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from niuu.domain.peer_message import (
    PeerAddress,
    PeerKind,
    PeerMessage,
    ReplyKind,
    ReplyRoute,
)
from niuu.domain.services.mesh_certificate import MeshCertificateIssuer
from niuu.domain.services.workload_identity import WorkloadIdentityService
from niuu.mesh.attestation import (
    AttestationReason,
    AttestationRefusedError,
    b64url_decode,
)
from niuu.mesh.config import MeshAttestationConfig
from niuu.mesh.participant import MeshParticipant
from skuld.config import MeshConfig, SkuldSettings
from skuld.mesh_adapter import SkuldMeshAdapter
from skuld.mesh_attestation import (
    ForgeCertificateSource,
    MeshAttestationUnavailableError,
    attest_mesh,
)
from skuld.peer_inbox import PeerAcceptance, PeerInboxMixin
from sleipnir.adapters.in_process import InProcessBus

SESSION_ID = "11111111-1111-1111-1111-111111111111"


def _issuer(ttl: int = 900) -> MeshCertificateIssuer:
    config = SimpleNamespace(
        enabled=True, issuer="niuu-test", audiences=["volundr-api"], key_id="k1"
    )
    return MeshCertificateIssuer(WorkloadIdentityService(config), ttl_seconds=ttl)


class FakeForge:
    """Forge's mesh-identity route, in process, as an httpx transport."""

    def __init__(self, issuer: MeshCertificateIssuer, *, memberships=("tenant:t1",)) -> None:
        self.issuer = issuer
        self.memberships = list(memberships)
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.body_override: dict[str, Any] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "peer id held by another session"})
        if self.body_override is not None:
            return httpx.Response(200, json=self.body_override)
        body = json.loads(request.content)
        session_id = request.url.path.split("/")[-2]
        issued = self.issuer.issue(
            peer_id=body["peer_id"],
            kind="session",
            public_key=b64url_decode(body["public_key"]),
            memberships=self.memberships,
            session_id=session_id,
            launch_id="launch-1",
            tenant_id="t1",
        )
        return httpx.Response(
            200,
            json={
                "certificate": issued.token,
                "jwks": issued.jwks,
                "issuer": issued.issuer,
                "ttl_seconds": issued.ttl_s,
                "expires_at": 0,
                "memberships": self.memberships,
            },
        )

    def client_factory(self):
        client = httpx.AsyncClient(
            base_url="http://forge", transport=httpx.MockTransport(self.handler)
        )

        async def get() -> httpx.AsyncClient:
            return client

        return get


async def _attested(
    mesh: Any,
    forge: FakeForge,
    peer_id: str,
    *,
    session_id: str = SESSION_ID,
    token: str = "session-token",
    on_refusal=None,
):
    return await attest_mesh(
        mesh,
        config=MeshAttestationConfig(),
        peer_id=peer_id,
        session_id=session_id,
        mesh_realm="realm-x",
        forge_token=token,
        http_client=forge.client_factory(),
        request_timeout_s=5.0,
        on_refusal=on_refusal or (lambda *a: None),
    )


def _inner(bus: InProcessBus, peer_id: str) -> SleipnirMeshAdapter:
    return SleipnirMeshAdapter(
        publisher=bus,
        subscriber=bus,
        own_peer_id=peer_id,
        rpc_timeout_s=2.0,
        rpc_reply_cache_size=8,
        manage_transport_lifecycle=False,
    )


class TestCertificateSource:
    async def test_asks_forge_for_this_sessions_certificate_with_its_credential(self) -> None:
        forge = FakeForge(_issuer())
        source = ForgeCertificateSource(
            http_client=forge.client_factory(),
            token="tok",
            session_id=SESSION_ID,
            mesh_realm="realm-x",
            timeout_s=5.0,
        )
        issued = await source.issue(b"\x01" * 32, "skuld-1")
        request = forge.requests[0]
        assert request.url.path == f"/api/v1/forge/sessions/{SESSION_ID}/mesh-identity"
        assert request.headers["authorization"] == "Bearer tok"
        body = json.loads(request.content)
        assert body["peer_id"] == "skuld-1" and body["mesh_realm"] == "realm-x"
        assert b64url_decode(body["public_key"]) == b"\x01" * 32
        assert issued.ttl_s == 900 and issued.jwks["keys"]

    async def test_a_refusal_from_forge_is_fatal_and_says_why(self) -> None:
        forge = FakeForge(_issuer())
        forge.status = 409
        source = ForgeCertificateSource(
            http_client=forge.client_factory(),
            token="tok",
            session_id=SESSION_ID,
            mesh_realm="",
            timeout_s=5.0,
        )
        with pytest.raises(MeshAttestationUnavailableError, match="409.*held by another"):
            await source.issue(b"\x01" * 32, "skuld-1")

    async def test_an_unreachable_forge_is_fatal_with_the_remedy(self) -> None:
        async def get() -> httpx.AsyncClient:
            def boom(request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("refused")

            return httpx.AsyncClient(base_url="http://forge", transport=httpx.MockTransport(boom))

        source = ForgeCertificateSource(
            http_client=get, token="tok", session_id=SESSION_ID, mesh_realm="", timeout_s=1.0
        )
        with pytest.raises(MeshAttestationUnavailableError, match="mesh.attestation.required"):
            await source.issue(b"\x01" * 32, "skuld-1")

    async def test_a_malformed_answer_is_fatal(self) -> None:
        forge = FakeForge(_issuer())
        forge.body_override = {"certificate": "x"}
        source = ForgeCertificateSource(
            http_client=forge.client_factory(),
            token="tok",
            session_id=SESSION_ID,
            mesh_realm="",
            timeout_s=1.0,
        )
        with pytest.raises(MeshAttestationUnavailableError, match="malformed"):
            await source.issue(b"\x01" * 32, "skuld-1")

    async def test_a_non_json_refusal_body_is_still_reported(self) -> None:
        async def get() -> httpx.AsyncClient:
            return httpx.AsyncClient(
                base_url="http://forge",
                transport=httpx.MockTransport(lambda r: httpx.Response(503, text="no mesh")),
            )

        source = ForgeCertificateSource(
            http_client=get, token="tok", session_id=SESSION_ID, mesh_realm="", timeout_s=1.0
        )
        with pytest.raises(MeshAttestationUnavailableError, match="503.*no mesh"):
            await source.issue(b"\x01" * 32, "skuld-1")


class TestFailsClosed:
    async def test_no_forge_credential_is_fatal_and_names_the_remedies(self) -> None:
        forge = FakeForge(_issuer())
        with pytest.raises(MeshAttestationUnavailableError) as raised:
            await _attested(object(), forge, "skuld-1", token="")
        message = str(raised.value)
        for remedy in ("forge_mcp.token", "projection_adapter", "mesh.attestation.required"):
            assert remedy in message
        assert forge.requests == []

    async def test_forge_refusing_the_certificate_stops_the_mesh_from_starting(self) -> None:
        forge = FakeForge(_issuer())
        forge.status = 409
        with pytest.raises(MeshAttestationUnavailableError, match="409"):
            await _attested(object(), forge, "skuld-1")

    async def test_the_broker_needs_forge_url_and_credential(self, tmp_path, monkeypatch) -> None:
        from skuld.broker import Broker

        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])
        settings = SkuldSettings(
            session={"id": SESSION_ID, "workspace_dir": str(tmp_path)},
            mesh={"enabled": True, "peer_id": "p", "transport": "in_process"},
        )
        broker = Broker(settings=settings)
        with pytest.raises(MeshAttestationUnavailableError, match="volundr_api_url"):
            await broker._attest_mesh(object(), "p")
        broker.volundr_api_url = "http://forge"
        with pytest.raises(MeshAttestationUnavailableError, match="forge_mcp.token"):
            await broker._attest_mesh(object(), "p")

    async def test_the_broker_wraps_the_mesh_once_certified(self, tmp_path, monkeypatch) -> None:
        from skuld.broker import Broker

        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])
        forge = FakeForge(_issuer())
        settings = SkuldSettings(
            session={"id": SESSION_ID, "workspace_dir": str(tmp_path)},
            volundr_api_url="http://forge",
            forge_mcp={"token": "session-token"},
            mesh={"enabled": True, "peer_id": "p", "transport": "in_process", "realm_id": "r"},
        )
        broker = Broker(settings=settings)
        monkeypatch.setattr(broker, "_get_http_client", forge.client_factory())
        inner = _inner(InProcessBus(), "p")
        attested = await broker._attest_mesh(inner, "p")
        assert broker._mesh_attested is attested
        assert attested.inner is inner
        body = json.loads(forge.requests[0].content)
        assert body["peer_id"] == "p" and body["mesh_realm"] == "r"
        await attested.signer.stop()


class TestSpoofingAtTheInbox:
    class _Inbox:
        def __init__(self) -> None:
            self.accepted: list[PeerMessage] = []

        async def accept_peer_message(self, envelope, request_id=None, *, channel=None):
            self.accepted.append(envelope)
            return PeerAcceptance(envelope.message_id, duplicate=False)

    async def _world(self):
        bus = InProcessBus()
        forge = FakeForge(_issuer())
        refusals: list[tuple[AttestationRefusedError, str, str]] = []
        victim_mesh = await _attested(
            _inner(bus, "victim"),
            forge,
            "victim",
            session_id="22222222-2222-2222-2222-222222222222",
        )
        skuld_mesh = await _attested(
            _inner(bus, "skuld-1"), forge, "skuld-1", on_refusal=lambda *a: refusals.append(a)
        )
        evil_mesh = await _attested(
            _inner(bus, "evil"), forge, "evil", session_id="33333333-3333-3333-3333-333333333333"
        )
        inbox = self._Inbox()
        adapter = SkuldMeshAdapter(
            participant=MeshParticipant(mesh=skuld_mesh, discovery=None, peer_id="skuld-1"),
            config=MeshConfig(enabled=True, peer_id="skuld-1"),
            session_id=SESSION_ID,
            peer_inbox=inbox,
        )
        await adapter.start()
        await victim_mesh.start()
        await evil_mesh.start()
        return adapter, inbox, victim_mesh, evil_mesh, refusals

    @staticmethod
    def _envelope(sender: PeerAddress, reply: ReplyRoute | None = None) -> dict:
        return PeerMessage(
            message_id="m-1",
            sender=sender,
            recipient=PeerAddress(PeerKind.SESSION, SESSION_ID),
            sent_at=datetime.now(UTC),
            content="please wire the money",
            reply=reply,
        ).to_wire()

    async def test_a_genuine_sender_is_delivered_to_the_inbox(self) -> None:
        adapter, inbox, victim, evil, _ = await self._world()
        sender = PeerAddress(PeerKind.SESSION, "22222222-2222-2222-2222-222222222222")
        answer = await victim.send(
            "skuld-1",
            {
                "type": "peer_message",
                "envelope": self._envelope(sender, ReplyRoute(ReplyKind.MESH, "victim")),
            },
        )
        assert answer == {"status": "accepted", "message_id": "m-1"}
        assert [m.sender.id for m in inbox.accepted] == [sender.id]
        await adapter.stop()

    async def test_a_session_claiming_to_be_another_is_refused_before_the_inbox(self) -> None:
        adapter, inbox, victim, evil, refusals = await self._world()
        spoofed = PeerAddress(PeerKind.SESSION, "22222222-2222-2222-2222-222222222222")
        answer = await evil.send(
            "skuld-1", {"type": "peer_message", "envelope": self._envelope(spoofed)}
        )
        assert answer["status"] == "refused" and answer["reason"] == "binding_mismatch"
        assert inbox.accepted == []
        assert refusals[0][0].reason is AttestationReason.BINDING_MISMATCH
        await adapter.stop()

    async def test_a_work_request_naming_another_peer_is_refused(self) -> None:
        adapter, inbox, victim, evil, _ = await self._world()
        answer = await evil.send(
            "skuld-1",
            {"type": "work_request", "source_peer_id": "victim", "prompt": "do harm"},
        )
        assert answer["status"] == "refused"
        assert inbox.accepted == []
        await adapter.stop()

    async def test_an_outcome_event_naming_another_source_never_reaches_the_inbox(self) -> None:
        from niuu.mesh.events import MeshEvent, MeshEventType

        adapter, inbox, victim, evil, refusals = await self._world()
        event = MeshEvent(
            type=MeshEventType.OUTCOME,
            source="victim",
            payload={"prompt": "do harm"},
            timestamp=datetime.now(UTC),
            urgency=0.8,
            correlation_id="c",
            session_id="",
        )
        await evil.publish(event, "code.requested")
        await asyncio.sleep(0.05)
        assert inbox.accepted == []
        assert refusals and refusals[0][0].reason is AttestationReason.BINDING_MISMATCH
        await adapter.stop()


class _Host(PeerInboxMixin):
    session_id = SESSION_ID

    def __init__(self) -> None:
        self._init_peer_inbox()


class TestCounting:
    def test_an_unverified_sender_is_counted_as_a_refused_peer_message(self) -> None:
        host = _Host()
        host.record_unverified_mesh_sender(
            AttestationRefusedError(AttestationReason.SIGNATURE_INVALID, "bad", claimed="evil"),
            "request",
            "peer_message",
        )
        host.record_unverified_mesh_sender(
            AttestationRefusedError(AttestationReason.MISSING, "none"), "event", "code.requested"
        )
        pairs = {(p["sender"], p["recipient"]): p for p in host.peer_message_status()["pairs"]}
        assert pairs[("evil", SESSION_ID)]["refused"] == 1
        assert pairs[("unknown", SESSION_ID)]["refused"] == 1

    def test_status_reports_refusals_by_direction_and_reason(self) -> None:
        from niuu.adapters.mesh.attested import _refusals, reset_refusal_counts

        reset_refusal_counts()
        assert "unverified" not in _Host().peer_message_status()
        _refusals["request.missing"] += 2
        assert _Host().peer_message_status()["unverified"] == {"request.missing": 2}
        reset_refusal_counts()
