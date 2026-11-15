"""The run's root publishes the engine's offers, and the mesh adapter hands them to the member."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from niuu.mesh.attestation import (
    KIND_SERVICE,
    KIND_SESSION,
    AttestationRefusedError,
    MeshAttestationVerifier,
    MeshSigner,
)
from niuu.mesh.events import MeshEvent, MeshEventType
from niuu.mesh.participant import MeshParticipant
from niuu.mesh.platform_events import (
    ATTESTATION_KEY,
    WorkflowOfferVerifier,
    offer_subject,
    sign_workflow_offer,
)
from skuld.config import MeshConfig
from skuld.mesh_adapter import SkuldMeshAdapter
from skuld.mesh_topics import thread_topic
from tests.test_niuu.test_mesh.test_attestation import Authority
from tests.test_skuld.workflow_support import (
    OFFER_ID,
    ROOT_ID,
    RUN_ID,
    SESSION_ID,
    TOPIC,
    offer_body,
    offer_event,
    root_broker,
    sender,
)

THREAD = thread_topic(RUN_ID)


async def _signer(authority: Authority, peer: str, kind: str = KIND_SERVICE) -> MeshSigner:
    signer = MeshSigner(
        authority.source(kind=kind, session_id="sess-1" if kind == KIND_SESSION else ""), peer
    )
    await signer.start()
    return signer


def _verifier(signer: MeshSigner, peer: str = "service:ting") -> WorkflowOfferVerifier:
    return WorkflowOfferVerifier(
        MeshAttestationVerifier(
            lambda: signer.jwks, issuer=signer.issuer, own_memberships=lambda: ()
        ),
        event_max_age_s=3600,
        signer_peer_id=peer,
    )


class TestOfferSignature:
    async def test_an_offer_signed_by_the_engine_verifies(self) -> None:
        ting = await _signer(Authority(), "service:ting")
        offer = offer_body()

        sender_ = _verifier(ting).verify(offer, sign_workflow_offer(ting, offer))

        assert sender_.peer_id == "service:ting"
        assert sender_.kind == KIND_SERVICE

    async def test_the_signature_is_over_the_payload_without_the_attestation_it_carries(
        self,
    ) -> None:
        ting = await _signer(Authority(), "service:ting")
        offer = offer_body()
        attestation = sign_workflow_offer(ting, offer)

        carried = {**offer, ATTESTATION_KEY: attestation}

        assert offer_subject(carried) == offer_subject(offer)
        _verifier(ting).verify(carried, attestation)

    @pytest.mark.parametrize(
        "change",
        [{"instructions": "other"}, {"topic": "review.requested"}, {"offer_id": "offer-9"}],
    )
    async def test_a_changed_offer_does_not_verify(self, change) -> None:
        ting = await _signer(Authority(), "service:ting")
        offer = offer_body()
        attestation = sign_workflow_offer(ting, offer)

        with pytest.raises(AttestationRefusedError, match="signature does not match"):
            _verifier(ting).verify({**offer, **change}, attestation)

    async def test_an_offer_without_a_signature_does_not_verify(self) -> None:
        ting = await _signer(Authority(), "service:ting")

        with pytest.raises(AttestationRefusedError, match="no attestation"):
            _verifier(ting).verify(offer_body(), None)

    async def test_another_service_or_a_session_is_not_the_engine(self) -> None:
        authority = Authority()
        mimir = await _signer(authority, "service:mimir")
        member = await _signer(authority, "a-session", kind=KIND_SESSION)
        offer = offer_body()

        with pytest.raises(AttestationRefusedError, match="not by 'service:ting'"):
            _verifier(mimir).verify(offer, sign_workflow_offer(mimir, offer))
        with pytest.raises(AttestationRefusedError, match="only a platform service"):
            _verifier(member).verify(offer, sign_workflow_offer(member, offer))

    async def test_an_offer_signature_is_not_a_platform_event_signature(self) -> None:
        # The subject is wrapped, so one signature cannot be read as the other.
        assert list(offer_subject(offer_body())) == ["workflow_offer"]


def _signing_root(tmp_path, ting: MeshSigner, **steps):
    broker = root_broker(
        tmp_path,
        mesh={"attestation": {"required": True}},
        **({"workflow_steps": steps} if steps else {}),
    )
    broker._mesh_attested = SimpleNamespace(
        verifier=MeshAttestationVerifier(
            lambda: ting.jwks, issuer=ting.issuer, own_memberships=lambda: ()
        )
    )
    return broker


class TestTheRootPublishesTheOffer:
    async def test_the_offer_is_published_verbatim_on_its_topic_and_the_runs_thread(
        self, tmp_path
    ) -> None:
        broker = root_broker(tmp_path)
        offer = offer_body()

        answer = await broker.publish_workflow_offer(offer, None)

        published = broker._mesh_adapter.published
        assert [topic for _, topic in published] == [TOPIC, THREAD]
        event = published[0][0]
        assert event.type == MeshEventType.WORKFLOW
        assert event.payload == offer
        assert event.correlation_id == RUN_ID
        assert event.source == ROOT_ID
        assert answer == {
            "offer_id": OFFER_ID,
            "event_id": event.event_id,
            "topics": [TOPIC, THREAD],
        }

    async def test_the_engines_signature_travels_with_it(self, tmp_path) -> None:
        ting = await _signer(Authority(), "service:ting")
        broker = _signing_root(tmp_path, ting)
        offer = offer_body()
        attestation = sign_workflow_offer(ting, offer)

        await broker.publish_workflow_offer(offer, attestation)

        event = broker._mesh_adapter.published[0][0]
        assert event.payload[ATTESTATION_KEY] == attestation
        assert {k: v for k, v in event.payload.items() if k != ATTESTATION_KEY} == offer

    async def test_it_is_reported_to_forge(self, tmp_path) -> None:
        broker = root_broker(tmp_path)

        await broker.publish_workflow_offer(offer_body(), None)

        [report] = [p["metadata"]["workflow_step"] for p in broker._posted]
        assert report == {
            "event": "offer_published",
            "run_id": RUN_ID,
            "offer_id": OFFER_ID,
            "node_id": "implement",
            "topic": TOPIC,
        }

    async def test_the_same_offer_again_is_the_same_event(self, tmp_path) -> None:
        broker = root_broker(tmp_path)

        await broker.publish_workflow_offer(offer_body(), None)
        await broker.publish_workflow_offer(offer_body(), None)

        ids = {event.event_id for event, _ in broker._mesh_adapter.published}
        assert len(ids) == 1
        assert len(broker._mesh_adapter.published) == 4

    async def test_an_offer_the_engine_did_not_sign_is_not_published(self, tmp_path) -> None:
        from skuld.workflow_steps import WorkflowOfferRefusedError

        ting = await _signer(Authority(), "service:ting")
        broker = _signing_root(tmp_path, ting)
        offer = offer_body()
        attestation = sign_workflow_offer(ting, offer)

        with pytest.raises(WorkflowOfferRefusedError, match="signature") as refused:
            await broker.publish_workflow_offer({**offer, "instructions": "x"}, attestation)
        with pytest.raises(WorkflowOfferRefusedError, match="no attestation") as unsigned:
            await broker.publish_workflow_offer(offer, None)

        assert refused.value.status == unsigned.value.status == 403
        assert broker._mesh_adapter.published == []
        assert broker._posted == []

    async def test_an_offer_for_another_run_is_refused(self, tmp_path) -> None:
        from skuld.workflow_steps import WorkflowOfferRefusedError

        broker = root_broker(tmp_path)

        with pytest.raises(WorkflowOfferRefusedError, match="this is the root of run") as refused:
            await broker.publish_workflow_offer(offer_body(run_id="another"), None)

        assert refused.value.status == 409

    @pytest.mark.parametrize(
        "offer",
        [{"kind": "step.passed", "run_id": RUN_ID}, {"kind": "step.offered", "run_id": RUN_ID}, {}],
    )
    async def test_something_that_is_not_an_offer_is_refused(self, tmp_path, offer) -> None:
        from skuld.workflow_steps import WorkflowOfferRefusedError

        broker = root_broker(tmp_path)

        with pytest.raises(WorkflowOfferRefusedError, match="not an offer") as refused:
            await broker.publish_workflow_offer(offer, None)

        assert refused.value.status == 400

    async def test_a_coordinators_run_takes_no_offer_from_the_engine(self, tmp_path) -> None:
        from skuld.workflow_steps import WorkflowOfferRefusedError

        broker = root_broker(tmp_path, workflow_steps={"orchestration": "session"})

        with pytest.raises(WorkflowOfferRefusedError, match="coordinator") as refused:
            await broker.publish_workflow_offer(offer_body(), None)

        assert refused.value.status == 409

    async def test_a_mesh_that_is_not_running_publishes_nothing(self, tmp_path) -> None:
        from skuld.workflow_steps import WorkflowOfferRefusedError

        broker = root_broker(tmp_path)
        broker._mesh_adapter.is_running = False

        with pytest.raises(WorkflowOfferRefusedError, match="mesh is not running") as refused:
            await broker.publish_workflow_offer(offer_body(), None)

        assert refused.value.status == 503

    async def test_a_transport_that_refuses_the_event_is_a_503_and_no_report(
        self, tmp_path
    ) -> None:
        from skuld.workflow_steps import WorkflowOfferRefusedError

        broker = root_broker(tmp_path)
        broker._mesh_adapter.fail = RuntimeError("nng closed")

        with pytest.raises(WorkflowOfferRefusedError, match="nng closed") as refused:
            await broker.publish_workflow_offer(offer_body(), None)

        assert refused.value.status == 503
        assert broker._posted == []

    async def test_a_session_that_is_not_a_root_refuses(self, tmp_path) -> None:
        from skuld.broker import Broker
        from skuld.config import SkuldSettings
        from skuld.workflow_steps import WorkflowStepNotHostedError

        broker = Broker(SkuldSettings(session={"id": SESSION_ID, "workspace_dir": str(tmp_path)}))

        with pytest.raises(WorkflowStepNotHostedError, match="not a workflow run's root"):
            await broker.publish_workflow_offer(offer_body(), None)


class TestTheRoomOfferRoute:
    @pytest.fixture
    def api(self):
        return importlib.import_module("skuld.broker_api")

    async def test_the_route_publishes_and_answers(self, tmp_path, api, monkeypatch) -> None:
        broker = root_broker(tmp_path)
        monkeypatch.setattr(api, "broker", broker)

        answer = await api.post_room_offer(api._RoomOfferRequest(offer=offer_body()))

        assert answer["offer_id"] == OFFER_ID
        assert len(broker._mesh_adapter.published) == 2

    async def test_the_route_maps_each_refusal_to_its_status(
        self, tmp_path, api, monkeypatch
    ) -> None:
        broker = root_broker(tmp_path)
        monkeypatch.setattr(api, "broker", broker)

        with pytest.raises(HTTPException) as other_run:
            await api.post_room_offer(api._RoomOfferRequest(offer=offer_body(run_id="x")))
        with pytest.raises(HTTPException) as malformed:
            await api.post_room_offer(api._RoomOfferRequest(offer={"kind": "nope"}))

        assert (other_run.value.status_code, malformed.value.status_code) == (409, 400)

    async def test_the_route_is_404_on_a_session_that_is_not_a_root(
        self, tmp_path, api, monkeypatch
    ) -> None:
        from tests.test_skuld.workflow_support import step_broker

        monkeypatch.setattr(api, "broker", step_broker(tmp_path))

        with pytest.raises(HTTPException) as refused:
            await api.post_room_offer(api._RoomOfferRequest(offer=offer_body()))

        assert refused.value.status_code == 404

    def test_the_route_is_owner_only_and_the_old_step_routes_are_gone(self, api) -> None:
        from niuu.room_access import required_role_for_route

        assert required_role_for_route("POST", "room/offer") == "owner"
        paths = set(api.app.openapi()["paths"])
        assert "/api/room/offer" in paths
        assert not [path for path in paths if path.startswith("/api/workflow/steps")]


class _Host:
    """The workflow host the mesh adapter hands events to."""

    def __init__(self) -> None:
        self.events: list[tuple[MeshEvent, object]] = []
        self.ready = 0

    async def accept_workflow_offer(self, event, *, verified):
        self.events.append((event, verified))
        return "picked_up"

    async def workflow_mesh_ready(self) -> None:
        self.ready += 1


def _adapter(host: _Host | None, **kwargs) -> tuple[SkuldMeshAdapter, MagicMock]:
    mesh = MagicMock()
    for name in ("start", "stop", "publish", "subscribe", "unsubscribe"):
        setattr(mesh, name, AsyncMock())
    mesh.set_rpc_handler = MagicMock()
    config = MeshConfig(
        enabled=True,
        peer_id=SESSION_ID,
        transport="in_process",
        consumes_event_types=[TOPIC],
        attestation={"required": True},
    )
    participant = MeshParticipant(mesh=mesh, discovery=None, peer_id=SESSION_ID)
    adapter = SkuldMeshAdapter(
        participant=participant,
        peer_inbox=host if host is not None else SimpleNamespace(),
        config=config,
        session_id=SESSION_ID,
        **kwargs,
    )
    return adapter, mesh


class TestTheMeshAdapterCarriesWorkflowEvents:
    async def test_an_offer_goes_to_the_workflow_host_with_the_verified_publisher(self) -> None:
        host = _Host()
        adapter, _ = _adapter(host)
        event = offer_event(verified=ROOT_ID)

        await adapter._handle_outcome_event(event)

        [(received, verified)] = host.events
        assert received is event
        assert verified == sender()

    async def test_a_publisher_nobody_verified_reaches_the_host_as_unverified(self) -> None:
        host = _Host()
        adapter, _ = _adapter(host)

        await adapter._handle_outcome_event(offer_event())

        assert host.events[0][1] is None

    async def test_this_peers_own_workflow_events_are_not_handed_back(self) -> None:
        host = _Host()
        adapter, _ = _adapter(host)

        await adapter._handle_outcome_event(offer_event(source=SESSION_ID))

        assert host.events == []

    async def test_a_session_with_no_workflow_host_ignores_them(self) -> None:
        adapter, _ = _adapter(None)

        await adapter._handle_outcome_event(offer_event())

    async def test_the_host_is_told_when_the_subscriptions_are_up(self) -> None:
        host = _Host()
        adapter, mesh = _adapter(host)

        await adapter.start()

        assert host.ready == 1
        subscribed = [call.args[0] for call in mesh.subscribe.await_args_list]
        assert TOPIC in subscribed

    async def test_an_explicit_host_is_used_over_the_inbox(self) -> None:
        host = _Host()
        adapter, _ = _adapter(None, workflow=host)

        await adapter._handle_outcome_event(offer_event())

        assert len(host.events) == 1
