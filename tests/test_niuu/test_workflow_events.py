"""The ``workflow`` mesh event: every known kind's shape, and the open set of kinds."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from niuu.adapters.mesh._serialization import dict_to_event, event_to_dict
from niuu.domain.workflow_step import (
    KIND_GATE_DECIDED,
    KIND_GATE_OPENED,
    KIND_RUN_ENDED,
    KIND_STEP_OFFERED,
    KIND_STEP_OUTCOME,
    KIND_STEP_PASSED,
    KIND_STEP_PICKED_UP,
    WORKFLOW_EVENT_KINDS,
    GateDecided,
    GateOpened,
    PersonaContract,
    PersonaPin,
    RunEnded,
    StepContract,
    StepOffered,
    StepOutcome,
    StepPassed,
    StepPickedUp,
    UnknownWorkflowEvent,
    WorkflowEventError,
    parse_workflow_event,
    workflow_event_id,
)
from niuu.mesh.events import MeshEvent, MeshEventType

RUN = "5f0f9a52-33e0-4f79-9d6e-3e7a2f6fd0a1"
OFFER = "offer-1"
ACTIVATION = "0b6ac9e6-0a5e-4b49-9b0e-7a3f4d6f4a10"

CODER_PIN = PersonaPin(alias="coder", id="coder", revision="3", digest="sha256:c0de")
REVIEWER_PIN = PersonaPin(alias="reviewer", id="reviewer", revision="2", digest="sha256:b00b")


def _offer(**overrides: Any) -> StepOffered:
    values: dict[str, Any] = {
        "run_id": RUN,
        "offer_id": OFFER,
        "node_id": "implement",
        "node_label": "Implement",
        "workflow_name": "Build",
        "topic": "code.requested",
        "inputs": [
            {
                "seq": 1,
                "event_type": "feature.requested",
                "edge_id": "e1",
                "node_id": "trigger",
                "payload": {"note": "add the endpoint"},
            }
        ],
        "deadline_at": "2026-11-08T12:00:00+00:00",
        "instructions": "# Workflow step: Implement\n\nDo the work.",
        "contracts": [
            StepContract.from_persona(
                CODER_PIN,
                PersonaContract(
                    produces_map={"pass": "code.changed", "blocked": "code.blocked"},
                    produces_event="code.completed",
                    outcome_schema={"summary": {"type": "string"}},
                ),
            )
        ],
    }
    values.update(overrides)
    return StepOffered(**values)


def _bodies() -> list[Any]:
    """One valid event of every known kind."""
    return [
        _offer(),
        StepPickedUp(
            run_id=RUN,
            offer_id=OFFER,
            node_id="implement",
            member="coder",
            session_id="ses-coder",
            activation_id=ACTIVATION,
        ),
        StepPassed(
            run_id=RUN,
            offer_id=OFFER,
            node_id="implement",
            member="reviewer",
            session_id="ses-reviewer",
            reason="eventFilters: repo=api does not match repo=web",
        ),
        StepOutcome(
            run_id=RUN,
            offer_id=OFFER,
            node_id="implement",
            member="coder",
            session_id="ses-coder",
            activation_id=ACTIVATION,
            ending="verdict",
            verdict="pass",
            outcome={"summary": "added the endpoint"},
            note="tests pass",
            evidence=[{"kind": "commit", "ref": "abc123", "summary": "the change"}],
            produced_event="code.changed",
        ),
        GateOpened(
            run_id=RUN,
            gate_id="gate-1",
            node_id="approve",
            node_label="Approve",
            mode="human_approval",
            summary="review the change",
        ),
        GateDecided(
            run_id=RUN,
            gate_id="gate-1",
            node_id="approve",
            decision="approved",
            decided_by="jozef",
        ),
        RunEnded(run_id=RUN, status="completed", reason="every node finished"),
    ]


class TestKinds:
    def test_the_known_kinds_are_the_design_s_seven(self) -> None:
        assert set(WORKFLOW_EVENT_KINDS) == {
            "step.offered",
            "step.picked_up",
            "step.passed",
            "step.outcome",
            "gate.opened",
            "gate.decided",
            "run.ended",
        }
        assert KIND_STEP_OFFERED == "step.offered"
        assert KIND_STEP_PICKED_UP == "step.picked_up"
        assert KIND_STEP_PASSED == "step.passed"
        assert KIND_STEP_OUTCOME == "step.outcome"
        assert KIND_GATE_OPENED == "gate.opened"
        assert KIND_GATE_DECIDED == "gate.decided"
        assert KIND_RUN_ENDED == "run.ended"

    def test_the_mesh_event_type_is_workflow(self) -> None:
        assert MeshEventType.WORKFLOW == "workflow"
        assert MeshEventType("workflow") is MeshEventType.WORKFLOW

    @pytest.mark.parametrize("body", _bodies(), ids=lambda body: body.KIND)
    def test_every_kind_round_trips_through_its_payload(self, body: Any) -> None:
        payload = body.to_payload()

        assert payload["kind"] == body.KIND
        assert payload["run_id"] == RUN
        assert json.loads(json.dumps(payload)) == payload
        assert parse_workflow_event(payload) == body

    @pytest.mark.parametrize("body", _bodies(), ids=lambda body: body.KIND)
    def test_every_kind_is_one_mesh_event_on_the_run_s_thread(self, body: Any) -> None:
        event = MeshEvent.workflow("ses-root", body, session_id="ses-root")

        assert event.type is MeshEventType.WORKFLOW
        assert event.correlation_id == RUN
        assert event.root_correlation_id == RUN
        assert event.payload == body.to_payload()
        assert event.event_id == body.event_id
        assert event.urgency == body.URGENCY
        assert event.as_workflow() == body

    @pytest.mark.parametrize("body", _bodies(), ids=lambda body: body.KIND)
    def test_every_kind_survives_the_mesh_wire(self, body: Any) -> None:
        event = MeshEvent.workflow("ses-root", body, session_id="ses-root")

        assert dict_to_event(json.loads(json.dumps(event_to_dict(event)))).as_workflow() == body


class TestOffer:
    def test_the_offer_carries_what_a_session_is_told_today(self) -> None:
        offer = _offer()
        coder = offer.contract_for("coder")

        assert offer.topic == "code.requested"
        assert offer.publish_topic == "code.requested"
        assert offer.attempt == 1
        assert offer.generation == 0
        assert offer.inputs[0].event_type == "feature.requested"
        assert offer.deadline_at is not None
        assert offer.instructions.startswith("# Workflow step")
        assert coder is not None
        assert coder.persona == CODER_PIN
        assert coder.valid_endings == ["verdict", "waiting", "done", "failed"]
        assert coder.valid_verdicts == ["blocked", "pass"]
        assert coder.verdict_events == {"pass": "code.changed", "blocked": "code.blocked"}
        assert coder.default_event == "code.completed"
        assert coder.outcome_schema == {"summary": {"type": "string"}}
        assert offer.contract_for("reviewer") is None

    def test_a_node_with_two_personas_carries_each_one_s_contract(self) -> None:
        reviewer = StepContract.from_persona(
            REVIEWER_PIN, PersonaContract(produces_map={"pass": "review.passed"})
        )
        offer = _offer(contracts=[*_offer().contracts, reviewer])

        assert offer.contract_for("reviewer") == reviewer
        assert offer.contract_for("coder") is not None

    def test_the_offer_payload_names_the_topic_not_a_derived_field(self) -> None:
        payload = _offer().to_payload()

        assert payload["topic"] == "code.requested"
        assert "publish_topic" not in payload

    def test_the_event_id_derives_from_the_offer_so_a_republish_is_the_same_event(self) -> None:
        first = _offer()
        again = _offer(instructions="changed text")

        assert first.event_id == again.event_id == workflow_event_id("step.offered", OFFER)
        assert _offer(offer_id="offer-2").event_id != first.event_id

    @pytest.mark.parametrize(
        "overrides",
        [
            {"offer_id": ""},
            {"node_id": ""},
            {"topic": ""},
            {"instructions": ""},
            {"contracts": []},
            {"attempt": 0},
            {"generation": -1},
        ],
    )
    def test_an_offer_missing_what_it_needs_is_refused(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            _offer(**overrides)

    def test_two_contracts_for_one_persona_alias_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="repeat a persona alias"):
            _offer(contracts=[*_offer().contracts, *_offer().contracts])


class TestContract:
    def _derived(self, **overrides: Any) -> StepContract:
        values: dict[str, Any] = {
            "persona": CODER_PIN,
            "valid_verdicts": ["blocked", "pass"],
            "verdict_events": {"pass": "code.changed", "blocked": "code.blocked"},
            "default_event": "code.completed",
            "outcome_schema": {"summary": {"type": "string"}},
        }
        values.update(overrides)
        return StepContract(**values)

    def test_agreeing_contracts_do_not_mismatch(self) -> None:
        assert self._derived().mismatch(self._derived(valid_verdicts=["pass", "blocked"])) is None

    @pytest.mark.parametrize(
        ("overrides", "needle"),
        [
            ({"persona": REVIEWER_PIN}, "offer pins coder"),
            ({"valid_endings": ["verdict"]}, "valid_endings"),
            ({"valid_verdicts": ["pass"]}, "valid_verdicts"),
            ({"verdict_events": {"pass": "code.other"}}, "verdict_events"),
            ({"default_event": "code.other"}, "default_event"),
            ({"outcome_schema": None}, "outcome_schema"),
            (
                {"persona": CODER_PIN.model_copy(update={"revision": "4"})},
                "revision 3",
            ),
        ],
    )
    def test_a_differing_contract_is_named_on_both_sides(
        self, overrides: dict[str, Any], needle: str
    ) -> None:
        reason = self._derived().mismatch(self._derived(**overrides))

        assert reason is not None
        assert needle in reason
        assert "offer" in reason
        assert "persona derives" in reason or "this member runs" in reason

    @pytest.mark.parametrize(
        "overrides",
        [
            {"valid_endings": []},
            {"valid_endings": ["done", "done"]},
            {"valid_verdicts": ["pass", "pass"]},
            {"valid_verdicts": [" "]},
        ],
    )
    def test_an_incoherent_contract_is_refused(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            self._derived(**overrides)

    def test_a_persona_pin_needs_every_part(self) -> None:
        with pytest.raises(ValidationError):
            PersonaPin(alias="coder", id="coder", revision="", digest="sha256:c0de")


class TestPickupAndPass:
    def test_a_pickup_is_fenced_on_the_offer_and_the_session(self) -> None:
        body = _bodies()[1]

        assert body.dedup_key() == f"{OFFER}:ses-coder"
        assert body.publish_topic is None
        assert body.attempt == 1

    def test_two_sessions_picking_up_one_offer_are_two_events(self) -> None:
        a = _bodies()[1]
        b = a.model_copy(update={"session_id": "ses-other"})

        assert a.event_id != b.event_id

    def test_a_pass_says_which_filter_did_not_match(self) -> None:
        body = _bodies()[2]

        assert body.reason.startswith("eventFilters")
        assert body.dedup_key() == f"{OFFER}:ses-reviewer"
        assert body.event_id != _bodies()[1].event_id

    def test_a_pass_and_a_pickup_of_one_session_are_different_events(self) -> None:
        picked = _bodies()[1].model_copy(update={"session_id": "ses-x"})
        passed = _bodies()[2].model_copy(update={"session_id": "ses-x"})

        assert picked.event_id != passed.event_id


class TestOutcome:
    def _outcome(self, **overrides: Any) -> StepOutcome:
        values: dict[str, Any] = {
            "run_id": RUN,
            "offer_id": OFFER,
            "node_id": "review",
            "member": "reviewer",
            "session_id": "ses-reviewer",
            "activation_id": ACTIVATION,
            "ending": "done",
        }
        values.update(overrides)
        return StepOutcome(**values)

    def test_an_outcome_also_goes_on_the_topic_it_produces(self) -> None:
        assert _bodies()[3].publish_topic == "code.changed"
        assert self._outcome().publish_topic is None

    def test_every_ending_has_a_shape(self) -> None:
        assert self._outcome(ending="done").ending == "done"
        assert self._outcome(ending="verdict", verdict="pass").verdict == "pass"
        waiting = self._outcome(
            ending="waiting", waiting_on={"kind": "person", "ref": "jozef", "reason": "asks"}
        )
        assert waiting.waiting_on is not None
        assert waiting.waiting_on.kind == "person"
        failed = self._outcome(ending="failed", failure_reason="the build is broken")
        assert failed.failure_reason == "the build is broken"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"ending": "verdict"},
            {"ending": "verdict", "verdict": "  "},
            {"ending": "waiting"},
            {"ending": "failed"},
            {"ending": "failed", "failure_reason": " "},
            {"ending": "finished"},
            {"waiting_on": {"kind": "weather", "ref": "x"}},
        ],
    )
    def test_an_ending_missing_what_it_needs_is_refused(self, overrides: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            self._outcome(**overrides)

    def test_a_waiting_step_closed_again_is_a_new_event(self) -> None:
        waiting = self._outcome(ending="waiting", waiting_on={"kind": "person", "ref": "jozef"})
        done = self._outcome(ending="done")
        verdict_a = self._outcome(ending="verdict", verdict="pass")
        verdict_b = self._outcome(ending="verdict", verdict="fail")

        assert len({waiting.event_id, done.event_id, verdict_a.event_id, verdict_b.event_id}) == 4

    def test_the_same_close_republished_is_the_same_event(self) -> None:
        assert self._outcome(note="a").event_id == self._outcome(note="b").event_id


class TestGatesAndEnd:
    def test_gate_events_share_the_gate_id_but_not_the_kind(self) -> None:
        opened, decided = _bodies()[4], _bodies()[5]

        assert opened.dedup_key() == decided.dedup_key() == "gate-1"
        assert opened.event_id != decided.event_id

    def test_a_gate_decision_is_approved_or_changes_requested(self) -> None:
        with pytest.raises(ValidationError):
            GateDecided(run_id=RUN, gate_id="g", node_id="n", decision="maybe", decided_by="x")
        with pytest.raises(ValidationError):
            GateDecided(run_id=RUN, gate_id="g", node_id="n", decision="approved", decided_by="")

    def test_a_gate_waiting_on_a_person_is_louder_than_a_pickup(self) -> None:
        assert _bodies()[4].URGENCY > _bodies()[1].URGENCY

    @pytest.mark.parametrize("status", ["completed", "failed", "canceled"])
    def test_a_run_ends_completed_failed_or_canceled(self, status: str) -> None:
        assert RunEnded(run_id=RUN, status=status).dedup_key() == RUN

    def test_a_run_ending_any_other_way_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            RunEnded(run_id=RUN, status="paused")


class TestOpenSet:
    def test_an_unknown_kind_is_kept_whole(self) -> None:
        payload = {"kind": "step.heartbeat", "run_id": RUN, "beat": 3, "nested": {"a": [1, 2]}}

        parsed = parse_workflow_event(payload)

        assert isinstance(parsed, UnknownWorkflowEvent)
        assert parsed.kind == "step.heartbeat"
        assert parsed.run_id == RUN
        assert parsed.payload == payload
        assert parsed.to_payload() == payload
        assert parsed.to_payload() is not payload

    def test_an_unknown_kind_is_carried_through_a_mesh_event(self) -> None:
        payload = {"kind": "run.paused", "run_id": RUN, "by": "ops"}
        event = MeshEvent(
            type=MeshEventType.WORKFLOW,
            source="ses-root",
            payload=payload,
            timestamp=MeshEvent.workflow("s", _bodies()[6], "s").timestamp,
            urgency=0.3,
            correlation_id=RUN,
            session_id="ses-root",
        )

        carried = dict_to_event(json.loads(json.dumps(event_to_dict(event))))
        parsed = carried.as_workflow()

        assert carried.payload == payload
        assert isinstance(parsed, UnknownWorkflowEvent)
        assert parsed.kind == "run.paused"

    def test_an_unknown_kind_without_a_run_id_is_still_kept(self) -> None:
        parsed = parse_workflow_event({"kind": "something.new"})

        assert isinstance(parsed, UnknownWorkflowEvent)
        assert parsed.run_id == ""

    def test_a_known_kind_keeps_the_fields_a_newer_sender_added(self) -> None:
        payload = _bodies()[6].to_payload() | {"billing_ref": "inv-9"}

        parsed = parse_workflow_event(payload)

        assert isinstance(parsed, RunEnded)
        assert parsed.model_extra == {"billing_ref": "inv-9"}
        assert parsed.to_payload()["billing_ref"] == "inv-9"

    def test_a_known_kind_that_does_not_fit_is_refused_loudly(self) -> None:
        payload = _bodies()[6].to_payload() | {"status": "paused"}

        with pytest.raises(WorkflowEventError, match="`run.ended` is malformed"):
            parse_workflow_event(payload)

    @pytest.mark.parametrize("payload", [{}, {"kind": ""}, {"kind": "  "}, {"kind": 3}])
    def test_a_payload_with_no_kind_is_refused(self, payload: dict[str, Any]) -> None:
        with pytest.raises(WorkflowEventError, match="needs a `kind`"):
            parse_workflow_event(payload)

    def test_only_a_workflow_event_has_a_workflow_payload(self) -> None:
        event = MeshEvent.thought("ses", "hm", RUN, "ses")

        with pytest.raises(WorkflowEventError, match="not a workflow event"):
            event.as_workflow()
