"""What the Skuld workflow tests share: a member's persona, its offers, and a broker to hold them.

An offer is built the way Ting builds it: the contract of the pinned persona, written from the
same persona document the member runs as, so a test that changes one side changes what the
member refuses.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from niuu.domain.workflow_step import (
    PersonaPin,
    StepContract,
    StepOffered,
    persona_contract_of,
)
from niuu.mesh.attestation import VERIFIED_SENDER_KEY
from niuu.mesh.events import MeshEvent, MeshEventType
from niuu.mesh.platform_events import ATTESTATION_KEY
from niuu.ports.cli.transport import TransportCapabilities
from personas.domain.persona_document import PortablePersonaDefinition
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.mesh_topics import mesh_topics
from skuld.peer_inbox import SenderIdentity

SESSION_ID = "6f1f0b52-7f4e-4a53-9b0b-3f4a2f2b9a11"
ROOT_ID = "a1b2c3d4-0000-4000-8000-00000000r007"
RUN_ID = "0b7e6d5c-4a39-4281-9f10-e1d2c3b4a596"
OFFER_ID = "offer-0001"
OTHER_OFFER_ID = "offer-0002"
TOPIC = "code.requested"
ALIAS = "coder"

WAITING_ON = {"kind": "person", "ref": "ada", "reason": "needs a product decision"}

PERSONA = PortablePersonaDefinition(
    id="coder",
    revision="r1",
    definition={
        "name": "coder",
        "system_prompt_template": "You write code.",
        "consumes": {"event_types": [TOPIC]},
        "produces": {
            "event_type_map": {
                "implemented": "code.implemented",
                "blocked": "code.blocked",
            },
            "schema": {
                "summary": {"type": "string", "description": "what changed"},
                "files_changed": {
                    "type": "number",
                    "description": "files touched",
                    "required": False,
                },
            },
        },
    },
).to_dict()

#: Another revision of the same persona, which a stale offer would pin.
OTHER_REVISION = PortablePersonaDefinition(
    id="coder", revision="r2", definition=PERSONA["definition"]
).to_dict()


def pin_of(document: dict[str, Any] = PERSONA, alias: str = ALIAS) -> PersonaPin:
    persona = PortablePersonaDefinition.from_dict(document)
    return PersonaPin(alias=alias, id=persona.id, revision=persona.revision, digest=persona.digest)


def contract_of(document: dict[str, Any] = PERSONA, alias: str = ALIAS) -> StepContract:
    return StepContract.from_persona(pin_of(document, alias), persona_contract_of(document))


def offer_body(**changes: Any) -> dict[str, Any]:
    """A ``step.offered`` payload as the engine writes it, with one change applied."""
    offer = StepOffered(
        run_id=changes.pop("run_id", RUN_ID),
        offer_id=changes.pop("offer_id", OFFER_ID),
        node_id=changes.pop("node_id", "implement"),
        node_label=changes.pop("node_label", "Implement"),
        topic=changes.pop("topic", TOPIC),
        deadline_at=changes.pop("deadline_at", datetime(2026, 11, 8, 12, tzinfo=UTC)),
        instructions=changes.pop(
            "instructions", "# Workflow step: Implement\nYou are the coder. Do the task."
        ),
        inputs=changes.pop(
            "inputs",
            [
                {
                    "seq": 1,
                    "event_type": "ticket.created",
                    "node_id": "start",
                    "payload": {"fields": {"area": "backend"}},
                }
            ],
        ),
        contracts=changes.pop("contracts", [contract_of()]),
        **changes,
    )
    return offer.to_payload()


def offer_event(
    payload: dict[str, Any] | None = None,
    *,
    source: str = ROOT_ID,
    attestation: dict[str, Any] | None = None,
    verified: str | None = None,
) -> MeshEvent:
    """The offer as a member's mesh delivers it.

    ``attestation`` is the engine's signature, carried in the payload; ``verified`` is the peer
    the mesh layer says it verified as the publisher (what a signing mesh puts on the event).
    """
    payload = payload if payload is not None else offer_body()
    carried = {**payload, **({ATTESTATION_KEY: attestation} if attestation else {})}
    # Built by hand, not through StepOffered, so a test can deliver a malformed offer.
    event = MeshEvent(
        type=MeshEventType.WORKFLOW,
        source=source,
        payload=carried,
        timestamp=datetime.now(UTC),
        urgency=0.3,
        correlation_id=str(payload.get("run_id", "")),
        session_id=ROOT_ID,
        event_id=str(payload.get("offer_id", "")),
    )
    if verified is not None:
        event = replace(
            event, attestation={VERIFIED_SENDER_KEY: {"peer_id": verified, "session_id": verified}}
        )
    return event


def sender(peer: str = ROOT_ID) -> SenderIdentity:
    return SenderIdentity(peer_id=peer, session_id=peer)


class FakeMeshAdapter:
    """The mesh adapter as the workflow host uses it: it records what is published."""

    def __init__(self, peer_id: str = SESSION_ID) -> None:
        self.peer_id = peer_id
        self.is_running = True
        self.published: list[tuple[MeshEvent, str]] = []
        self.fail: Exception | None = None

    async def publish_event(self, event: MeshEvent, topic: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.published.append((event, topic))

    def kinds(self, topic: str | None = None) -> list[str]:
        return [
            event.payload["kind"]
            for event, published_on in self.published
            if topic is None or published_on == topic
        ]


#: The workflow_steps and mesh settings of a member, for tests of other parts of the broker
#: that need a step host (the member's persona goes in ``session.persona``).
MEMBER_STEPS = {
    "enabled": True,
    "run_id": RUN_ID,
    "member": "coder",
    "persona_alias": ALIAS,
    "orchestrator_peer_id": ROOT_ID,
}
MEMBER_MESH = {"enabled": True, "attestation": {"required": False}}


def member_settings(**overrides: Any) -> dict[str, Any]:
    """The settings Forge gives a workflow member's Skuld (an unauthenticated local mesh)."""
    settings: dict[str, Any] = {
        "volundr_api_url": "http://forge.test",
        "transport": "subprocess",
        "chronicle_watcher_enabled": False,
        "mesh": {"enabled": True, "attestation": {"required": False}},
        "workflow_steps": {
            "enabled": True,
            "run_id": RUN_ID,
            "member": "coder",
            "persona_alias": ALIAS,
            "orchestrator_peer_id": ROOT_ID,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(settings.get(key), dict):
            settings[key] = {**settings[key], **value}
            continue
        settings[key] = value
    return settings


def step_broker(tmp_path, *, activity: str = "idle", **overrides: Any) -> Broker:
    """A workflow member's broker with a fake transport, mesh adapter and Forge."""
    settings = member_settings(**overrides)
    settings["session"] = {
        "id": SESSION_ID,
        "workspace_dir": str(tmp_path),
        "persona": PERSONA,
        **settings.get("session", {}),
    }
    broker = Broker(SkuldSettings(**settings))
    transport = MagicMock()
    transport.is_alive = True
    transport.capabilities = TransportCapabilities()
    transport.send_message = AsyncMock(return_value=True)
    broker._transport = transport
    broker._activity_state = activity
    broker._complete_trace_span = AsyncMock()
    broker._start_trace_span = AsyncMock(return_value=None)
    broker._await_event_log_receipt = AsyncMock(return_value=True)
    broker._apply_retrieval_reflex = AsyncMock(side_effect=lambda message: message)
    posted: list[dict] = []

    async def post(url, json=None, **_kwargs):
        posted.append(json)
        return SimpleNamespace(status_code=204, url=url)

    broker._http_client = SimpleNamespace(post=post)
    broker._http_client_jwt = None
    broker._posted = posted
    broker._mesh_topics = mesh_topics(broker._settings)
    broker._mesh_adapter = FakeMeshAdapter()
    return broker


def root_broker(tmp_path, **overrides: Any) -> Broker:
    """A workflow run's root: no agent, a fake mesh adapter, an unauthenticated local mesh."""
    settings = member_settings(**overrides)
    settings["workflow_steps"] = {
        "root": True,
        "run_id": RUN_ID,
        **overrides.get("workflow_steps", {}),
    }
    settings["session"] = {"id": ROOT_ID, "workspace_dir": str(tmp_path)}
    broker = Broker(SkuldSettings(**settings))
    posted: list[dict] = []

    async def post(url, json=None, **_kwargs):
        posted.append(json)
        return SimpleNamespace(status_code=204, url=url)

    broker._http_client = SimpleNamespace(post=post)
    broker._http_client_jwt = None
    broker._posted = posted
    broker._mesh_adapter = FakeMeshAdapter(ROOT_ID)
    return broker
