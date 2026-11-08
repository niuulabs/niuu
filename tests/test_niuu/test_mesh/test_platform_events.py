"""Platform events are signed by platform services and checked where they are acted on."""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.adapters.mesh.attested import refusal_counts, reset_refusal_counts
from niuu.collaboration.models import Participant
from niuu.collaboration.observation_relay import ObservationRelay
from niuu.mesh.attestation import (
    KIND_AUTHORITY,
    KIND_RESIDENT,
    KIND_SERVICE,
    KIND_SESSION,
    MeshAttestationVerifier,
    MeshSigner,
)
from niuu.mesh.config import MeshAttestationConfig
from niuu.mesh.platform_events import (
    ATTESTATION_KEY,
    PlatformEventVerifier,
    SigningBus,
)
from niuu.mesh.platform_identity import (
    HOST_KEY_ISSUER,
    PlatformIdentityUnavailableError,
    platform_event_verifier,
    start_platform_signer,
    verifier_for,
)
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent
from tests.test_niuu.test_mesh.test_attestation import Authority


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_refusal_counts()


def _event(event_type: str = "ting.plan.approved", **payload) -> SleipnirEvent:
    return SleipnirEvent(
        event_type=event_type,
        source="ting",
        payload=payload or {"plan": "p1"},
        summary="a plan was approved",
        urgency=0.5,
        domain="code",
        timestamp=datetime.now(UTC),
    )


async def _signer(authority: Authority, kind: str, peer: str = "service:ting") -> MeshSigner:
    session_id = "sess-1" if kind == KIND_SESSION else ""
    signer = MeshSigner(authority.source(kind=kind, session_id=session_id), peer)
    await signer.start()
    return signer


def _receiver(signer: MeshSigner, **kwargs) -> PlatformEventVerifier:
    return PlatformEventVerifier(
        MeshAttestationVerifier(
            lambda: signer.jwks, issuer=signer.issuer, own_memberships=lambda: ()
        ),
        event_max_age_s=3600,
        **kwargs,
    )


async def _published(signer: MeshSigner, event: SleipnirEvent) -> SleipnirEvent:
    bus = InProcessBus()
    seen: list[SleipnirEvent] = []

    async def handler(received: SleipnirEvent) -> None:
        seen.append(received)

    await bus.subscribe(["*"], handler)
    await SigningBus(bus, signer).publish(event)
    await bus.flush()
    return seen[0]


@pytest.fixture
def authority() -> Authority:
    return Authority()


class TestPlatformEvents:
    @pytest.mark.parametrize("kind", [KIND_SERVICE, KIND_AUTHORITY])
    async def test_a_signed_event_from_a_platform_service_is_accepted(
        self, authority, kind
    ) -> None:
        signer = await _signer(authority, kind)
        wire = await _published(signer, _event(plan="p1"))
        assert ATTESTATION_KEY in wire.payload

        accepted = _receiver(signer).accept(wire)

        assert accepted is not None
        assert accepted.payload == {"plan": "p1"}
        assert refusal_counts() == {}

    async def test_a_forged_unsigned_event_is_refused_with_the_reason_and_counted(
        self, authority, caplog
    ) -> None:
        signer = await _signer(authority, KIND_SERVICE)
        refusals = []
        receiver = _receiver(signer, on_refusal=lambda *a: refusals.append(a))
        with caplog.at_level(logging.ERROR):
            assert receiver.accept(_event(plan="approve everything")) is None
        assert refusals[0][0].reason.value == "missing"
        assert refusals[0][1:] == ("platform_event", "ting.plan.approved")
        assert refusal_counts() == {"platform_event.missing": 1}
        assert "refused a platform_event" in caplog.text

    async def test_an_altered_event_fails_its_signature(self, authority) -> None:
        signer = await _signer(authority, KIND_SERVICE)
        wire = await _published(signer, _event(plan="p1"))
        altered = SleipnirEvent(**{**wire.__dict__, "payload": {**wire.payload, "plan": "p2"}})
        assert _receiver(signer).accept(altered) is None
        assert refusal_counts() == {"platform_event.signature_invalid": 1}

    async def test_a_mesh_peers_certificate_cannot_pass_as_a_platform_service(
        self, authority
    ) -> None:
        trust = await _signer(authority, KIND_SERVICE)
        for kind in (KIND_SESSION, KIND_RESIDENT):
            peer = await _signer(authority, kind, peer=f"peer-{kind}")
            wire = await _published(peer, _event())
            assert _receiver(trust).accept(wire) is None
        assert refusal_counts() == {"platform_event.not_platform_service": 2}

    async def test_a_stale_event_is_refused(self, authority) -> None:
        signer = await _signer(authority, KIND_SERVICE)
        wire = await _published(signer, _event())
        late = PlatformEventVerifier(
            MeshAttestationVerifier(
                lambda: signer.jwks,
                issuer=signer.issuer,
                own_memberships=lambda: (),
                clock=lambda: time.time() + 7200,
            ),
            event_max_age_s=3600,
        )
        assert late.accept(wire) is None
        assert refusal_counts() == {"platform_event.stale": 1}

    async def test_the_meshs_own_events_are_not_touched(self, authority) -> None:
        signer = await _signer(authority, KIND_SERVICE)
        wire = await _published(signer, _event("mesh.realm_x.code", note="peer-signed"))
        assert ATTESTATION_KEY not in wire.payload

    async def test_a_batch_is_signed_and_the_bus_is_otherwise_the_wrapped_one(
        self, authority
    ) -> None:
        signer = await _signer(authority, KIND_SERVICE)
        bus = InProcessBus()
        seen: list[SleipnirEvent] = []

        async def handler(received: SleipnirEvent) -> None:
            seen.append(received)

        wrapped = SigningBus(bus, signer)
        await wrapped.subscribe(["*"], handler)
        await wrapped.publish_batch([_event(), _event("ting.gate.opened", gate="g1")])
        await bus.flush()
        assert wrapped.signer is signer
        assert [ATTESTATION_KEY in e.payload for e in seen] == [True, True]


