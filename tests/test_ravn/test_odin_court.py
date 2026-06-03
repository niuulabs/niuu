"""Tests for ODIN court fan-in resolution of Valkyrie judgments."""

from __future__ import annotations

from datetime import timedelta

from ravn.odin.court import InMemoryCourtAuditSink, OdinCourt
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from sleipnir.domain.catalog import (
    signal_received,
    valkyrie_action_proposed,
    valkyrie_judgment_proposed,
    valkyrie_judgment_rejected,
)
from sleipnir.domain.events import SleipnirEvent


async def _collect(bus: InProcessBus, event_types: list[str]) -> list[SleipnirEvent]:
    received: list[SleipnirEvent] = []

    async def handler(event: SleipnirEvent) -> None:
        received.append(event)

    await bus.subscribe(event_types, handler)
    return received


def _judgment(
    *,
    environment_id: str = "cluster-a",
    valkyrie_id: str = "valkyrie:k8s-a",
    tier: str = "ambient",
    recommended_action: str = "inspect",
    authority: str = "autonomous",
    confidence: float = 0.82,
    correlation_id: str = "corr-1",
) -> SleipnirEvent:
    return valkyrie_judgment_proposed(
        environment_id=environment_id,
        valkyrie_id=valkyrie_id,
        attention_tier=tier,
        recommended_action=recommended_action,
        authority_boundary=authority,
        confidence=confidence,
        operational_state="investigating",
        rationale=f"{valkyrie_id} sees {tier}",
        signal_refs=[f"signal:{correlation_id}"],
        evidence=[{"signal": correlation_id, "valkyrie": valkyrie_id}],
        target_surfaces=["surface:ops"],
        expires_at="",
        dissent_refs=[],
        correlation_ids={"root": correlation_id},
        source=valkyrie_id,
        correlation_id=correlation_id,
    )


def _action(
    *,
    environment_id: str = "cluster-a",
    valkyrie_id: str = "valkyrie:k8s-a",
    action_id: str = "action-1",
    authority: str = "autonomous",
    dry_run: bool = False,
    correlation_id: str = "corr-1",
) -> SleipnirEvent:
    return valkyrie_action_proposed(
        environment_id=environment_id,
        valkyrie_id=valkyrie_id,
        action_id=action_id,
        capability="k8s.inspect_pod",
        action_authority=authority,
        target={"namespace": "default", "pod": "api-7d9"},
        rationale="Inspect pod before taking disruptive action.",
        evidence=[{"action": action_id}],
        target_surfaces=["surface:ops"],
        dry_run=dry_run,
        source=valkyrie_id,
        correlation_id=correlation_id,
    )


async def test_agreeing_judgments_emit_one_attention_decision() -> None:
    bus = InProcessBus()
    court = OdinCourt(publisher=bus, subscriber=bus, quorum_size=2)
    await court.start()
    decisions = await _collect(bus, [registry.ATTENTION_DECISION_MADE])

    await bus.publish(_judgment(valkyrie_id="valkyrie:k8s-a", correlation_id="root-a"))
    await bus.publish(_judgment(valkyrie_id="valkyrie:k8s-b", correlation_id="root-a"))
    await bus.flush()
    await bus.flush()

    assert [event.event_type for event in decisions] == [registry.ATTENTION_DECISION_MADE]
    payload = decisions[0].payload
    assert payload["environment_id"] == "cluster-a"
    assert payload["root_correlation_id"] == "root-a"
    assert payload["decision"] == "record_only"
    assert payload["tier"] == "ambient"
    assert len(payload["judgment_refs"]) == 2
    assert len(court.audit_records()) == 1

    await court.stop()


async def test_disagreement_is_preserved_as_queryable_dissent() -> None:
    bus = InProcessBus()
    audit = InMemoryCourtAuditSink()
    court = OdinCourt(publisher=bus, subscriber=bus, audit_sink=audit, quorum_size=2)
    await court.start()
    decisions = await _collect(bus, [registry.ATTENTION_DECISION_MADE])

    await bus.publish(
        _judgment(
            valkyrie_id="valkyrie:k8s-a",
            tier="ambient",
            recommended_action="inspect",
            correlation_id="root-disagree",
        )
    )
    await bus.publish(
        _judgment(
            valkyrie_id="valkyrie:k8s-b",
            tier="urgent",
            recommended_action="open_huddle",
            authority="human_review_required",
            correlation_id="root-disagree",
        )
    )
    await bus.flush()
    await bus.flush()

    assert decisions[0].payload["tier"] == "urgent"
    assert decisions[0].payload["decision"] == "open_huddle"
    record = audit.find(environment_id="cluster-a", root_correlation_id="root-disagree")
    assert record is not None
    assert record.dissent
    assert {item["type"] for item in record.dissent} >= {
        "tier_disagreement",
        "recommended_action_disagreement",
        "action_authority_disagreement",
    }

    await court.stop()


