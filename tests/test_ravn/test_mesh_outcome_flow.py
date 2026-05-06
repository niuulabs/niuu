"""Integration test: persona outcome event flow via SleipnirMeshAdapter.

Demonstrates the full event-driven flow:
1. Coder persona completes task → publishes "code.changed" outcome
2. Reviewer persona (subscribed to code.changed) receives event
3. Reviewer's handler creates a new task from the event

This test uses the _FakeSleipnirTransport to simulate the pub/sub layer,
proving the flow works regardless of underlying transport (nng, rabbitmq, etc).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from ravn.domain.events import RavnEvent, RavnEventType

# ---------------------------------------------------------------------------
# Fake Sleipnir transport (same as test_mesh.py)
# ---------------------------------------------------------------------------


@dataclass
class _FakeSubscription:
    """Fake Sleipnir subscription."""

    topic: str
    handler: Callable
    active: bool = True

    async def unsubscribe(self) -> None:
        self.active = False


class _FakeSleipnirTransport:
    """Fake Sleipnir transport implementing both publisher and subscriber.

    This simulates what NngTransport, RabbitMQTransport, NatsTransport, or
    RedisStreamsTransport would do — the SleipnirMeshAdapter doesn't care
    which one it's using.
    """

    def __init__(self) -> None:
        self.published: list[Any] = []
        self.subscriptions: list[_FakeSubscription] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)
        # Deliver to matching subscriptions
        for sub in self.subscriptions:
            if sub.active and self._matches(sub.topic, event.event_type):
                await sub.handler(event)

    async def subscribe(
        self,
        event_types: list[str],
        handler: Callable,
    ) -> _FakeSubscription:
        topic = event_types[0] if event_types else "*"
        sub = _FakeSubscription(topic=topic, handler=handler)
        self.subscriptions.append(sub)
        return sub

    def _matches(self, pattern: str, event_type: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return event_type.startswith(pattern[:-1])
        return pattern == event_type


# ---------------------------------------------------------------------------
# Fake persona configs
# ---------------------------------------------------------------------------


@dataclass
class FakeProduces:
    event_type: str = ""


@dataclass
class FakeConsumes:
    event_types: list[str] = field(default_factory=list)


@dataclass
class FakePersonaConfig:
    name: str
    produces: FakeProduces = field(default_factory=FakeProduces)
    consumes: FakeConsumes = field(default_factory=FakeConsumes)


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


class TestMeshOutcomeFlow:
    """Test the full outcome event flow between personas."""

    @pytest.mark.asyncio
    async def test_coder_publishes_reviewer_receives(self) -> None:
        """Coder finishes → publishes code.changed → Reviewer receives it."""
        # Shared transport (simulates the message broker)
        transport = _FakeSleipnirTransport()

        # --- CODER SETUP ---
        coder_mesh = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="coder-001",
        )
        coder_persona = FakePersonaConfig(
            name="coder",
            produces=FakeProduces(event_type="code.changed"),
        )

        # --- REVIEWER SETUP ---
        reviewer_mesh = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="reviewer-001",
        )
        # Persona config would be used by real commands.py handler
        _ = FakePersonaConfig(
            name="reviewer",
            consumes=FakeConsumes(event_types=["code.changed"]),
        )

        # Track what the reviewer receives
        received_events: list[RavnEvent] = []
        tasks_created: list[dict] = []

        async def reviewer_outcome_handler(event: RavnEvent) -> None:
            """Simulates commands.py _handle_outcome_event."""
            received_events.append(event)

            if event.type != RavnEventType.OUTCOME:
                return

            payload = event.payload
            event_type = payload.get("event_type", "")
            source_persona = payload.get("persona", "")
            outcome = payload.get("outcome", {})

            # This is what commands.py does — create a task
            task = {
                "task_id": f"event_{event_type.replace('.', '_')}_{event.task_id[:8]}",
                "title": f"Handle {event_type} from {source_persona}",
                "triggered_by": f"mesh:outcome:{event_type}",
                "context": json.dumps(outcome),
            }
            tasks_created.append(task)

        # Subscribe reviewer to code.changed
        await reviewer_mesh.subscribe("code.changed", reviewer_outcome_handler)

        # --- CODER PUBLISHES OUTCOME ---
        # This simulates DriveLoop._emit_mesh_outcome_event()
        outcome_event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source="drive_loop",
            payload={
                "event_type": coder_persona.produces.event_type,
                "persona": coder_persona.name,
                "success": True,
                "outcome": {
                    "files_changed": ["src/main.py", "src/utils.py"],
                    "commit_sha": "abc123",
                },
            },
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="task-coder-001",
            session_id="session-001",
            task_id="task-coder-001",
        )

        await coder_mesh.publish(outcome_event, topic="code.changed")

        # --- VERIFY ---
        # Reviewer should have received the event
        assert len(received_events) == 1
        assert received_events[0].type == RavnEventType.OUTCOME
        assert received_events[0].payload["event_type"] == "code.changed"
        assert received_events[0].payload["persona"] == "coder"
        assert received_events[0].payload["outcome"]["commit_sha"] == "abc123"

        # Task should have been created
        assert len(tasks_created) == 1
        assert tasks_created[0]["triggered_by"] == "mesh:outcome:code.changed"
        assert "Handle code.changed from coder" in tasks_created[0]["title"]

        # Cleanup
        await reviewer_mesh.unsubscribe("code.changed")

    @pytest.mark.asyncio
    async def test_multiple_consumers_all_receive(self) -> None:
        """Multiple personas consuming same event type all receive it."""
        transport = _FakeSleipnirTransport()

        # One producer
        coder_mesh = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="coder-001",
        )

        # Two consumers
        reviewer1_received: list[RavnEvent] = []
        reviewer2_received: list[RavnEvent] = []

        reviewer1_mesh = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="reviewer-001",
        )
        reviewer2_mesh = SleipnirMeshAdapter(
            publisher=transport,
            subscriber=transport,
            own_peer_id="reviewer-002",
        )

        async def handler1(event: RavnEvent) -> None:
            reviewer1_received.append(event)

        async def handler2(event: RavnEvent) -> None:
            reviewer2_received.append(event)

        await reviewer1_mesh.subscribe("code.changed", handler1)
        await reviewer2_mesh.subscribe("code.changed", handler2)

        # Publish
        outcome_event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source="drive_loop",
            payload={"event_type": "code.changed", "persona": "coder"},
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="task-001",
            session_id="",
            task_id="task-001",
        )
        await coder_mesh.publish(outcome_event, topic="code.changed")

        # Both should receive
        assert len(reviewer1_received) == 1
        assert len(reviewer2_received) == 1

    @pytest.mark.asyncio
    async def test_chain_coder_to_reviewer_to_deployer(self) -> None:
        """Full chain: coder → reviewer → deployer."""
        transport = _FakeSleipnirTransport()

        # Three personas
        coder_mesh = SleipnirMeshAdapter(
            publisher=transport, subscriber=transport, own_peer_id="coder"
        )
        reviewer_mesh = SleipnirMeshAdapter(
            publisher=transport, subscriber=transport, own_peer_id="reviewer"
        )
        deployer_mesh = SleipnirMeshAdapter(
            publisher=transport, subscriber=transport, own_peer_id="deployer"
        )

        chain_log: list[str] = []

        # Reviewer listens to code.changed, publishes review.passed
        async def reviewer_handler(event: RavnEvent) -> None:
            chain_log.append(f"reviewer received: {event.payload.get('event_type')}")
            # Simulate reviewer completing and publishing
            review_outcome = RavnEvent(
                type=RavnEventType.OUTCOME,
                source="reviewer",
                payload={
                    "event_type": "review.passed",
                    "persona": "reviewer",
                    "outcome": {"verdict": "approved"},
                },
                timestamp=datetime.now(UTC),
                urgency=0.3,
                correlation_id=event.correlation_id,
                session_id="",
                task_id="review-001",
            )
            await reviewer_mesh.publish(review_outcome, topic="review.passed")

        # Deployer listens to review.passed
        async def deployer_handler(event: RavnEvent) -> None:
            chain_log.append(f"deployer received: {event.payload.get('event_type')}")

        await reviewer_mesh.subscribe("code.changed", reviewer_handler)
        await deployer_mesh.subscribe("review.passed", deployer_handler)

        # Coder publishes
        code_outcome = RavnEvent(
            type=RavnEventType.OUTCOME,
            source="coder",
            payload={"event_type": "code.changed", "persona": "coder"},
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="task-001",
            session_id="",
            task_id="code-001",
        )
        await coder_mesh.publish(code_outcome, topic="code.changed")

        # Verify chain
        assert chain_log == [
            "reviewer received: code.changed",
            "deployer received: review.passed",
        ]

    @pytest.mark.asyncio
    async def test_no_cross_talk_different_topics(self) -> None:
        """Personas only receive events they subscribed to."""
        transport = _FakeSleipnirTransport()

        reviewer_mesh = SleipnirMeshAdapter(
            publisher=transport, subscriber=transport, own_peer_id="reviewer"
        )
        deployer_mesh = SleipnirMeshAdapter(
            publisher=transport, subscriber=transport, own_peer_id="deployer"
        )

        reviewer_received: list[str] = []
        deployer_received: list[str] = []

        async def reviewer_handler(event: RavnEvent) -> None:
            reviewer_received.append(event.payload.get("event_type", ""))

        async def deployer_handler(event: RavnEvent) -> None:
            deployer_received.append(event.payload.get("event_type", ""))

        # Reviewer subscribes to code.changed only
        await reviewer_mesh.subscribe("code.changed", reviewer_handler)
        # Deployer subscribes to deploy.requested only
        await deployer_mesh.subscribe("deploy.requested", deployer_handler)

        # Publish code.changed
        coder_mesh = SleipnirMeshAdapter(
            publisher=transport, subscriber=transport, own_peer_id="coder"
        )
        await coder_mesh.publish(
            RavnEvent(
                type=RavnEventType.OUTCOME,
                source="coder",
                payload={"event_type": "code.changed"},
                timestamp=datetime.now(UTC),
                urgency=0.3,
                correlation_id="1",
                session_id="",
            ),
            topic="code.changed",
        )

        # Only reviewer should receive
        assert reviewer_received == ["code.changed"]
        assert deployer_received == []
