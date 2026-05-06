"""Tests for mesh routing tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ravn.adapters.tools.mesh_routing_tools import (
    ListConsumersTool,
    RouteWorkTool,
    build_mesh_routing_tools,
)


@dataclass
class FakePeer:
    """Fake peer for testing."""

    peer_id: str
    persona: str
    status: str = "ready"
    consumes_event_types: list[str] = field(default_factory=list)


class FakeDiscovery:
    """Fake discovery port for testing."""

    def __init__(self, peers: dict[str, FakePeer] | None = None) -> None:
        self._peers = peers or {}

    def peers(self) -> dict[str, FakePeer]:
        return self._peers

    def find_peer_for_event_type(self, event_type: str) -> FakePeer | None:
        for peer in self._peers.values():
            if event_type in peer.consumes_event_types:
                return peer
        return None


class FakeMesh:
    """Fake mesh port for testing."""

    def __init__(self, responses: dict[str, dict] | None = None) -> None:
        self._responses = responses or {}
        self.sent_messages: list[tuple[str, dict]] = []

    async def send(self, peer_id: str, message: dict, timeout_s: float = 120.0) -> dict[str, Any]:
        self.sent_messages.append((peer_id, message))
        return self._responses.get(peer_id, {"status": "error", "error": "no response"})


class TestRouteWorkTool:
    """Tests for RouteWorkTool."""

    def test_name_and_description(self) -> None:
        tool = RouteWorkTool()
        assert tool.name == "route_work"
        assert "event type" in tool.description.lower()

    def test_input_schema(self) -> None:
        tool = RouteWorkTool()
        schema = tool.input_schema
        assert schema["required"] == ["event_type", "prompt"]
        assert "event_type" in schema["properties"]
        assert "prompt" in schema["properties"]

    @pytest.mark.asyncio
    async def test_returns_error_when_no_mesh(self) -> None:
        tool = RouteWorkTool(mesh=None, discovery=None)
        result = await tool.execute({"event_type": "review.requested", "prompt": "test"})
        assert result.is_error
        assert "not available" in result.content

    @pytest.mark.asyncio
    async def test_returns_error_when_no_peer_found(self) -> None:
        discovery = FakeDiscovery({})
        mesh = FakeMesh()
        tool = RouteWorkTool(mesh=mesh, discovery=discovery)

        result = await tool.execute({"event_type": "review.requested", "prompt": "test"})
        assert result.is_error
        assert "No peer found" in result.content

    @pytest.mark.asyncio
    async def test_routes_to_peer_consuming_event_type(self) -> None:
        peer = FakePeer(
            peer_id="reviewer-abc",
            persona="reviewer",
            consumes_event_types=["review.requested"],
        )
        discovery = FakeDiscovery({"reviewer-abc": peer})
        mesh = FakeMesh(
            {
                "reviewer-abc": {
                    "status": "complete",
                    "output": "Review complete",
                }
            }
        )
        tool = RouteWorkTool(mesh=mesh, discovery=discovery)

        result = await tool.execute(
            {"event_type": "review.requested", "prompt": "Review this code"}
        )

        assert not result.is_error
        assert "[From reviewer]" in result.content
        assert "Review complete" in result.content
        assert len(mesh.sent_messages) == 1
        _, msg = mesh.sent_messages[0]
        assert msg["type"] == "work_request"
        assert msg["event_type"] == "review.requested"

    @pytest.mark.asyncio
    async def test_includes_structured_outcome_when_present(self) -> None:
        peer = FakePeer(
            peer_id="reviewer-abc",
            persona="reviewer",
            consumes_event_types=["review.requested"],
        )
        discovery = FakeDiscovery({"reviewer-abc": peer})
        mesh = FakeMesh(
            {
                "reviewer-abc": {
                    "status": "complete",
                    "output": "Here is the review\n---outcome---\nverdict: pass\n---end---",
                    "outcome": {
                        "fields": {"verdict": "pass"},
                        "valid": True,
                        "errors": [],
                    },
                }
            }
        )
        tool = RouteWorkTool(mesh=mesh, discovery=discovery)

        result = await tool.execute({"event_type": "review.requested", "prompt": "Review this"})

        assert not result.is_error
        assert "[From reviewer]" in result.content
        assert "[Outcome:" in result.content
        assert '"verdict": "pass"' in result.content

    @pytest.mark.asyncio
    async def test_handles_timeout(self) -> None:
        peer = FakePeer(
            peer_id="slow-peer",
            persona="slow",
            consumes_event_types=["work.requested"],
        )
        discovery = FakeDiscovery({"slow-peer": peer})
        mesh = FakeMesh({"slow-peer": {"status": "timeout"}})
        tool = RouteWorkTool(mesh=mesh, discovery=discovery)

        result = await tool.execute({"event_type": "work.requested", "prompt": "Do work"})

        assert result.is_error
        assert "timed out" in result.content

    @pytest.mark.asyncio
    async def test_handles_error_response(self) -> None:
        peer = FakePeer(
            peer_id="broken-peer",
            persona="broken",
            consumes_event_types=["work.requested"],
        )
        discovery = FakeDiscovery({"broken-peer": peer})
        mesh = FakeMesh({"broken-peer": {"status": "error", "error": "internal failure"}})
        tool = RouteWorkTool(mesh=mesh, discovery=discovery)

        result = await tool.execute({"event_type": "work.requested", "prompt": "Do work"})

        assert result.is_error
        assert "internal failure" in result.content


class TestListConsumersTool:
    """Tests for ListConsumersTool."""

    def test_name_and_description(self) -> None:
        tool = ListConsumersTool()
        assert tool.name == "list_consumers"
        assert "event type" in tool.description.lower()

    @pytest.mark.asyncio
    async def test_returns_error_when_no_discovery(self) -> None:
        tool = ListConsumersTool(discovery=None)
        result = await tool.execute({"event_type": "review.requested"})
        assert result.is_error
        assert "not available" in result.content

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_consumers(self) -> None:
        discovery = FakeDiscovery({})
        tool = ListConsumersTool(discovery=discovery)
        result = await tool.execute({"event_type": "review.requested"})
        assert not result.is_error
        assert "No peers consume" in result.content

    @pytest.mark.asyncio
    async def test_lists_consumers(self) -> None:
        peers = {
            "reviewer-1": FakePeer(
                peer_id="reviewer-1",
                persona="reviewer",
                consumes_event_types=["review.requested", "code.changed"],
            ),
            "coder-1": FakePeer(
                peer_id="coder-1",
                persona="coder",
                consumes_event_types=["code.requested"],
            ),
        }
        discovery = FakeDiscovery(peers)
        tool = ListConsumersTool(discovery=discovery)

        result = await tool.execute({"event_type": "review.requested"})

        assert not result.is_error
        assert "reviewer-1" in result.content
        assert "reviewer" in result.content
        assert "coder-1" not in result.content


class TestBuildMeshRoutingTools:
    """Tests for build_mesh_routing_tools factory."""

    def test_returns_empty_when_no_mesh(self) -> None:
        tools = build_mesh_routing_tools(mesh=None, discovery=FakeDiscovery())
        assert tools == []

    def test_returns_empty_when_no_discovery(self) -> None:
        tools = build_mesh_routing_tools(mesh=FakeMesh(), discovery=None)
        assert tools == []

    def test_returns_tools_when_both_available(self) -> None:
        tools = build_mesh_routing_tools(mesh=FakeMesh(), discovery=FakeDiscovery())
        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"route_work", "list_consumers"}
