"""Attach resident-native sessions to persona event subscriptions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from niuu.adapters.discovery.event_bus import EventBusDiscoveryAdapter
from niuu.adapters.mesh.attested import AttestedMesh
from niuu.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from niuu.domain.outcome import OutcomeSchema, parse_outcome_block
from niuu.domain.services.mesh_certificate import MeshCertificateIssuer
from niuu.mesh.attestation import KIND_RESIDENT, MeshAttestationVerifier, MeshSigner
from niuu.mesh.config import MeshAttestationConfig
from niuu.mesh.events import MeshEvent, MeshEventType
from niuu.mesh.identity import MeshIdentity
from volundr.domain.mesh_peer_claims import MeshPeerClaims, resident_holder
from volundr.domain.models import ResidentObservedState, ResidentRuntime
from volundr.domain.ports import (
    ResidentChatConnection,
    ResidentRuntimeRepository,
    ResidentSessionController,
    SessionPersona,
    SessionPersonaProvider,
)
from volundr.domain.services.mesh_identity import (
    FLOCK_PREFIX,
    REALM_PREFIX,
    TENANT_PREFIX,
    ForgeMeshCertificateSource,
)

logger = logging.getLogger(__name__)


@dataclass
class _ResidentReaction:
    reaction_id: str
    runtime_id: UUID
    title: str
    prompt: str
    session_id: str
    root_correlation_id: str
    status: str = "queued"
    output: str = ""
    error: str = ""
    connection: ResidentChatConnection | None = None
    runner: asyncio.Task[None] | None = None


@dataclass(frozen=True)
class ResidentMeshCertificates:
    """What Forge needs to certify its resident peers in process: the issuer and the claims."""

    issuer: MeshCertificateIssuer
    claims: MeshPeerClaims


@dataclass
class _ResidentPeer:
    mesh: SleipnirMeshAdapter | AttestedMesh
    discovery: EventBusDiscoveryAdapter
    persona: SessionPersona | None
    #: Set when the peer signs its mesh messages (``mesh_certificates`` configured).
    signer: MeshSigner | None = None
    #: One id per activation of the peer; the claim on its peer id carries it.
    launch_id: str = ""


def resident_memberships(runtime: ResidentRuntime) -> tuple[str, ...]:
    """What Forge certifies a resident peer belongs to: its flock, realm and tenant."""
    memberships = [f"{FLOCK_PREFIX}{runtime.flock_id}"]
    if runtime.realm_id is not None:
        memberships.append(f"{REALM_PREFIX}{runtime.realm_id}")
    memberships.append(f"{TENANT_PREFIX}{runtime.tenant_id}")
    return tuple(memberships)


class ResidentFlockAdapter:
    """Attach active resident session adapters to the configured Sleipnir bus."""

    def __init__(
        self,
        repository: ResidentRuntimeRepository,
        session_controllers: list[ResidentSessionController],
        bus: Any,
        *,
        persona_provider: SessionPersonaProvider | None = None,
        mesh_certificates: ResidentMeshCertificates | None = None,
        attestation: MeshAttestationConfig | None = None,
    ) -> None:
        self._repository = repository
        self._controllers = {controller.engine: controller for controller in session_controllers}
        self._bus = bus
        self._persona_provider = persona_provider
        self._mesh_certificates = mesh_certificates
        self._attestation = attestation or MeshAttestationConfig()
        self._peers: dict[UUID, _ResidentPeer] = {}
        self._reactions: dict[str, _ResidentReaction] = {}

    async def sync(self) -> None:
        """Converge persona event subscribers with active flock residents."""
        runtimes = await self._repository.list_for_reconciliation()
        desired = {
            runtime.id: runtime
            for runtime in runtimes
            if runtime.observed_state is ResidentObservedState.ACTIVE
            and runtime.flock_id is not None
            and runtime.flock_peer_id
            and runtime.engine in self._controllers
        }
        for runtime_id in set(self._peers) - set(desired):
            await self._stop_peer(runtime_id, self._peers.pop(runtime_id))
        for runtime_id, runtime in desired.items():
            persona = await self._resolve_persona(runtime.owner_id, runtime.persona_name)
            current = self._peers.get(runtime_id)
            if current is not None and current.persona == persona:
                continue
            if current is not None:
                self._peers.pop(runtime_id, None)
                await self._stop_peer(runtime_id, current)
            inner = SleipnirMeshAdapter(
                publisher=self._bus,
                subscriber=self._bus,
                own_peer_id=runtime.flock_peer_id,
                environment_id=str(runtime.flock_id),
                manage_transport_lifecycle=False,
            )
            peer, signer, launch_id = await self._attest(runtime, inner)
            discovery = EventBusDiscoveryAdapter(
                MeshIdentity(
                    peer_id=runtime.flock_peer_id,
                    realm_id=str(runtime.flock_id),
                    persona=runtime.persona_name or runtime.name,
                    capabilities=[capability.value for capability in runtime.capabilities],
                    permission_mode="permissive",
                    version="volundr",
                    consumes_event_types=(
                        list(persona.consumes_event_types) if persona is not None else []
                    ),
                    emits_event_types=(
                        [persona.produces_event_type]
                        if persona is not None and persona.produces_event_type
                        else []
                    ),
                ),
                self._bus,
                self._bus,
                manage_transport_lifecycle=False,
            )
            resident_peer = _ResidentPeer(
                mesh=peer,
                discovery=discovery,
                persona=persona,
                signer=signer,
                launch_id=launch_id,
            )
            self._peers[runtime_id] = resident_peer
            try:
                await discovery.start()
                await peer.start()
                if persona is not None:
                    for event_type in persona.consumes_event_types:
                        await peer.subscribe(
                            event_type,
                            lambda event, resident_id=runtime_id: self._handle_persona_event(
                                resident_id, event
                            ),
                        )
            except Exception:
                self._peers.pop(runtime_id, None)
                await asyncio.gather(
                    self._stop_peer(runtime_id, resident_peer), return_exceptions=True
                )
                raise
            logger.info(
                "Resident flock peer started runtime=%s peer=%s flock=%s",
                runtime.id,
                runtime.flock_peer_id,
                runtime.flock_id,
            )

    async def _attest(
        self, runtime: ResidentRuntime, inner: SleipnirMeshAdapter
    ) -> tuple[SleipnirMeshAdapter | AttestedMesh, MeshSigner | None, str]:
        """Wrap the peer's mesh so it signs what it sends and verifies what it receives.

        Without ``mesh_certificates`` (Forge runs no mesh identity) the mesh is unwrapped.
        With them the resident is certified in process (kind ``resident``: its flock,
        realm and tenant), its peer id is claimed like any session's, and a certificate
        Forge cannot issue (for instance a session holding the peer id) fails the sync.
        """
        if self._mesh_certificates is None:
            return inner, None, ""
        launch_id = uuid4().hex
        source = ForgeMeshCertificateSource(
            self._mesh_certificates.issuer,
            self._mesh_certificates.claims,
            holder=resident_holder(runtime.id),
            kind=KIND_RESIDENT,
            launch_id=launch_id,
            memberships=resident_memberships(runtime),
            tenant_id=runtime.tenant_id,
            mesh_realm=str(runtime.flock_id),
        )
        signer = MeshSigner(
            source,
            runtime.flock_peer_id,
            renew_fraction=self._attestation.renew_fraction,
            retry_fraction=self._attestation.retry_fraction,
        )
        await signer.start()
        verifier = MeshAttestationVerifier(
            lambda: signer.jwks,
            issuer=signer.issuer,
            own_memberships=signer.memberships,
            clock_skew_s=self._attestation.clock_skew_s,
            event_max_age_s=self._attestation.event_max_age_s,
        )
        mesh = AttestedMesh(
            inner,
            signer,
            verifier,
            own_peer_id=runtime.flock_peer_id,
            event_max_age_s=self._attestation.event_max_age_s,
        )
        return mesh, signer, launch_id

    async def _stop_peer(self, runtime_id: UUID, peer: _ResidentPeer) -> None:
        """Stop the peer's mesh, discovery and signer, and free its peer id."""
        await peer.mesh.stop()
        await peer.discovery.stop()
        if peer.signer is None:
            return
        await peer.signer.stop()
        await self._mesh_certificates.claims.release(
            holder=resident_holder(runtime_id), launch_id=peer.launch_id
        )

    async def _resolve_persona(self, owner_id: str, name: str) -> SessionPersona | None:
        if self._persona_provider is None or not name.strip():
            return None
        persona = await self._persona_provider.get(owner_id, name)
        if persona is None:
            logger.warning("Resident flock persona not found owner=%s name=%s", owner_id, name)
        return persona

    async def _handle_persona_event(self, runtime_id: UUID, event: MeshEvent) -> None:
        runtime = await self._repository.get(runtime_id)
        if runtime is None or event.source == runtime.flock_peer_id:
            return
        event_type = str(event.payload.get("event_type") or "event")
        identity = ":".join(
            (
                str(runtime_id),
                event_type,
                event.task_id or "",
                event.correlation_id,
                event.root_correlation_id,
            )
        )
        reaction_id = f"event_{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
        if reaction_id in self._reactions:
            return

        message = str(
            event.payload.get("prompt") or event.payload.get("task_description") or ""
        ).strip()
        payload = json.dumps(event.payload, sort_keys=True, default=str)
        prompt_parts = [
            f"Event type: {event_type}",
            f"Source: {event.source}",
        ]
        if message:
            prompt_parts.extend(("Message:", message))
        prompt_parts.extend(("Event payload:", payload))
        reaction = _ResidentReaction(
            reaction_id=reaction_id,
            runtime_id=runtime_id,
            title=f"React to {event_type} ({reaction_id[-8:]})",
            prompt="\n".join(prompt_parts),
            session_id=event.session_id,
            root_correlation_id=event.root_correlation_id or event.correlation_id,
        )
        self._reactions[reaction_id] = reaction
        reaction.runner = asyncio.create_task(
            self._run_reaction(reaction),
            name=f"resident-event-{reaction_id}",
        )

    async def stop(self) -> None:
        """Stop event subscribers and active resident reactions."""
        for runtime_id, peer in list(self._peers.items()):
            await self._stop_peer(runtime_id, peer)
        self._peers.clear()
        for reaction in self._reactions.values():
            if reaction.runner and not reaction.runner.done():
                reaction.runner.cancel()
        await asyncio.gather(
            *(reaction.runner for reaction in self._reactions.values() if reaction.runner),
            return_exceptions=True,
        )

    async def _run_reaction(self, reaction: _ResidentReaction) -> None:
        connection: ResidentChatConnection | None = None
        runtime: Any = None
        try:
            runtime = await self._repository.get(reaction.runtime_id)
            if runtime is None:
                raise RuntimeError(f"Resident runtime {reaction.runtime_id} no longer exists")
            controller = self._controllers[runtime.engine]
            prompt = reaction.prompt
            peer = self._peers.get(reaction.runtime_id)
            persona = peer.persona if peer is not None else None
            if persona is not None and persona.system_prompt:
                prompt = f"{persona.system_prompt}\n\nSubscribed event:\n{prompt}"
            await self._publish_reaction_started(runtime, reaction)
            session = await controller.create_session(
                runtime,
                title=reaction.title,
                model=runtime.model,
            )
            connection = await controller.connect_chat(runtime, session.id)
            reaction.connection = connection
            reaction.status = "running"
            await connection.send(
                {"type": "user", "content": prompt, "request_id": reaction.reaction_id}
            )
            while reaction.status == "running":
                frame = await connection.receive()
                frame_type = str(frame.get("type") or "")
                if frame_type == "content_block_delta":
                    delta = frame.get("delta")
                    if isinstance(delta, dict):
                        reaction.output += str(delta.get("text") or "")
                    continue
                if frame_type == "control_request":
                    await connection.send(
                        {
                            "type": "permission_response",
                            "request_id": str(frame.get("request_id") or ""),
                            "behavior": "allowOnce",
                        }
                    )
                    continue
                if frame_type in {"tool_start", "tool_result"}:
                    await self._publish_reaction_tool_frame(runtime, reaction, frame)
                    continue
                if frame_type == "result":
                    result = str(frame.get("result") or "")
                    if result and not reaction.output:
                        reaction.output = result
                    if not reaction.output.strip():
                        reaction.error = "Resident reaction completed without an assistant response"
                        reaction.status = "failed"
                        await self._publish_reaction_error(runtime, reaction)
                        return
                    reaction.status = "complete"
                    await self._publish_reaction_output(runtime, reaction, persona)
                    return
                if frame_type == "error":
                    reaction.error = str(frame.get("error") or "Resident reaction failed")
                    reaction.status = "failed"
                    await self._publish_reaction_error(runtime, reaction)
                    return
        except asyncio.CancelledError:
            reaction.status = "cancelled"
            raise
        except Exception as exc:
            reaction.error = str(exc)
            reaction.status = "failed"
            logger.exception(
                "Resident event reaction failed runtime=%s reaction=%s",
                reaction.runtime_id,
                reaction.reaction_id,
            )
            if runtime is not None:
                await self._publish_reaction_error(runtime, reaction)
        finally:
            reaction.connection = None
            if connection is not None:
                await connection.close()

    async def _publish_reaction_started(
        self,
        runtime: Any,
        reaction: _ResidentReaction,
    ) -> None:
        peer = self._peers.get(reaction.runtime_id)
        if peer is None:
            return
        event = MeshEvent(
            type=MeshEventType.TASK_STARTED,
            source=runtime.flock_peer_id,
            payload={
                "task_id": reaction.reaction_id,
                "title": reaction.title,
                "persona": runtime.persona_name or runtime.name,
            },
            timestamp=datetime.now(UTC),
            urgency=0.2,
            correlation_id=reaction.session_id or reaction.reaction_id,
            session_id=reaction.session_id,
            task_id=reaction.reaction_id,
            root_correlation_id=reaction.root_correlation_id,
        )
        await peer.mesh.publish(event, topic=f"activity.{runtime.flock_peer_id}")

    async def _publish_reaction_tool_frame(
        self,
        runtime: Any,
        reaction: _ResidentReaction,
        frame: dict[str, Any],
    ) -> None:
        peer = self._peers.get(reaction.runtime_id)
        if peer is None:
            return
        frame_type = str(frame.get("type") or "")
        metadata = frame.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        payload: dict[str, Any] = {
            "tool_name": str(metadata.get("tool_name") or frame.get("data") or "tool"),
            "persona": runtime.persona_name or runtime.name,
        }
        if frame_type == "tool_start":
            payload["input"] = metadata.get("input") or {}
            event_type = MeshEventType.TOOL_START
        else:
            payload["result"] = str(frame.get("data") or "")
            payload["is_error"] = bool(metadata.get("is_error"))
            event_type = MeshEventType.TOOL_RESULT
        event = MeshEvent(
            type=event_type,
            source=runtime.flock_peer_id,
            payload=payload,
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id=reaction.session_id or reaction.reaction_id,
            session_id=reaction.session_id,
            task_id=reaction.reaction_id,
            root_correlation_id=reaction.root_correlation_id,
        )
        await peer.mesh.publish(event, topic=f"activity.{runtime.flock_peer_id}")

    async def _publish_reaction_error(
        self,
        runtime: Any,
        reaction: _ResidentReaction,
    ) -> None:
        peer = self._peers.get(reaction.runtime_id)
        if peer is None:
            return
        error = MeshEvent(
            type=MeshEventType.ERROR,
            source=runtime.flock_peer_id,
            payload={
                "message": reaction.error,
                "persona": runtime.persona_name or runtime.name,
            },
            timestamp=datetime.now(UTC),
            urgency=0.6,
            correlation_id=reaction.session_id or reaction.reaction_id,
            session_id=reaction.session_id,
            task_id=reaction.reaction_id,
            root_correlation_id=reaction.root_correlation_id,
        )
        await peer.mesh.publish(error, topic=f"activity.{runtime.flock_peer_id}")

    async def _publish_reaction_output(
        self,
        runtime: Any,
        reaction: _ResidentReaction,
        persona: SessionPersona | None,
    ) -> None:
        peer = self._peers.get(reaction.runtime_id)
        if peer is None:
            return
        if reaction.output:
            response = MeshEvent(
                type=MeshEventType.RESPONSE,
                source=runtime.flock_peer_id,
                payload={"text": reaction.output, "persona": runtime.persona_name or runtime.name},
                timestamp=datetime.now(UTC),
                urgency=0.2,
                correlation_id=reaction.session_id or reaction.reaction_id,
                session_id=reaction.session_id,
                task_id=reaction.reaction_id,
                root_correlation_id=reaction.root_correlation_id,
            )
            await peer.mesh.publish(response, topic=f"activity.{runtime.flock_peer_id}")

        if persona is None or not persona.produces_event_type:
            return
        schema = OutcomeSchema(persona.produces_schema) if persona.produces_schema else None
        parsed = parse_outcome_block(reaction.output, schema)
        fields = dict(parsed.fields) if parsed is not None else {}
        outcome_payload: dict[str, Any] = {
            "persona": persona.name,
            "success": True,
            "event_type": persona.produces_event_type,
            "outcome": fields,
            "fields": fields,
            "valid": bool(parsed.valid) if parsed is not None else not persona.produces_schema,
            "task_id": reaction.reaction_id,
            "bubble_up": True,
        }
        summary = fields.get("summary")
        if isinstance(summary, str) and summary.strip():
            outcome_payload["summary"] = summary.strip()
        outcome = MeshEvent(
            type=MeshEventType.OUTCOME,
            source=runtime.flock_peer_id,
            payload=outcome_payload,
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id=reaction.reaction_id,
            session_id=reaction.session_id,
            task_id=reaction.reaction_id,
            root_correlation_id=reaction.root_correlation_id,
        )
        await peer.mesh.publish(outcome, topic=persona.produces_event_type)
