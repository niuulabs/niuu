"""Mesh routing tools — event-type based work delegation between personas.

These tools enable persona-to-persona communication based on what event types
each persona consumes. Unlike cascade tools (which are task/coordinator focused),
mesh routing is semantic — "route this review work to whoever handles reviews".

The routing flow:
1. Agent calls route_work with an event_type and prompt
2. Discovery finds a peer whose persona consumes that event_type
3. Work is sent to that peer via mesh.send()
4. Peer executes and returns result
5. Result is returned to the calling agent
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING

from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort

if TYPE_CHECKING:
    from ravn.ports.discovery import DiscoveryPort
    from ravn.ports.mesh import MeshPort

logger = logging.getLogger(__name__)

_PERMISSION = "mesh:route"
_DEFAULT_WORK_REQUEST_TIMEOUT_S = 120.0


def _ok(content: str) -> ToolResult:
    return ToolResult(tool_call_id="", content=content)


def _err(content: str) -> ToolResult:
    return ToolResult(tool_call_id="", content=content, is_error=True)


class RouteWorkTool(ToolPort):
    """Route work to a peer based on event type.

    Finds a peer whose persona consumes the specified event_type and sends
    the work request to them. Returns the result from the peer.

    This enables semantic routing: "send this review to whoever handles reviews"
    rather than "send this to peer X".
    """

    def __init__(
        self,
        mesh: MeshPort | None = None,
        discovery: DiscoveryPort | None = None,
        timeout_s: float = _DEFAULT_WORK_REQUEST_TIMEOUT_S,
    ) -> None:
        self._mesh = mesh
        self._discovery = discovery
        self._timeout_s = timeout_s

    @property
    def name(self) -> str:
        return "route_work"

    @property
    def description(self) -> str:
        return (
            "Route work to a peer based on event type. "
            "Finds a peer whose persona consumes the specified event_type "
            "and sends the work request to them."
        )

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "description": (
                        "The event type that identifies what kind of work this is. "
                        "Examples: 'review.requested', 'code.requested', 'research.requested'. "
                        "The work will be routed to a peer whose persona consumes this event type."
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": "The work request to send to the peer.",
                },
                "timeout_s": {
                    "type": "number",
                    "description": (
                        f"Timeout in seconds (default {_DEFAULT_WORK_REQUEST_TIMEOUT_S})."
                    ),
                },
            },
            "required": ["event_type", "prompt"],
        }

    async def execute(self, input: dict) -> ToolResult:
        event_type = input.get("event_type", "")
        prompt = input.get("prompt", "")
        timeout_s = float(input.get("timeout_s", self._timeout_s))

        if not event_type:
            return _err("Error: event_type is required")
        if not prompt:
            return _err("Error: prompt is required")

        # Check if mesh and discovery are available
        if self._mesh is None or self._discovery is None:
            return _err("Error: mesh routing not available (mesh or discovery disabled)")

        # Find a peer that consumes this event type
        peer = None
        if hasattr(self._discovery, "find_peer_for_event_type"):
            peer = self._discovery.find_peer_for_event_type(event_type)

        if peer is None:
            return _err(f"No peer found that consumes event type '{event_type}'")

        # Send work request to the peer
        request_id = uuid.uuid4().hex[:8]
        message = {
            "type": "work_request",
            "event_type": event_type,
            "prompt": prompt,
            "request_id": request_id,
            "timeout_s": timeout_s,
        }

        try:
            logger.info(
                "route_work: sending to peer=%s event_type=%s request_id=%s",
                peer.peer_id,
                event_type,
                request_id,
            )
            reply = await self._mesh.send(peer.peer_id, message, timeout_s=timeout_s)

            if reply.get("status") == "complete":
                output = reply.get("output", "")
                outcome = reply.get("outcome")

                # Format response with structured outcome if available
                result_parts = [f"[From {peer.persona}]"]
                if outcome and outcome.get("valid"):
                    fields = outcome.get("fields", {})
                    result_parts.append(f"\n[Outcome: {json.dumps(fields)}]")
                result_parts.append(f"\n\n{output}")

                return _ok("".join(result_parts))

            if reply.get("status") == "timeout":
                return _err(f"Work request to {peer.peer_id} timed out after {timeout_s}s")

            if reply.get("status") == "error":
                error = reply.get("error", "unknown error")
                return _err(f"Work request failed: {error}")

            # Unexpected status
            return _err(f"Unexpected reply: {reply}")

        except TimeoutError:
            return _err(f"Mesh send to {peer.peer_id} timed out")
        except Exception as exc:
            logger.error("route_work: failed: %s", exc)
            return _err(f"route_work failed: {exc}")


class ListConsumersTool(ToolPort):
    """List peers that consume a given event type."""

    def __init__(self, discovery: DiscoveryPort | None = None) -> None:
        self._discovery = discovery

    @property
    def name(self) -> str:
        return "list_consumers"

    @property
    def description(self) -> str:
        return "List all peers that consume a given event type."

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "description": "The event type to search for consumers of.",
                },
            },
            "required": ["event_type"],
        }

    async def execute(self, input: dict) -> ToolResult:
        event_type = input.get("event_type", "")

        if not event_type:
            return _err("Error: event_type is required")

        if self._discovery is None:
            return _err("Error: discovery not available")

        peers = self._discovery.peers()
        consumers = []

        for peer in peers.values():
            if event_type in peer.consumes_event_types:
                consumers.append(
                    {
                        "peer_id": peer.peer_id,
                        "persona": peer.persona,
                        "status": peer.status,
                        "consumes": peer.consumes_event_types,
                    }
                )

        if not consumers:
            return _ok(f"No peers consume event type '{event_type}'")

        return _ok(json.dumps(consumers, indent=2))


def build_mesh_routing_tools(
    mesh: MeshPort | None = None,
    discovery: DiscoveryPort | None = None,
    timeout_s: float = _DEFAULT_WORK_REQUEST_TIMEOUT_S,
) -> list[ToolPort]:
    """Build the mesh routing tools.

    Returns an empty list if mesh or discovery is not available.
    """
    if mesh is None or discovery is None:
        return []

    return [
        RouteWorkTool(mesh=mesh, discovery=discovery, timeout_s=timeout_s),
        ListConsumersTool(discovery=discovery),
    ]