def _participant(subscribes_to: list[str]) -> Participant:
    return Participant(
        peer_id="resident",
        persona="resident",
        participant_type="ravn",
        subscribes_to=subscribes_to,
        color="#fff",
    )


class TestObservationRelay:
    async def _relay(self, verifier, subscribes_to=("ting.plan.*",)):
        bus = InProcessBus()
        directed = AsyncMock(return_value="ok")
        relay = ObservationRelay(
            bus,
            participant=lambda: _participant(list(subscribes_to)),
            patterns=["*"],
            send_directed=directed,
            broadcast_notification=AsyncMock(),
            payload_preview_chars=2000,
            platform_verifier=verifier,
        )
        await relay.start()
        return bus, relay, directed

    async def test_a_forged_event_is_not_relayed_and_a_signed_one_is(self, authority) -> None:
        signer = await _signer(authority, KIND_SERVICE)
        bus, relay, directed = await self._relay(_receiver(signer))

        await bus.publish(_event(plan="forged"))
        await bus.flush()
        assert directed.await_count == 0
        assert refusal_counts() == {"platform_event.missing": 1}

        await SigningBus(bus, signer).publish(_event(plan="real"))
        await bus.flush()
        assert directed.await_count == 1
        metadata = directed.await_args.args[2]
        assert metadata["payload"] == {"plan": "real"}
        assert ATTESTATION_KEY not in directed.await_args.args[1]
        await relay.stop()

    async def test_an_event_the_resident_did_not_subscribe_to_is_not_a_refusal(
        self, authority
    ) -> None:
        signer = await _signer(authority, KIND_SERVICE)
        bus, relay, directed = await self._relay(_receiver(signer))
        await bus.publish(_event("ting.billing.updated"))
        await bus.flush()
        assert directed.await_count == 0
        assert refusal_counts() == {}
        await relay.stop()

    async def test_with_signing_off_the_relay_behaves_as_before(self) -> None:
        bus, relay, directed = await self._relay(None)
        await bus.publish(_event(plan="unsigned"))
        await bus.flush()
        assert directed.await_count == 1
        await relay.stop()


class TestTingEventTrigger:
    def _adapter(self, verifier):
        from ting.adapters.event_trigger import EventTriggerAdapter, _TriggerRule

        adapter = EventTriggerAdapter(
            subscriber=InProcessBus(),
            saga_repo=MagicMock(),
            volundr_factory=MagicMock(),
            event_bus=MagicMock(),
            rules=[
                _TriggerRule(
                    event_pattern="ting.plan.approved",
                    saga_template="t",
                    auto_start=True,
                    filter={},
                )
            ],
            templates_dir=MagicMock(),
            owner_id="owner",
            platform_verifier=verifier,
        )
        adapter._trigger_saga = AsyncMock()  # noqa: SLF001
        return adapter

    async def test_a_forged_event_starts_no_saga_and_a_signed_one_does(self, authority) -> None:
        import asyncio

        signer = await _signer(authority, KIND_SERVICE)
        adapter = self._adapter(_receiver(signer))

        await adapter._handle_event(_event(plan="forged"))  # noqa: SLF001
        await asyncio.sleep(0)
        assert adapter._trigger_saga.await_count == 0  # noqa: SLF001
        assert refusal_counts() == {"platform_event.missing": 1}

        wire = await _published(signer, _event(plan="real"))
        await adapter._handle_event(wire)  # noqa: SLF001
        await asyncio.sleep(0)
        assert adapter._trigger_saga.await_count == 1  # noqa: SLF001
        assert ATTESTATION_KEY not in adapter._trigger_saga.await_args.args[0].payload  # noqa: SLF001

    async def test_an_event_no_rule_wants_is_not_checked(self, authority) -> None:
        signer = await _signer(authority, KIND_SERVICE)
        adapter = self._adapter(_receiver(signer))
        await adapter._handle_event(_event("ting.billing.updated"))  # noqa: SLF001
        assert refusal_counts() == {}

    async def test_with_signing_off_it_behaves_as_before(self) -> None:
        import asyncio

        adapter = self._adapter(None)
        await adapter._handle_event(_event(plan="unsigned"))  # noqa: SLF001
        await asyncio.sleep(0)
        assert adapter._trigger_saga.await_count == 1  # noqa: SLF001


