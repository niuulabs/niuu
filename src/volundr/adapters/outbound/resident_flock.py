"""Attach resident-native sessions to persona event subscriptions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niuu.domain.outcome import OutcomeSchema, parse_outcome_block
from niuu.mesh.identity import MeshIdentity
from ravn.adapters.discovery.event_bus import EventBusDiscoveryAdapter
from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from ravn.domain.events import RavnEvent, RavnEventType
from volundr.domain.models import ResidentObservedState
from volundr.domain.ports import (
    ResidentChatConnection,
    ResidentRuntimeRepository,
    ResidentSessionController,
    SessionPersona,
    SessionPersonaProvider,
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


@dataclass
class _ResidentPeer:
    mesh: SleipnirMeshAdapter
    discovery: EventBusDiscoveryAdapter
    persona: SessionPersona | None


class ResidentFlockAdapter:
    """Attach active resident session adapters to the configured Sleipnir bus."""

    def __init__(
        self,
        repository: ResidentRuntimeRepository,
        session_controllers: list[ResidentSessionController],
        bus: Any,
        *,
        persona_provider: SessionPersonaProvider | None = None,
    ) -> None:
        self._repository = repository
        self._controllers = {controller.engine: controller for controller in session_controllers}
        self._bus = bus
        self._persona_provider = persona_provider
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
            peer = self._peers.pop(runtime_id)
            await peer.mesh.stop()
            await peer.discovery.stop()
        for runtime_id, runtime in desired.items():
            persona = await self._resolve_persona(runtime.owner_id, runtime.persona_name)
            current = self._peers.get(runtime_id)
            if current is not None and current.persona == persona:
                continue
            if current is not None:
                self._peers.pop(runtime_id, None)
                await current.mesh.stop()
                await current.discovery.stop()
            peer = SleipnirMeshAdapter(
                publisher=self._bus,
                subscriber=self._bus,
                own_peer_id=runtime.flock_peer_id,
                environment_id=str(runtime.flock_id),
                manage_transport_lifecycle=False,
            )
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
            resident_peer = _ResidentPeer(mesh=peer, discovery=discovery, persona=persona)
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
                await asyncio.gather(peer.stop(), discovery.stop(), return_exceptions=True)
                raise
            logger.info(
                "Resident flock peer started runtime=%s peer=%s flock=%s",
                runtime.id,
                runtime.flock_peer_id,
                runtime.flock_id,
            )

    async def _resolve_persona(self, owner_id: str, name: str) -> SessionPersona | None:
        if self._persona_provider is None or not name.strip():
            return None
        persona = await self._persona_provider.get(owner_id, name)
        if persona is None:
            logger.warning("Resident flock persona not found owner=%s name=%s", owner_id, name)
        return persona

    async def _handle_persona_event(self, runtime_id: UUID, event: RavnEvent) -> None:
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
        for peer in list(self._peers.values()):
            await peer.mesh.stop()
            await peer.discovery.stop()
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

    async def _publish_reaction_error(
        self,
        runtime: Any,
        reaction: _ResidentReaction,
    ) -> None:
        peer = self._peers.get(reaction.runtime_id)
        if peer is None:
            return
        error = RavnEvent(
            type=RavnEventType.ERROR,
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
            response = RavnEvent(
                type=RavnEventType.RESPONSE,
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
        outcome = RavnEvent(
            type=RavnEventType.OUTCOME,
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
