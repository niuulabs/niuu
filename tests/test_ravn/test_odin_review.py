"""The unified ODIN review path: one envelope, one lifecycle, one dispatcher."""

from __future__ import annotations

import pytest

from ravn.adapters.reflection.flock_learning import FlockLearningStore
from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.odin.review import (
    JsonReviewStore,
    ReviewItem,
    ReviewKind,
    ReviewRequester,
    ReviewStatus,
    item_targets,
    review_decided_event,
    review_requested_event,
)
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.models import OperationalSignal
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from tests.ravn.fixtures.fakes import BusRecorder

OOM_SIGNAL = OperationalSignal(
    signal_id="sig-oom-1",
    event_type="signal.kubernetes.event",
    environment_id="cluster-a",
    domain="k8s",
    severity="warning",
    summary="Pod OOMKilled",
    payload={"kind": "Pod", "reason": "OOMKilled", "name": "payments-7d4", "namespace": "payments"},
)


def _item(**overrides) -> ReviewItem:
    data = {
        "kind": ReviewKind.EVOLUTION_BUILD.value,
        "requested_action": "install",
        "environment_id": "cluster-a",
        "valkyrie_id": "valkyrie:k8s-a",
        "title": "probe",
        "summary": "a probe",
    }
    data.update(overrides)
    return ReviewItem.new(**data)


# ---------------------------------------------------------------------------
# Contract: envelope, store, requester
# ---------------------------------------------------------------------------


def test_review_item_round_trips_through_event_payload() -> None:
    item = _item(evidence={"artifact": {"content": "x"}}, dedupe_key="k")
    event = review_requested_event(item, source="test")
    assert event.event_type == registry.ODIN_REVIEW_REQUESTED
    restored = ReviewItem.from_payload(event.payload)
    assert restored == item


def test_review_item_payload_requires_identity_fields() -> None:
    with pytest.raises(ValueError, match="item_id"):
        ReviewItem.from_payload({"kind": "evolution_build", "environment_id": "e"})


def test_decided_event_requires_a_decision() -> None:
    item = _item()
    with pytest.raises(ValueError, match="no decision"):
        review_decided_event(item, source="test")
    item.decide(decision="approved", operator_id="op")
    assert review_decided_event(item, source="test").payload["status"] == "approved"


def test_autonomy_items_require_the_change_autonomy_capability() -> None:
    item = _item(kind=ReviewKind.AUTONOMY_CHANGE.value, requested_action="set_autonomy_mode")
    assert item.requested_capability == "change_autonomy"
    assert _item().requested_capability == "approve"


def test_store_persists_and_reloads_items(tmp_path) -> None:
    store = JsonReviewStore(tmp_path / "outbox.json")
    item = _item(dedupe_key="evolution_build:cluster-a:cap")
    store.save(item)

    reloaded = JsonReviewStore(tmp_path / "outbox.json")
    assert reloaded.get(item.item_id) == item
    assert reloaded.find_pending("evolution_build:cluster-a:cap") is not None
    assert reloaded.list(status=ReviewStatus.PENDING.value, kind="evolution_build")


async def test_requester_dedupes_and_reannounces(tmp_path) -> None:
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    requester = ReviewRequester(
        publisher=bus,
        store=JsonReviewStore(tmp_path / "outbox.json"),
        source="test",
    )

    first = await requester.request(_item(dedupe_key="dup"))
    duplicate = await requester.request(_item(dedupe_key="dup"))
    await bus.flush()
    assert first is not None
    assert duplicate is None
    assert requester.has_pending("dup")
    assert len(await recorder.of_type(registry.ODIN_REVIEW_REQUESTED)) == 1

    # A restarted requester re-announces what is still pending.
    restarted = ReviewRequester(
        publisher=bus,
        store=JsonReviewStore(tmp_path / "outbox.json"),
        source="test",
    )
    assert await restarted.reannounce() == 1
    await bus.flush()
    assert len(await recorder.of_type(registry.ODIN_REVIEW_REQUESTED)) == 2


def test_item_targets_by_audience() -> None:
    me = {
        "valkyrie_id": "valkyrie:k8s-a",
        "environment_id": "cluster-a",
        "flock_ids": ["k8s-valkyries"],
    }
    assert item_targets(_item(), **me)
    assert not item_targets(_item(valkyrie_id="valkyrie:other"), **me)
    assert item_targets(_item(valkyrie_id="", audience="environment"), **me)
    assert not item_targets(
        _item(valkyrie_id="", environment_id="cluster-b", audience="environment"), **me
    )
    assert item_targets(
        _item(valkyrie_id="", audience="flock", flock_id="flock:k8s-valkyries"), **me
    )
    assert not item_targets(_item(valkyrie_id="", audience="flock", flock_id="printer-cell"), **me)


# ---------------------------------------------------------------------------
# The guarded round-trip: build → hold → request → approve → install
# ---------------------------------------------------------------------------