class TestServiceIdentity:
    def _workload_identity(self, pem: str):
        return SimpleNamespace(
            enabled=True,
            issuer="niuu-workload",
            audiences=["volundr-api"],
            key_id="niuu-workload",
            signing_key_pem=pem,
            signing_key_env="",
        )

    async def test_off_signs_nothing_and_says_so_once(self, caplog) -> None:
        with caplog.at_level(logging.WARNING):
            signer = await start_platform_signer("ting", MeshAttestationConfig(required=False))
        assert signer is None
        assert platform_event_verifier(MagicMock(), MeshAttestationConfig(required=False)) is None
        assert caplog.text.count("mesh.attestation.required is false") == 1

    async def test_a_service_signs_with_the_deployments_workload_key(self) -> None:
        from niuu.domain.services.workload_identity import WorkloadIdentityService

        config = self._workload_identity("")
        forge = WorkloadIdentityService(config)
        wi = self._workload_identity(forge.private_key_pem_for_tests())
        mesh = MeshAttestationConfig()

        signer = await start_platform_signer("ting", mesh, workload_identity=wi)

        assert signer.peer_id == "service:ting"
        assert signer.certificate.kind == KIND_SERVICE
        assert signer.certificate.memberships == ()
        # Forge's own JWKS verifies the service's certificate: one trust anchor.
        verifier = MeshAttestationVerifier(
            lambda: forge.jwks(), issuer=forge.issuer, own_memberships=lambda: ()
        )
        assert verifier.read_certificate(signer.certificate.token).peer_id == "service:ting"
        await signer.stop()

    async def test_on_one_host_the_key_file_forge_signs_with_is_the_default(self, tmp_path) -> None:
        key_file = str(tmp_path / "forge-session-signing-key.pem")
        mesh = MeshAttestationConfig()

        ting = await start_platform_signer("ting", mesh, key_file=key_file)
        mimir = await start_platform_signer("mimir", mesh, key_file=key_file)

        assert ting.issuer == HOST_KEY_ISSUER
        # Two services of the host trust each other's certificates: same key, same JWKS.
        assert ting.jwks == mimir.jwks
        check = verifier_for(mimir, mesh)
        assert check.read_certificate(ting.certificate.token).kind == KIND_SERVICE
        assert platform_event_verifier(check, mesh) is not None
        await ting.stop()
        await mimir.stop()

    async def test_no_key_to_sign_with_is_fatal_with_the_remedy(self, tmp_path) -> None:
        blocked = tmp_path / "file"
        blocked.write_text("x")
        with pytest.raises(PlatformIdentityUnavailableError, match="mesh.attestation.required"):
            await start_platform_signer(
                "ting", MeshAttestationConfig(), key_file=str(blocked / "key.pem")
            )

    async def test_forge_signs_its_bus_with_its_authority_signer(self, authority) -> None:
        from volundr.composition_builders import _sign_forge_platform_events

        signer = await _signer(authority, KIND_AUTHORITY, peer="forge:node")
        settings = SimpleNamespace(mesh=SimpleNamespace(attestation=MeshAttestationConfig()))
        bus = InProcessBus()

        wrapped = await _sign_forge_platform_events(settings, bus, SimpleNamespace(signer=signer))

        assert isinstance(wrapped, SigningBus) and wrapped.signer is signer
        assert await _sign_forge_platform_events(settings, None, None) is None
        settings.mesh.attestation = MeshAttestationConfig(required=False)
        assert (
            await _sign_forge_platform_events(settings, bus, SimpleNamespace(signer=signer)) is bus
        )

    async def test_forge_without_an_authority_signs_as_a_service(self, tmp_path) -> None:
        from volundr.composition_builders import _sign_forge_platform_events

        settings = SimpleNamespace(
            mesh=SimpleNamespace(attestation=MeshAttestationConfig()),
            workload_identity=SimpleNamespace(enabled=False),
        )
        import niuu.mesh.platform_identity as identity

        original = identity.HOST_KEY_FILE
        identity.HOST_KEY_FILE = str(tmp_path / "k.pem")
        try:
            from unittest.mock import patch

            with patch(
                "volundr.composition_builders.start_platform_signer",
                AsyncMock(return_value=await _signer(Authority(), KIND_SERVICE, "service:forge")),
            ):
                wrapped = await _sign_forge_platform_events(settings, InProcessBus(), None)
        finally:
            identity.HOST_KEY_FILE = original
        assert isinstance(wrapped, SigningBus)