async def test_timeout_resolves_pending_case() -> None:
    bus = InProcessBus()
    court = OdinCourt(publisher=bus, subscriber=bus, quorum_size=2, timeout_s=1.0)
    await court.start()
    decisions = await _collect(bus, [registry.ATTENTION_DECISION_MADE])
    judgment = _judgment(tier="present", correlation_id="root-timeout")

    await bus.publish(judgment)
    await bus.flush()
    assert decisions == []

    await court.sweep_expired(now=judgment.timestamp + timedelta(seconds=2))
    await bus.flush()
    await bus.flush()

    assert len(decisions) == 1
    assert decisions[0].payload["decision"] == "notify"
    assert decisions[0].payload["judgment_refs"] == [judgment.event_id]

    await court.stop()


async def test_rejected_judgment_is_excluded_but_audited() -> None:
    bus = InProcessBus()
    court = OdinCourt(publisher=bus, subscriber=bus, quorum_size=1)
    await court.start()
    decisions = await _collect(bus, [registry.ATTENTION_DECISION_MADE])
    rejected = valkyrie_judgment_rejected(
        environment_id="cluster-a",
        valkyrie_id="valkyrie:k8s-b",
        canonical_event_type=registry.VALKYRIE_JUDGMENT_PROPOSED,
        errors=["tier invalid"],
        rejected_fields={"tier": "watch"},
        source="ravn:drive_loop",
        correlation_id="root-reject",
    )
    valid = _judgment(valkyrie_id="valkyrie:k8s-a", correlation_id="root-reject")

    await bus.publish(rejected)
    await bus.publish(valid)
    await bus.flush()
    await bus.flush()

    payload = decisions[0].payload
    assert payload["judgment_refs"] == [valid.event_id]
    assert rejected.event_id not in payload["judgment_refs"]
    record = court.audit_records()[0]
    assert record.rejected_refs == [rejected.event_id]
    assert record.dissent[0]["type"] == "rejected_judgment"

    await court.stop()


async def test_action_boundary_blocks_review_required_actions() -> None:
    bus = InProcessBus()
    court = OdinCourt(publisher=bus, subscriber=bus, quorum_size=2)
    await court.start()
    decisions = await _collect(bus, [registry.ATTENTION_DECISION_MADE])
    requests = await _collect(bus, [registry.VALKYRIE_ACTION_REQUESTED])

    await bus.publish(
        _judgment(
            tier="present",
            authority="human_review_required",
            correlation_id="root-review",
        )
    )
    await bus.publish(_action(authority="human_review_required", correlation_id="root-review"))
    await bus.flush()
    await bus.flush()

    assert decisions[0].payload["decision"] == "draft_for_review"
    assert decisions[0].payload["action_authorization"] == "human_review_required"
    assert requests == []

    await court.stop()


async def test_yolo_action_boundary_emits_action_request() -> None:
    bus = InProcessBus()
    court = OdinCourt(publisher=bus, subscriber=bus, quorum_size=2)
    await court.start()
    decisions = await _collect(bus, [registry.ATTENTION_DECISION_MADE])
    requests = await _collect(bus, [registry.VALKYRIE_ACTION_REQUESTED])

    await bus.publish(_judgment(tier="present", authority="yolo", correlation_id="root-yolo"))
    await bus.publish(_action(authority="yolo", correlation_id="root-yolo"))
    await bus.flush()
    await bus.flush()

    assert decisions[0].payload["decision"] == "autonomous_action"
    assert decisions[0].payload["action_authorization"] == "autonomous"
    assert len(requests) == 1
    assert requests[0].payload["authority_boundary"] == "autonomous"
    assert requests[0].payload["dry_run"] is False

    await court.stop()


async def test_environment_fixture_decisions_for_k8s_inbox_and_printer() -> None:
    bus = InProcessBus()
    court = OdinCourt(publisher=bus, subscriber=bus, quorum_size=1)
    await court.start()
    decisions = await _collect(bus, [registry.ATTENTION_DECISION_MADE])
    rooms = await _collect(bus, [registry.ROOM_OPENED])

    k8s_signal = signal_received(
        environment_id="cluster-prod",
        environment_type="k8s",
        signal_source="kubernetes.events",
        signal_kind="kubernetes",
        severity="error",
        data={"reason": "CrashLoopBackOff"},
        source="adapter:kubernetes",
        correlation_id="root-k8s",
    )
    await bus.publish(k8s_signal)
    await bus.publish(
        _judgment(
            environment_id="cluster-prod",
            tier="urgent",
            recommended_action="open_huddle",
            authority="human_review_required",
            correlation_id="root-k8s",
        )
    )
    await bus.publish(
        _judgment(
            environment_id="host-mail",
            tier="present",
            recommended_action="draft_reply",
            authority="human_review_required",
            correlation_id="root-inbox",
        )
    )
    await bus.publish(
        _judgment(
            environment_id="printer-cell-a",
            tier="ambient",
            recommended_action="record_resin_low",
            authority="autonomous",
            correlation_id="root-printer",
        )
    )
    await bus.flush()
    await bus.flush()

    by_environment = {event.payload["environment_id"]: event.payload for event in decisions}
    assert by_environment["cluster-prod"]["decision"] == "open_huddle"
    assert by_environment["host-mail"]["decision"] == "notify"
    assert by_environment["printer-cell-a"]["decision"] == "record_only"
    assert len(rooms) == 1
    assert rooms[0].payload["environment_id"] == "cluster-prod"
    assert "human:operator" in rooms[0].payload["participants"]

    await court.stop()
