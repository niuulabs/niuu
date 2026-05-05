"""SkuldMeshAdapter — mesh peer bridge for Claude CLI sessions (NIU-612).

Gives Skuld a mesh identity so it can participate in a ravn flock as a peer.
Subscribes to task topics and feeds received prompts to the CLI transport.
Results (including outcome blocks) are published back as response events.

NIU-634: wired to use niuu.mesh.MeshParticipant for lifecycle management.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

from niuu.adapters.cli import CliTurnRunner
from niuu.domain.outcome import parse_outcome_block
from niuu.mesh.participant import MeshParticipant
from niuu.ports.cli import CLITransport
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.ports.mesh import MeshPort
from skuld.config import MeshConfig
from sleipnir.ports.events import SleipnirSubscriber

logger = logging.getLogger("skuld.mesh_adapter")


class SkuldMeshAdapter:
    """Bridge between the ravn mesh and the CLI transport.

    Lifecycle:
        start() — start participant (mesh + discovery), subscribe to task topics
        stop()  — unsubscribe, stop participant (mesh + discovery)
    """

    def __init__(
        self,
        participant: MeshParticipant,
        transport: CLITransport,
        config: MeshConfig,
        session_id: str,
    ) -> None:
        self._participant = participant
        self._mesh: MeshPort | None = participant.mesh
        self._transport = transport
        self._config = config
        self._session_id = session_id
        self._peer_id = participant.peer_id or config.peer_id or socket.gethostname()
        self._running = False
        self._turn_runner = CliTurnRunner(transport)
        self._pending_responses = self._turn_runner.pending_responses
        self._execute_lock = self._turn_runner.execute_lock

    @property
    def peer_id(self) -> str:
        return self._peer_id

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def sleipnir_subscriber(self) -> SleipnirSubscriber | None:
        """Expose the Sleipnir subscriber from the underlying mesh transport.

        Returns None when the mesh is not a SleipnirMeshAdapter (e.g. in-process
        test mesh that has no dedicated subscriber port).
        """
        return getattr(self._mesh, "subscriber", None)

    async def start(self) -> None:
        """Start participant (mesh + discovery) and subscribe to task topics."""
        if self._running:
            return

        await self._participant.start()
        logger.info("mesh adapter: participant started (peer_id=%s)", self._peer_id)

        # Set up RPC handler for work_request messages
        if self._mesh is not None and hasattr(self._mesh, "set_rpc_handler"):
            self._mesh.set_rpc_handler(self._handle_rpc)
            logger.info("mesh adapter: RPC handler registered")

        # Subscribe to consumed event types
        if self._mesh is not None:
            for event_type in self._config.consumes_event_types:
                await self._mesh.subscribe(event_type, self._handle_outcome_event)
                logger.info("mesh adapter: subscribed to topic %r", event_type)

        self._running = True
        logger.info(
            "mesh adapter: started (peer_id=%s, capabilities=%s, consumes=%s)",
            self._peer_id,
            self._config.capabilities,
            self._config.consumes_event_types,
        )

    async def stop(self) -> None:
        """Unsubscribe from topics and stop participant (mesh + discovery)."""
        if not self._running:
            return

        self._running = False

        if self._mesh is not None:
            for event_type in self._config.consumes_event_types:
                await self._mesh.unsubscribe(event_type)

        # Cancel any pending response futures
        await self._turn_runner.cancel_pending()

        await self._participant.stop()

        logger.info("mesh adapter: stopped (peer_id=%s)", self._peer_id)

    async def publish(self, event: RavnEvent, topic: str) -> None:
        """Publish an event onto the mesh when the adapter is active."""
        if self._mesh is None:
            return
        await self._mesh.publish(event, topic=topic)

    async def _handle_rpc(self, message: dict) -> dict:
        """Handle incoming RPC messages (work_request, task_dispatch)."""
        msg_type = message.get("type", "")

        if msg_type == "work_request":
            return await self._handle_work_request(message)

        return {"error": "unknown_message_type", "type": msg_type}

    async def _handle_work_request(self, message: dict) -> dict:
        """Process a work_request: send prompt to CLI, collect result."""
        prompt = message.get("prompt", "")
        event_type = message.get("event_type", "")
        request_id = message.get("request_id", str(uuid.uuid4()))
        timeout_s = float(message.get("timeout_s", self._config.default_work_timeout_s))

        if not prompt:
            return {
                "status": "error",
                "request_id": request_id,
                "error": "empty prompt",
            }

        logger.info(
            "mesh adapter: work_request %s (event_type=%s, timeout=%.0fs)",
            request_id,
            event_type,
            timeout_s,
        )

        try:
            result_text = await asyncio.wait_for(
                self._execute_prompt(prompt, request_id),
                timeout=timeout_s,
            )

            response: dict[str, Any] = {
                "status": "complete",
                "request_id": request_id,
                "output": result_text,
                "event_type": event_type,
            }

            parsed = parse_outcome_block(result_text)
            if parsed is not None:
                response["outcome"] = {
                    "fields": parsed.fields,
                    "valid": parsed.valid,
                    "errors": parsed.errors,
                }

            return response
        except TimeoutError:
            logger.warning("mesh adapter: work_request %s timed out", request_id)
            return {
                "status": "timeout",
                "request_id": request_id,
                "event_type": event_type,
            }
        except Exception as exc:
            logger.error("mesh adapter: work_request %s failed: %s", request_id, exc)
            return {
                "status": "error",
                "request_id": request_id,
                "error": str(exc),
            }

    async def _execute_prompt(self, prompt: str, request_id: str) -> str:
        """Send prompt to CLI transport and collect the result text.

        Serialized via ``_execute_lock`` — the CLI handles one prompt at
        a time, so overlapping calls would corrupt the event callback chain.
        """
        return await self._turn_runner.run_prompt(prompt, request_id)

    async def _handle_outcome_event(self, event: RavnEvent) -> None:
        """Handle incoming outcome events from mesh subscriptions.

        When an outcome event arrives that matches our subscribed topics,
        feed the prompt to the CLI transport and publish the result back.
        """
        if event.type != RavnEventType.OUTCOME:
            return

        payload = event.payload
        prompt = payload.get("prompt", "")
        if not prompt:
            logger.debug("mesh adapter: ignoring outcome event without prompt")
            return

        event_type = payload.get("event_type", "")
        request_id = str(uuid.uuid4())

        logger.info(
            "mesh adapter: handling outcome event (event_type=%s, source=%s)",
            event_type,
            event.source,
        )

        try:
            result_text = await self._execute_prompt(prompt, request_id)
        except Exception as exc:
            logger.error("mesh adapter: outcome event handling failed: %s", exc)
            result_text = f"Error: {exc}"

        # Publish result back to mesh
        parsed = parse_outcome_block(result_text)
        outcome_payload: dict[str, Any] = {
            "event_type": event_type,
            "persona": self._config.persona,
            "source_peer_id": self._peer_id,
            "output": result_text,
        }
        if parsed is not None:
            outcome_payload["outcome"] = {
                "fields": parsed.fields,
                "valid": parsed.valid,
                "errors": parsed.errors,
            }

        response_event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=self._peer_id,
            payload=outcome_payload,
            timestamp=datetime.now(UTC),
            urgency=self._config.default_response_urgency,
            correlation_id=event.correlation_id,
            session_id=self._session_id,
        )

        if self._mesh is not None:
            topic = f"{self._config.persona}.completed"
            await self._mesh.publish(response_event, topic=topic)
            logger.info("mesh adapter: published response to topic %r", topic)