def _runtime(tmp_path, *, autonomy_mode: str = "guarded") -> tuple[ResidentLearningRuntime, ...]:
    skill_dir = tmp_path / "skills"
    skills = SkillManagementRegistry(
        FileSkillRegistry(skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False),
        metadata_path=tmp_path / "skill_management.json",
    )
    bus = InProcessBus()
    store = JsonReviewStore(tmp_path / "review_outbox.json")
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            domain="k8s",
            flock_ids=["flock:k8s-valkyries"],
            autonomy_mode=autonomy_mode,
        ),
        skills=skills,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / "tools",
        learning_store=FlockLearningStore(tmp_path / "flock_learning.json"),
        legacy_probe_builder_enabled=True,
        review_requester=ReviewRequester(
            publisher=bus,
            store=store,
            source="valkyrie:k8s-a",
        ),
    )
    return runtime, bus, store, skills


async def test_guarded_build_holds_and_operator_approval_installs(tmp_path) -> None:
    runtime, bus, store, skills = _runtime(tmp_path)
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    await runtime.start()

    held = await runtime.process_signal(OOM_SIGNAL)
    assert held["decision"] == "capability_build_held"

    requested = await recorder.of_type(registry.ODIN_REVIEW_REQUESTED)
    assert len(requested) == 1
    payload = requested[0].payload
    assert payload["kind"] == "evolution_build"
    assert payload["requested_action"] == "install"
    assert payload["evidence"]["artifact"]["tool_code"]
    assert payload["evidence"]["review"]["outcome"] == "needs_approval"
    assert payload["evidence"]["signal_ids"] == ["sig-oom-1"]

    # The operator approves the exact item the resident filed.
    item = ReviewItem.from_payload(payload)
    item.decide(decision="approved", operator_id="human:jozef", reason="looks safe")
    await bus.publish(review_decided_event(item, source="ravn:odin-review"))
    await bus.flush()

    skill_name = payload["title"]
    assert await skills.get_runnable_skill(skill_name) is not None
    resolved = await recorder.of_type(registry.ODIN_REVIEW_RESOLVED)
    assert len(resolved) == 1
    assert resolved[0].payload["apply_outcome"] == "applied"
    assert any(event.event_type == registry.FLOCK_LEARNING_PROPOSED for event in recorder.events), (
        "an operator-approved self-build must still teach the flock"
    )
    outbox_item = store.get(item.item_id)
    assert outbox_item.status == ReviewStatus.APPLIED.value

    # Redelivery of the same decision is a no-op.
    await bus.publish(review_decided_event(item, source="ravn:odin-review"))
    await bus.flush()
    assert len(await recorder.of_type(registry.ODIN_REVIEW_RESOLVED)) == 1

    await runtime.stop()


async def test_rejected_build_declines_durably_and_never_rebuilds(tmp_path) -> None:
    runtime, bus, store, skills = _runtime(tmp_path)
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    await runtime.start()

    held = await runtime.process_signal(OOM_SIGNAL)
    assert held["decision"] == "capability_build_held"
    payload = (await recorder.of_type(registry.ODIN_REVIEW_REQUESTED))[0].payload

    item = ReviewItem.from_payload(payload)
    item.decide(decision="rejected", operator_id="human:jozef", reason="not needed")
    await bus.publish(review_decided_event(item, source="ravn:odin-review"))
    await bus.flush()

    assert await skills.get_runnable_skill(payload["title"]) is None
    resolved = await recorder.of_type(registry.ODIN_REVIEW_RESOLVED)
    assert resolved[0].payload["apply_outcome"] == "applied"
    assert resolved[0].payload["status"] == "rejected"

    # The dream consolidation reopens the capability, but the durable decline
    # keeps the resident from rebuilding and re-filing the same item.
    assert await runtime.reopen_unresolved_capabilities() == 1
    again = await runtime.process_signal(OOM_SIGNAL)
    assert again["decision"] == "defer_and_request_capability"
    assert len(await recorder.of_type(registry.ODIN_REVIEW_REQUESTED)) == 1

    await runtime.stop()


async def test_pending_review_blocks_capability_reopen(tmp_path) -> None:
    runtime, bus, _store, _skills = _runtime(tmp_path)
    await runtime.start()

    held = await runtime.process_signal(OOM_SIGNAL)
    assert held["decision"] == "capability_build_held"
    # The held build is awaiting the operator; the consolidation dream must
    # not reopen the gap and trigger a duplicate build.
    assert await runtime.reopen_unresolved_capabilities() == 0

    await runtime.stop()


# ---------------------------------------------------------------------------
# Court escalations and skill promotions through the same dispatcher
# ---------------------------------------------------------------------------


async def test_approved_court_escalation_requests_the_drafted_action(tmp_path) -> None:
    runtime, bus, _store, _skills = _runtime(tmp_path, autonomy_mode="autonomous")
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    await runtime.start()

    item = _item(
        kind=ReviewKind.COURT_ESCALATION.value,
        requested_action="execute_action",
        evidence={
            "audit_ref": "odin-court:cluster-a:sig-1",
            "tier": "present",
            "action": {
                "action_id": "act-1",
                "capability": "k8s.restart_pod",
                "valkyrie_id": "valkyrie:k8s-a",
                "target": {"namespace": "payments", "pod": "payments-7d4"},
                "dry_run": False,
            },
        },
    )
    item.decide(decision="approved", operator_id="human:jozef", reason="do it")
    await bus.publish(review_decided_event(item, source="ravn:odin-review"))
    await bus.flush()

    actions = await recorder.of_type(registry.VALKYRIE_ACTION_REQUESTED)
    assert len(actions) == 1
    assert actions[0].payload["capability"] == "k8s.restart_pod"
    assert actions[0].payload["authority_boundary"] == "operator_approved"
    assert actions[0].payload["dry_run"] is False
    await runtime.stop()


async def test_rejected_court_escalation_suppresses_attention(tmp_path) -> None:
    runtime, bus, _store, _skills = _runtime(tmp_path, autonomy_mode="autonomous")
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    await runtime.start()

    item = _item(
        kind=ReviewKind.COURT_ESCALATION.value,
        requested_action="execute_action",
        evidence={"audit_ref": "odin-court:cluster-a:sig-2", "action": {}},
    )
    item.decide(decision="rejected", operator_id="human:jozef", reason="noise")
    await bus.publish(review_decided_event(item, source="ravn:odin-review"))
    await bus.flush()

    suppressed = await recorder.of_type(registry.ATTENTION_SUPPRESSED)
    assert len(suppressed) == 1
    assert suppressed[0].payload["audit_ref"] == "odin-court:cluster-a:sig-2"
    assert suppressed[0].payload["operator_id"] == "human:jozef"
    await runtime.stop()


async def test_approved_promotion_promotes_and_announces(tmp_path) -> None:
    runtime, bus, _store, skills = _runtime(tmp_path, autonomy_mode="guarded")
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    await skills.create(
        name="proven-probe",
        content="# skill: proven-probe\n\nmetadata:\n  capability: x\n",
        scope="private",
    )
    await runtime.start()

    item = _item(
        kind=ReviewKind.SKILL_PROMOTION.value,
        requested_action="promote",
        title="proven-probe",
        evidence={"skill_name": "proven-probe", "from_scope": "private", "to_scope": "environment"},
    )
    item.decide(decision="approved", operator_id="human:jozef", reason="proved itself")
    await bus.publish(review_decided_event(item, source="ravn:odin-review"))
    await bus.flush()

    assert (await skills.show("proven-probe"))["metadata"]["scope"] == "environment"
    promoted = await recorder.of_type(registry.LEARNING_PROMOTED)
    assert len(promoted) == 1
    assert promoted[0].payload["to_scope"] == "environment"
    assert promoted[0].payload["action_kind"] == "promote"
    await runtime.stop()


async def test_promotion_of_unknown_skill_fails_loudly(tmp_path) -> None:
    runtime, bus, _store, _skills = _runtime(tmp_path)
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    await runtime.start()

    item = _item(
        kind=ReviewKind.SKILL_PROMOTION.value,
        requested_action="promote",
        title="no-such-skill",
        evidence={"skill_name": "no-such-skill", "to_scope": "environment"},
    )
    item.decide(decision="approved", operator_id="human:jozef")
    await bus.publish(review_decided_event(item, source="ravn:odin-review"))
    await bus.flush()

    resolved = await recorder.of_type(registry.ODIN_REVIEW_RESOLVED)
    assert len(resolved) == 1
    assert resolved[0].payload["apply_outcome"] == "apply_failed"
    await runtime.stop()


async def test_restarted_resident_does_not_rebuild_a_pending_item(tmp_path) -> None:
    """A fresh process must not burn another build on an item awaiting review."""
    runtime, bus, store, skills = _runtime(tmp_path)
    await runtime.start()
    held = await runtime.process_signal(OOM_SIGNAL)
    assert held["decision"] == "capability_build_held"
    await runtime.stop()

    class _CountingBuilder:
        def __init__(self) -> None:
            self.calls = 0

        async def build(self, request):
            self.calls += 1
            raise AssertionError("must not rebuild while the item is pending")

    builder = _CountingBuilder()
    from ravn.odin.review import JsonReviewStore as _Store
    from ravn.odin.review import ReviewRequester as _Requester

    restarted = ResidentLearningRuntime(
        identity=runtime.identity,
        skills=skills,
        publisher=bus,
        subscriber=bus,
        builder=builder,
        tools_dir=tmp_path / "tools",
        review_requester=_Requester(
            publisher=bus,
            store=_Store(tmp_path / "review_outbox.json"),
            source="valkyrie:k8s-a",
        ),
    )
    await restarted.start()
    result = await restarted.process_signal(OOM_SIGNAL)
    assert builder.calls == 0
    assert result is None or result.get("decision") != "capability_build_held"
    await restarted.stop()
