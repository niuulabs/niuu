"""A harness session taking a workflow run's steps from the run's mesh.

An offer arrives as a ``workflow`` mesh event. The member checks who published it and who
signed it, its own pin and contract, and its filters; it then picks the step up or passes, holds
the step durably, hands it to the harness at a turn boundary, and publishes the close as
``step.outcome``. Everything it did is also reported to Forge.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.domain.workflow_step import (
    WORKFLOW_STEP_FRAME,
    StepOffered,
    StepPassed,
    WorkflowStepCloseArgs,
    WorkflowStepCloseError,
    parse_workflow_event,
    workflow_event_id,
)
from niuu.mesh.attestation import (
    KIND_SERVICE,
    KIND_SESSION,
    MeshAttestationVerifier,
    MeshSigner,
)
from niuu.mesh.events import MeshEvent
from niuu.mesh.platform_events import sign_workflow_offer
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.forge_mcp.host import BrokerForgeMcpHost
from skuld.mesh_topics import thread_topic
from skuld.workflow_steps import (
    OFFER_IGNORED,
    OFFER_PASSED,
    OFFER_PICKED_UP,
    OFFER_REFUSED,
    OFFER_REPEAT,
    WorkflowStep,
    ending_from_close,
    filter_mismatch,
    step_turn_id,
)
from tests.test_niuu.test_mesh.test_attestation import Authority
from tests.test_skuld.workflow_support import (
    ALIAS,
    OFFER_ID,
    OTHER_OFFER_ID,
    OTHER_REVISION,
    PERSONA,
    ROOT_ID,
    RUN_ID,
    SESSION_ID,
    TOPIC,
    WAITING_ON,
    contract_of,
    offer_body,
    offer_event,
    sender,
    step_broker,
)

THREAD = thread_topic(RUN_ID)

#: The close the harness makes (harness -> workflow_step_close).
CLOSE = {
    "ending": "verdict",
    "verdict": "implemented",
    "outcome": {"summary": "Added the retry", "files_changed": 2},
    "note": "tests pass",
    "evidence": [{"kind": "commit", "ref": "abc123", "summary": "the change"}],
}


def _activation_id(broker: Broker, offer_id: str = OFFER_ID) -> str:
    step = next(s for s in broker._workflow_steps.values() if s.offer_id == offer_id)
    return step.activation_id


def _close_args(broker: Broker, **change) -> WorkflowStepCloseArgs:
    return WorkflowStepCloseArgs(activation_id=_activation_id(broker), **{**CLOSE, **change})


def _step_frames(broker: Broker) -> list[dict]:
    return [
        entry["payload"]["step"]
        for entry in broker._event_log_buffer
        if entry["kind"] == WORKFLOW_STEP_FRAME and "step" in entry["payload"]
    ]


def _passed_frames(broker: Broker) -> list[dict]:
    return [
        entry["payload"]["passed"]
        for entry in broker._event_log_buffer
        if entry["kind"] == WORKFLOW_STEP_FRAME and "passed" in entry["payload"]
    ]


def _reports(broker: Broker, event: str | None = None) -> list[dict]:
    reports = [
        payload["metadata"]["workflow_step"]
        for payload in broker._posted
        if "workflow_step" in payload["metadata"]
    ]
    return [r for r in reports if event is None or r["event"] == event]


def _published(broker: Broker, kind: str | None = None) -> list[tuple[dict, str]]:
    return [
        (event.payload, topic)
        for event, topic in broker._mesh_adapter.published
        if kind is None or event.payload["kind"] == kind
    ]


async def _settle() -> None:
    """Let the background injection and delivery tasks the broker started finish."""
    for _ in range(3):
        pending = [
            t
            for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.get_name().startswith("mesh-cert-renewal")
        ]
        await asyncio.gather(*pending, return_exceptions=True)


async def _take(broker: Broker, payload: dict | None = None, *, verified: str = ROOT_ID) -> str:
    """Deliver an offer from the run's root, as an unauthenticated local mesh does."""
    decision = await broker.accept_workflow_offer(
        offer_event(payload), verified=sender(verified) if verified else None
    )
    await _settle()
    return decision


@pytest.fixture(autouse=True)
def _counted(monkeypatch):
    """The refusals counter, observed."""
    counted = MagicMock()
    monkeypatch.setattr("skuld.workflow_steps.get_observability", lambda: counted)
    return counted


class TestPickingUpAnOffer:
    async def test_an_offer_for_this_member_is_held_reported_and_announced(self, tmp_path) -> None:
        broker = step_broker(tmp_path)

        decision = await _take(broker)

        assert decision == OFFER_PICKED_UP
        step = next(iter(broker._workflow_steps.values()))
        assert step.state == "active"
        assert step.offer_id == OFFER_ID
        assert step.member == "coder"
        assert step.valid_verdicts == ["blocked", "implemented"]
        assert step.verdict_events == {
            "implemented": "code.implemented",
            "blocked": "code.blocked",
        }
        assert _step_frames(broker)[0]["state"] == "active"
        assert _step_frames(broker)[0]["injected"] is False
        [report] = _reports(broker, "picked_up")
        assert report == {
            "event": "picked_up",
            "run_id": RUN_ID,
            "offer_id": OFFER_ID,
            "node_id": "implement",
            "member": "coder",
            "activation_id": step.activation_id,
            "attempt": 1,
        }

    async def test_the_pickup_is_published_on_the_runs_thread(self, tmp_path) -> None:
        broker = step_broker(tmp_path)

        await _take(broker)

        [(payload, topic)] = _published(broker, "step.picked_up")
        body = parse_workflow_event(payload)
        assert topic == THREAD
        assert body.offer_id == OFFER_ID
        assert body.session_id == SESSION_ID
        assert body.activation_id == _activation_id(broker)
        [(event, _)] = broker._mesh_adapter.published
        assert event.correlation_id == RUN_ID
        assert event.source == SESSION_ID
        assert event.event_id == workflow_event_id("step.picked_up", f"{OFFER_ID}:{SESSION_ID}")

    async def test_this_session_mints_the_activation_id(self, tmp_path) -> None:
        first = step_broker(tmp_path)
        await _take(first)
        again = step_broker(tmp_path)
        await _take(again)

        assert _activation_id(first) == _activation_id(again)
        other = step_broker(tmp_path)
        await _take(other, offer_body(offer_id=OTHER_OFFER_ID))
        assert _activation_id(other, OTHER_OFFER_ID) != _activation_id(first)

    async def test_the_harness_is_told_the_task_and_how_to_close_this_activation(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path)

        await _take(broker)

        activation = _activation_id(broker)
        sent = broker._transport.send_message.await_args.args[0]
        assert sent.startswith("# Workflow step: Implement\nYou are the coder. Do the task.")
        assert "## Closing the step" in sent
        assert f'"activation_id": "{activation}"' in sent
        assert '"verdict": "blocked"' in sent  # the first valid verdict, the how-to's example
        [turn] = [t for t in broker._conversation_turns if t.role == "user"]
        assert turn.id == step_turn_id(SESSION_ID, activation)
        assert turn.metadata["workflow_step"] == {
            "activation_id": activation,
            "run_id": RUN_ID,
            "offer_id": OFFER_ID,
            "node_id": "implement",
            "attempt": 1,
        }

    async def test_the_step_is_durable_before_it_is_announced(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        broker._await_event_log_receipt = AsyncMock(return_value=False)

        decision = await _take(broker)

        assert decision == OFFER_REFUSED
        assert broker._workflow_steps == {}
        assert _published(broker) == []
        [refused] = _reports(broker, "offer_refused")
        assert refused["reason"] == "not_durable"
        assert _reports(broker, "picked_up") == []

    async def test_a_topic_this_persona_does_not_consume_is_not_taken(self, tmp_path) -> None:
        broker = step_broker(tmp_path)

        decision = await _take(broker, offer_body(topic="review.requested"))

        assert decision == OFFER_IGNORED
        assert broker._workflow_steps == {}
        assert _reports(broker) == []
        assert _published(broker) == []

    async def test_events_of_other_kinds_are_information_not_work(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        passed = MeshEvent.workflow(
            ROOT_ID,
            StepPassed(
                run_id=RUN_ID,
                offer_id=OFFER_ID,
                node_id="implement",
                member="reviewer",
                session_id="other",
            ),
            "other",
        )

        assert await broker.accept_workflow_offer(passed, verified=sender()) == OFFER_IGNORED
        assert broker._workflow_steps == {}

    async def test_a_session_that_is_not_a_member_takes_nothing(self, tmp_path) -> None:
        broker = Broker(SkuldSettings(session={"id": SESSION_ID, "workspace_dir": str(tmp_path)}))

        decision = await broker.accept_workflow_offer(offer_event(), verified=sender())

        assert decision == OFFER_IGNORED

    async def test_a_broker_that_failed_to_start_refuses_loudly(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        broker._fail_startup(RuntimeError("mesh bind failed"))

        assert await _take(broker) == OFFER_REFUSED

        [refused] = _reports(broker, "offer_refused")
        assert refused["reason"] == "broker_failed"
        assert "mesh bind failed" in refused["detail"]


class TestPassingAnOffer:
    FILTERED = {"workflow_steps": {"event_filters": {"area": "frontend"}}}

    async def test_filters_that_do_not_match_pass_the_offer_naming_the_filter(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path, **self.FILTERED)

        decision = await _take(broker)

        assert decision == OFFER_PASSED
        assert broker._workflow_steps == {}
        [report] = _reports(broker, "passed")
        assert report["offer_id"] == OFFER_ID
        assert report["member"] == "coder"
        assert "area='frontend'" in report["reason"]
        assert "'backend'" in report["reason"]
        [(payload, topic)] = _published(broker, "step.passed")
        assert topic == THREAD
        assert parse_workflow_event(payload).reason == report["reason"]
        assert _reports(broker, "picked_up") == []
        broker._transport.send_message.assert_not_awaited()

    async def test_a_pass_is_durable_so_ting_can_read_it_from_the_log(self, tmp_path) -> None:
        broker = step_broker(tmp_path, **self.FILTERED)

        await _take(broker)

        [passed] = _passed_frames(broker)
        assert passed["offer_id"] == OFFER_ID
        assert passed["member"] == "coder"
        assert "area='frontend'" in passed["reason"]

    async def test_matching_filters_take_the_offer(self, tmp_path) -> None:
        broker = step_broker(tmp_path, workflow_steps={"event_filters": {"area": "backend"}})

        assert await _take(broker) == OFFER_PICKED_UP

    async def test_a_filter_against_a_list_matches_when_it_contains_the_value(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path, workflow_steps={"event_filters": {"area": "backend"}})
        inputs = [
            {
                "seq": 1,
                "event_type": "ticket.created",
                "node_id": "start",
                "payload": {"fields": {"area": ["docs", "backend"]}},
            }
        ]

        assert await _take(broker, offer_body(inputs=inputs)) == OFFER_PICKED_UP

    async def test_one_matching_input_is_enough(self, tmp_path) -> None:
        broker = step_broker(tmp_path, **self.FILTERED)
        inputs = [
            {
                "seq": 1,
                "event_type": "ticket.created",
                "node_id": "start",
                "payload": {"fields": {"area": "backend"}},
            },
            {
                "seq": 2,
                "event_type": "design.ready",
                "node_id": "design",
                "payload": {"outcome": {"area": "frontend"}},
            },
        ]

        assert await _take(broker, offer_body(inputs=inputs)) == OFFER_PICKED_UP

    async def test_no_filters_take_every_offer(self, tmp_path) -> None:
        broker = step_broker(tmp_path)

        assert await _take(broker, offer_body(inputs=[])) == OFFER_PICKED_UP

    def test_a_filter_with_nothing_to_match_says_so(self) -> None:
        assert "no inputs" in filter_mismatch([], {"area": "backend"})
        assert filter_mismatch([], {}) is None


class TestRepeatedOffers:
    async def test_an_offer_heard_twice_is_one_step(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        frames = len(_step_frames(broker))
        sends = broker._transport.send_message.await_count

        decision = await _take(broker)

        assert decision == OFFER_REPEAT
        assert len(broker._workflow_steps) == 1
        assert len(_step_frames(broker)) == frames
        assert broker._transport.send_message.await_count == sends
        assert len(_reports(broker, "picked_up")) == 1

    async def test_a_repeat_publishes_the_same_pickup_event_again(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)

        await _take(broker)

        events = [event for event, _ in broker._mesh_adapter.published]
        assert len(events) == 2
        assert events[0].event_id == events[1].event_id

    async def test_a_repeat_of_a_passed_offer_passes_it_again_without_a_new_record(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path, workflow_steps={"event_filters": {"area": "frontend"}})
        await _take(broker)

        assert await _take(broker) == OFFER_REPEAT

        assert len(_passed_frames(broker)) == 1
        assert len(_reports(broker, "passed")) == 1
        assert len(_published(broker, "step.passed")) == 2

    async def test_a_closed_offer_heard_again_does_not_start_a_second_step(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        await broker.close_workflow_step(_close_args(broker))

        assert await _take(broker) == OFFER_REPEAT

        assert len(broker._workflow_steps) == 1
        assert broker._open_workflow_step() is None

    async def test_one_step_at_a_time_a_second_offer_is_refused_while_one_is_open(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)

        decision = await _take(broker, offer_body(offer_id=OTHER_OFFER_ID))

        assert decision == OFFER_REFUSED
        assert len(broker._workflow_steps) == 1
        [refused] = _reports(broker, "offer_refused")
        assert refused["reason"] == "busy"
        assert refused["offer_id"] == OTHER_OFFER_ID
        assert "runs one step at a time" in refused["detail"]
        # Nothing was recorded for the refused offer, so offering it again after the first
        # step closed takes it.
        await broker.close_workflow_step(_close_args(broker))
        assert await _take(broker, offer_body(offer_id=OTHER_OFFER_ID)) == OFFER_PICKED_UP

    async def test_a_waiting_step_still_counts_as_open(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        await broker.close_workflow_step(
            WorkflowStepCloseArgs(
                activation_id=_activation_id(broker), ending="waiting", waiting_on=WAITING_ON
            )
        )

        assert await _take(broker, offer_body(offer_id=OTHER_OFFER_ID)) == OFFER_REFUSED

        assert "state waiting" in _reports(broker, "offer_refused")[0]["detail"]


class TestRefusedOffers:
    """A refusal is returned, logged at error, counted, reported to Forge, and records nothing."""

    def _assert_refused(self, broker, reason, caplog, _counted, *, offer_id=OFFER_ID) -> dict:
        assert broker._workflow_steps == {}
        assert _step_frames(broker) == []
        assert _passed_frames(broker) == []
        assert _published(broker) == []
        broker._transport.send_message.assert_not_awaited()
        [report] = _reports(broker)
        assert report["event"] == "offer_refused"
        assert report["reason"] == reason
        assert report["offer_id"] == offer_id
        assert report["run_id"] == RUN_ID
        assert f"({reason})" in caplog.text
        _counted.count.assert_called_once()
        assert _counted.count.call_args.kwargs["attributes"]["reason"] == reason
        return report

    async def test_an_offer_for_another_run(self, tmp_path, caplog, _counted) -> None:
        broker = step_broker(tmp_path)
        caplog.set_level(logging.ERROR)

        assert await _take(broker, offer_body(run_id="another-run")) == OFFER_REFUSED

        report = self._assert_refused(broker, "other_run", caplog, _counted)
        assert "belongs to" in report["detail"]

    async def test_a_malformed_offer(self, tmp_path, caplog, _counted) -> None:
        broker = step_broker(tmp_path)
        caplog.set_level(logging.ERROR)
        payload = offer_body()
        del payload["instructions"]

        assert await _take(broker, payload) == OFFER_REFUSED

        report = self._assert_refused(broker, "malformed", caplog, _counted)
        assert "instructions" in report["detail"]

    async def test_a_publisher_that_is_not_the_runs_root_when_the_mesh_is_unauthenticated(
        self, tmp_path, caplog, _counted
    ) -> None:
        broker = step_broker(tmp_path)
        caplog.set_level(logging.ERROR)

        decision = await broker.accept_workflow_offer(
            offer_event(source="skuld:someone-else"), verified=None
        )

        assert decision == OFFER_REFUSED
        report = self._assert_refused(broker, "not_orchestrator", caplog, _counted)
        assert "'someone-else'" in report["detail"]
        assert ROOT_ID in report["detail"]

    async def test_a_publisher_the_mesh_verified_as_another_peer(
        self, tmp_path, caplog, _counted
    ) -> None:
        broker = step_broker(tmp_path, mesh={"attestation": {"required": True}})
        caplog.set_level(logging.ERROR)

        decision = await broker.accept_workflow_offer(
            offer_event(), verified=sender("another-members-peer")
        )

        assert decision == OFFER_REFUSED
        report = self._assert_refused(broker, "not_orchestrator", caplog, _counted)
        assert "another-members-peer" in report["detail"]

    async def test_a_publisher_the_mesh_did_not_verify(self, tmp_path, caplog, _counted) -> None:
        broker = step_broker(tmp_path, mesh={"attestation": {"required": True}})
        caplog.set_level(logging.ERROR)

        assert await _take(broker, verified="") == OFFER_REFUSED

        self._assert_refused(broker, "unverified", caplog, _counted)

    async def test_an_offer_that_names_no_contract_for_this_persona(
        self, tmp_path, caplog, _counted
    ) -> None:
        broker = step_broker(tmp_path)
        caplog.set_level(logging.ERROR)

        decision = await _take(broker, offer_body(contracts=[contract_of(alias="reviewer")]))

        assert decision == OFFER_REFUSED
        report = self._assert_refused(broker, "pin", caplog, _counted)
        assert "[reviewer]" in report["detail"]
        assert "'coder'" in report["detail"]

    async def test_an_offer_that_pins_another_revision_of_the_persona(
        self, tmp_path, caplog, _counted
    ) -> None:
        broker = step_broker(tmp_path)
        caplog.set_level(logging.ERROR)

        decision = await _take(broker, offer_body(contracts=[contract_of(OTHER_REVISION)]))

        assert decision == OFFER_REFUSED
        report = self._assert_refused(broker, "pin", caplog, _counted)
        assert "revision r2" in report["detail"]
        assert "revision r1" in report["detail"]

    async def test_an_offer_whose_contract_differs_from_the_one_the_pin_derives(
        self, tmp_path, caplog, _counted
    ) -> None:
        broker = step_broker(tmp_path)
        caplog.set_level(logging.ERROR)
        wrong = contract_of().model_copy(update={"valid_verdicts": ["shipped"]})

        assert await _take(broker, offer_body(contracts=[wrong])) == OFFER_REFUSED

        report = self._assert_refused(broker, "contract", caplog, _counted)
        assert "valid_verdicts: offer says ['shipped']" in report["detail"]
        assert "persona derives ['blocked', 'implemented']" in report["detail"]

    async def test_an_offer_whose_outcome_schema_differs(self, tmp_path, caplog, _counted) -> None:
        broker = step_broker(tmp_path)
        caplog.set_level(logging.ERROR)
        wrong = contract_of().model_copy(update={"outcome_schema": None})

        assert await _take(broker, offer_body(contracts=[wrong])) == OFFER_REFUSED

        report = self._assert_refused(broker, "contract", caplog, _counted)
        assert "outcome_schema" in report["detail"]


class TestTheEnginesSignature:
    """On a signing mesh an engine run takes only offers its engine (Ting) signed."""

    @pytest.fixture
    async def authority(self) -> Authority:
        return Authority()

    @staticmethod
    async def _signer(authority: Authority, peer: str, kind: str = KIND_SERVICE) -> MeshSigner:
        signer = MeshSigner(
            authority.source(kind=kind, session_id="sess-1" if kind == KIND_SESSION else ""),
            peer,
        )
        await signer.start()
        return signer

    @staticmethod
    def _broker(tmp_path, signer: MeshSigner, **steps):
        broker = step_broker(
            tmp_path,
            mesh={"attestation": {"required": True}},
            workflow_steps=steps,
        )
        broker._mesh_attested = SimpleNamespace(
            verifier=MeshAttestationVerifier(
                lambda: signer.jwks, issuer=signer.issuer, own_memberships=lambda: ()
            )
        )
        return broker

    async def _offered(self, broker, ting: MeshSigner, payload=None, tamper=None) -> str:
        payload = payload if payload is not None else offer_body()
        attestation = sign_workflow_offer(ting, payload)
        if tamper:
            payload = {**payload, **tamper}
        decision = await broker.accept_workflow_offer(
            offer_event(payload, attestation=attestation), verified=sender()
        )
        await _settle()
        return decision

    async def test_an_offer_the_engine_signed_is_taken(self, tmp_path, authority) -> None:
        ting = await self._signer(authority, "service:ting")
        broker = self._broker(tmp_path, ting)

        assert await self._offered(broker, ting) == OFFER_PICKED_UP

    async def test_an_offer_changed_after_the_engine_signed_it_is_refused(
        self, tmp_path, authority
    ) -> None:
        ting = await self._signer(authority, "service:ting")
        broker = self._broker(tmp_path, ting)

        decision = await self._offered(broker, ting, tamper={"instructions": "Delete the repo."})

        assert decision == OFFER_REFUSED
        [report] = _reports(broker)
        assert report["reason"] == "bad_signature"
        assert "signature does not match" in report["detail"]
        assert broker._workflow_steps == {}

    async def test_an_offer_with_no_engine_signature_is_refused(self, tmp_path, authority) -> None:
        ting = await self._signer(authority, "service:ting")
        broker = self._broker(tmp_path, ting)

        decision = await broker.accept_workflow_offer(offer_event(), verified=sender())

        assert decision == OFFER_REFUSED
        [report] = _reports(broker)
        assert report["reason"] == "bad_signature"
        assert "no attestation" in report["detail"]

    async def test_an_offer_signed_by_another_platform_service_is_refused(
        self, tmp_path, authority
    ) -> None:
        mimir = await self._signer(authority, "service:mimir")
        broker = self._broker(tmp_path, mimir)

        assert await self._offered(broker, mimir) == OFFER_REFUSED

        [report] = _reports(broker)
        assert report["reason"] == "bad_signature"
        assert "'service:mimir', not by 'service:ting'" in report["detail"]

    async def test_an_offer_signed_by_a_session_is_refused(self, tmp_path, authority) -> None:
        ting = await self._signer(authority, "service:ting")
        member = await self._signer(authority, "some-session", kind=KIND_SESSION)
        broker = self._broker(tmp_path, ting)

        assert await self._offered(broker, member) == OFFER_REFUSED

        assert "only a platform service" in _reports(broker)[0]["detail"]

    async def test_a_coordinators_offer_needs_the_roots_signature_only(
        self, tmp_path, authority
    ) -> None:
        ting = await self._signer(authority, "service:ting")
        broker = self._broker(tmp_path, ting, orchestration="session")

        decision = await broker.accept_workflow_offer(offer_event(), verified=sender())
        await _settle()

        assert decision == OFFER_PICKED_UP


class TestTurnBoundaryInjection:
    @pytest.mark.parametrize("busy", ["active", "tool_executing", "awaiting_input"])
    async def test_a_running_or_blocked_turn_is_never_interrupted(self, tmp_path, busy) -> None:
        broker = step_broker(tmp_path, activity=busy)

        await _take(broker)

        broker._transport.send_message.assert_not_awaited()
        assert broker._open_workflow_step().injected is False

    async def test_the_instructions_arrive_when_the_turn_ends(self, tmp_path) -> None:
        broker = step_broker(tmp_path, activity="active")
        await _take(broker)
        broker._transport.send_message.assert_not_awaited()

        await broker._report_activity_state("idle")
        await _settle()

        broker._transport.send_message.assert_awaited_once()
        assert "## Closing the step" in broker._transport.send_message.await_args.args[0]

    async def test_the_instructions_are_handed_over_once(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)

        await broker._report_activity_state("active")
        await broker._report_activity_state("idle")
        await _settle()

        broker._transport.send_message.assert_awaited_once()

    async def test_a_not_yet_started_transport_is_started_first(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        broker._transport.is_alive = False

        async def start() -> None:
            broker._transport.is_alive = True

        broker._transport.start = AsyncMock(side_effect=start)

        await _take(broker)

        broker._transport.start.assert_awaited_once()
        broker._transport.send_message.assert_awaited_once()

    async def test_instructions_the_harness_never_accepted_fail_the_step_loudly(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path)
        broker._transport.send_message = AsyncMock(side_effect=RuntimeError("pane is wedged"))
        broker._settings.delivery.max_attempts = 1

        await _take(broker)
        await asyncio.sleep(0.05)

        [step] = broker._workflow_steps.values()
        assert step.state == "closed"
        assert step.ending["kind"] == "failed"
        assert "did not accept the step's instructions" in step.ending["failure_reason"]
        [closed] = _reports(broker, "closed")
        assert closed["ending"]["kind"] == "failed"
        [(outcome, _)] = _published(broker, "step.outcome")[:1]
        assert outcome["ending"] == "failed"

    async def test_a_transport_that_will_not_start_fails_the_step_with_the_reason(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path)
        broker._transport.is_alive = False
        broker._transport.start = AsyncMock(side_effect=RuntimeError("claude binary missing"))

        await _take(broker)

        [step] = broker._workflow_steps.values()
        assert step.state == "closed"
        assert (
            "could not hand the step to the harness: claude binary missing"
            in (step.ending["failure_reason"])
        )
        [closed] = _reports(broker, "closed")
        assert closed["ending"]["kind"] == "failed"
        broker._transport.send_message.assert_not_awaited()

    async def test_a_step_closed_before_the_delivery_verdict_is_left_as_it_closed(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path)
        broker._transport.send_message = AsyncMock(side_effect=RuntimeError("late failure"))
        broker._settings.delivery.max_attempts = 1
        await _take(broker)
        assert len(_reports(broker, "closed")) == 1

        [step] = broker._workflow_steps.values()
        task = asyncio.create_task(asyncio.sleep(0))
        await broker._finish_step_delivery(step, task)

        assert len(_reports(broker, "closed")) == 1


class TestClosingAStep:
    async def test_a_valid_close_is_persisted_reported_published_and_answered(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        activation = _activation_id(broker)

        answer = await broker.close_workflow_step(_close_args(broker))

        assert answer == {
            "activation_id": activation,
            "state": "closed",
            "ending": "verdict",
            "reported": True,
        }
        assert _step_frames(broker)[-1]["state"] == "closed"
        assert _step_frames(broker)[-1]["ending"]["verdict"] == "implemented"
        assert broker._open_workflow_step() is None

    async def test_the_report_has_the_contracts_shape(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)

        await broker.close_workflow_step(_close_args(broker))

        assert _reports(broker, "closed") == [
            {
                "event": "closed",
                "run_id": RUN_ID,
                "offer_id": OFFER_ID,
                "activation_id": _activation_id(broker),
                "attempt": 1,
                "ending": {
                    "kind": "verdict",
                    "verdict": "implemented",
                    "outcome": {"summary": "Added the retry", "files_changed": 2},
                    "note": "tests pass",
                    "evidence": [{"kind": "commit", "ref": "abc123", "summary": "the change"}],
                    "waiting_on": None,
                    "failure_reason": None,
                },
            }
        ]
        payload = broker._posted[-1]
        assert payload["state"] == broker._activity_state
        assert payload["state_since"]

    async def test_the_close_is_published_as_the_outcome_on_the_thread_and_its_event(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        broker._mesh_adapter.published.clear()

        await broker.close_workflow_step(_close_args(broker))

        outcomes = _published(broker, "step.outcome")
        assert [topic for _, topic in outcomes] == ["code.implemented", THREAD]
        payload = outcomes[0][0]
        body = parse_workflow_event(payload)
        assert body.offer_id == OFFER_ID
        assert body.session_id == SESSION_ID
        assert body.member == "coder"
        assert body.activation_id == _activation_id(broker)
        assert body.ending == "verdict"
        assert body.verdict == "implemented"
        assert body.outcome == {"summary": "Added the retry", "files_changed": 2}
        assert body.note == "tests pass"
        assert body.evidence[0].ref == "abc123"
        assert body.produced_event == "code.implemented"
        events = {event.event_id for event, _ in broker._mesh_adapter.published}
        assert len(events) == 1
        [(event, _), _] = broker._mesh_adapter.published
        assert event.correlation_id == RUN_ID

    async def test_a_waiting_close_and_the_final_close_are_two_outcomes(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        activation = _activation_id(broker)
        broker._mesh_adapter.published.clear()

        await broker.close_workflow_step(
            WorkflowStepCloseArgs(activation_id=activation, ending="waiting", waiting_on=WAITING_ON)
        )

        assert broker._workflow_steps[activation].state == "waiting"
        assert broker._open_workflow_step() is not None
        [(waiting, topic)] = _published(broker, "step.outcome")
        assert topic == THREAD  # a waiting close hands nothing off
        assert waiting["ending"] == "waiting"
        assert waiting["waiting_on"] == WAITING_ON
        assert waiting["produced_event"] == ""

        await broker.close_workflow_step(_close_args(broker))

        assert broker._workflow_steps[activation].state == "closed"
        outcomes = _published(broker, "step.outcome")
        ids = {
            workflow_event_id(
                "step.outcome", f"{activation}:{p['ending']}:{p.get('verdict') or ''}"
            )
            for p, _ in outcomes
        }
        assert len(ids) == 2
        assert [r["ending"]["kind"] for r in _reports(broker, "closed")] == ["waiting", "verdict"]

    async def test_a_verdict_the_persona_maps_to_another_event_goes_out_on_that_event(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)

        await broker.close_workflow_step(
            _close_args(broker, verdict="blocked", outcome={"summary": "stuck"})
        )

        outcomes = _published(broker, "step.outcome")
        assert outcomes[0][1] == "code.blocked"
        assert outcomes[0][0]["produced_event"] == "code.blocked"

    async def test_an_outcome_that_cannot_be_published_is_loud_and_does_not_undo_the_close(
        self, tmp_path, caplog
    ) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        broker._mesh_adapter.fail = RuntimeError("nng socket closed")
        caplog.set_level(logging.ERROR)

        answer = await broker.close_workflow_step(_close_args(broker))

        assert answer["state"] == "closed"
        assert len(_reports(broker, "closed")) == 1
        assert "degraded [skuld.workflow.step.outcome]" in caplog.text
        assert "nng socket closed" in caplog.text

    async def test_a_report_never_enters_the_heartbeat_detail(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)

        await broker.close_workflow_step(_close_args(broker))

        assert "workflow_step" not in broker._activity_extra

    @pytest.mark.parametrize(
        "change, message",
        [
            ({"verdict": "shipped"}, "verdict 'shipped' is not valid for this step"),
            ({"verdict": None}, "ending 'verdict' needs `verdict`"),
            ({"outcome": None}, "needs `outcome` (required fields: summary)"),
            ({"outcome": {"files_changed": 2}}, "required field 'summary' is missing"),
            ({"outcome": {"summary": 5}}, "field 'summary': expected string"),
            ({"waiting_on": WAITING_ON}, "`waiting_on` is only for ending 'waiting'"),
            ({"failure_reason": "no"}, "`failure_reason` is only for ending 'failed'"),
        ],
    )
    async def test_a_bad_verdict_close_is_refused_with_the_exact_reason(
        self, tmp_path, change, message
    ) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        frames = len(_step_frames(broker))
        published = len(broker._mesh_adapter.published)

        with pytest.raises(WorkflowStepCloseError, match=re.escape(message)):
            await broker.close_workflow_step(_close_args(broker, **change))

        assert len(_step_frames(broker)) == frames
        assert _reports(broker, "closed") == []
        assert len(broker._mesh_adapter.published) == published
        assert broker._open_workflow_step() is not None

    async def test_a_close_for_another_activation_is_refused(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)

        with pytest.raises(WorkflowStepCloseError, match="is not the open step"):
            await broker.close_workflow_step(
                WorkflowStepCloseArgs(activation_id="not-this-one", **CLOSE)
            )

    async def test_waiting_needs_a_handle_and_failed_needs_a_reason(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        activation = _activation_id(broker)

        with pytest.raises(WorkflowStepCloseError, match="needs `waiting_on`"):
            await broker.close_workflow_step(
                WorkflowStepCloseArgs(activation_id=activation, ending="waiting")
            )
        with pytest.raises(WorkflowStepCloseError, match="needs `failure_reason`"):
            await broker.close_workflow_step(
                WorkflowStepCloseArgs(activation_id=activation, ending="failed")
            )

    async def test_done_and_failed_endings_are_accepted_and_published(self, tmp_path) -> None:
        for ending, extra in (("done", {}), ("failed", {"failure_reason": "tests do not run"})):
            broker = step_broker(tmp_path)
            await _take(broker)

            await broker.close_workflow_step(
                WorkflowStepCloseArgs(activation_id=_activation_id(broker), ending=ending, **extra)
            )

            [report] = _reports(broker, "closed")
            assert report["ending"]["kind"] == ending
            assert report["ending"]["failure_reason"] == extra.get("failure_reason")
            assert _published(broker, "step.outcome")[0][0]["ending"] == ending

    async def test_a_closed_step_cannot_be_closed_again(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        args = _close_args(broker)
        await broker.close_workflow_step(args)
        await _take(broker, offer_body(offer_id=OTHER_OFFER_ID))

        with pytest.raises(WorkflowStepCloseError, match="is not the open step"):
            await broker.close_workflow_step(args)

    async def test_with_no_open_step_the_close_says_so(self, tmp_path) -> None:
        broker = step_broker(tmp_path)

        with pytest.raises(WorkflowStepCloseError, match="no workflow step is open"):
            await broker.close_workflow_step(WorkflowStepCloseArgs(activation_id="x", **CLOSE))

    async def test_a_close_whose_frame_was_not_stored_is_refused_and_leaves_the_step_open(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        broker._await_event_log_receipt = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="was not stored"):
            await broker.close_workflow_step(_close_args(broker))

        [step] = broker._workflow_steps.values()
        assert (step.state, step.ending, step.closed_at) == ("active", None, None)
        assert _reports(broker, "closed") == []
        assert _published(broker, "step.outcome") == []

    def test_the_pure_check_does_not_change_the_step(self) -> None:
        payload = offer_body()
        offer = StepOffered.model_validate({k: v for k, v in payload.items() if k != "kind"})
        step = WorkflowStep.from_offer(offer, contract_of(), session_id=SESSION_ID, member="coder")
        before = copy.deepcopy(step)

        ending = ending_from_close(
            step, WorkflowStepCloseArgs(activation_id=step.activation_id, **CLOSE)
        )

        assert ending["kind"] == "verdict"
        assert step == before


class TestReportsReachTing:
    async def test_an_undelivered_report_is_kept_and_sent_in_order_later(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        delivered: list[dict] = []
        outcomes = iter([False, True, True])

        async def post(url, json=None, **_kwargs):
            ok = next(outcomes)
            if ok:
                delivered.append(json)
            return SimpleNamespace(status_code=204 if ok else 503, url=url)

        broker._http_client = SimpleNamespace(post=post)

        await broker.close_workflow_step(
            WorkflowStepCloseArgs(
                activation_id=_activation_id(broker), ending="waiting", waiting_on=WAITING_ON
            )
        )
        assert len(broker._pending_step_reports) == 1
        assert delivered == []

        await broker._flush_workflow_step_reports()

        assert broker._pending_step_reports == []
        [report] = [p["metadata"]["workflow_step"] for p in delivered]
        assert report["ending"]["kind"] == "waiting"

    async def test_with_activity_reporting_off_the_report_is_held_and_logged(
        self, tmp_path, caplog
    ) -> None:
        broker = step_broker(tmp_path)
        broker._settings.activity_reporting_enabled = False
        await _take(broker)

        await broker.close_workflow_step(_close_args(broker))

        assert len(broker._pending_step_reports) >= 1
        assert "workflow step report cannot be sent" in caplog.text


class TestUnclosedTurns:
    async def test_a_turn_that_ends_with_the_step_open_is_reported_unclosed(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)

        await broker._report_activity_state("active")
        await broker._report_activity_state("idle")

        assert _reports(broker, "unclosed") == [
            {
                "event": "unclosed",
                "run_id": RUN_ID,
                "offer_id": OFFER_ID,
                "activation_id": _activation_id(broker),
                "attempt": 1,
            }
        ]
        assert next(iter(broker._workflow_steps.values())).state == "active"

    async def test_nothing_is_re_prompted_after_an_unclosed_turn(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)

        await broker._report_activity_state("active")
        await broker._report_activity_state("idle")
        await _settle()

        broker._transport.send_message.assert_awaited_once()

    async def test_a_turn_that_closed_the_step_is_not_unclosed(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        await broker._report_activity_state("active")
        await broker.close_workflow_step(_close_args(broker))

        await broker._report_activity_state("idle")

        assert _reports(broker, "unclosed") == []

    async def test_a_turn_that_never_started_is_not_unclosed(self, tmp_path) -> None:
        broker = step_broker(tmp_path, activity="provisioning")
        await _take(broker)

        await broker._report_activity_state("idle")

        assert _reports(broker, "unclosed") == []


class TestListening:
    async def test_a_member_reports_listening_once_its_subscriptions_are_up(self, tmp_path) -> None:
        broker = step_broker(tmp_path)

        await broker.workflow_mesh_ready()

        [report] = _reports(broker, "listening")
        assert report == {
            "event": "listening",
            "run_id": RUN_ID,
            "member": "coder",
            "start_id": broker._workflow_start_id,
            "topics": [TOPIC],
        }

    async def test_each_start_is_its_own_start(self, tmp_path) -> None:
        first = step_broker(tmp_path)
        second = step_broker(tmp_path)

        assert first._workflow_start_id != second._workflow_start_id

    async def test_a_session_that_is_not_a_member_reports_nothing(self, tmp_path) -> None:
        broker = Broker(SkuldSettings(session={"id": SESSION_ID, "workspace_dir": str(tmp_path)}))
        broker._http_client = SimpleNamespace(post=AsyncMock())

        await broker.workflow_mesh_ready()

        broker._http_client.post.assert_not_awaited()


class TestRestart:
    @staticmethod
    def _frames(broker: Broker) -> list:
        """What the durable log holds of the broker, in order, as a restart reads it."""
        return [
            SimpleNamespace(kind=entry["kind"], payload=entry["payload"])
            for entry in broker._event_log_buffer
            if entry["kind"] == WORKFLOW_STEP_FRAME
        ]

    async def test_a_held_step_is_restored_and_handed_over_after_the_restart(
        self, tmp_path
    ) -> None:
        before = step_broker(tmp_path, activity="active")
        await _take(before)
        assert before._transport.send_message.await_count == 0
        after = step_broker(tmp_path)

        after._restore_durable_workflow_steps(self._frames(before))
        await after._maybe_inject_workflow_step()
        await _settle()

        step = after._open_workflow_step()
        assert step.offer_id == OFFER_ID
        assert step.activation_id == _activation_id(before)
        after._transport.send_message.assert_awaited_once()
        assert "## Closing the step" in after._transport.send_message.await_args.args[0]

    async def test_a_step_already_handed_over_is_not_handed_over_again(self, tmp_path) -> None:
        before = step_broker(tmp_path)
        await _take(before)
        after = step_broker(tmp_path)

        after._restore_durable_workflow_steps(self._frames(before))
        await after._maybe_inject_workflow_step()
        await _settle()

        assert after._open_workflow_step().injected is True
        after._transport.send_message.assert_not_awaited()

    async def test_a_restored_offer_heard_again_is_not_a_second_step(self, tmp_path) -> None:
        before = step_broker(tmp_path)
        await _take(before)
        after = step_broker(tmp_path)
        after._restore_durable_workflow_steps(self._frames(before))

        assert await _take(after) == OFFER_REPEAT

        assert len(after._workflow_steps) == 1
        assert _reports(after, "picked_up") == []  # a repeat is not a new pickup report
        assert len(_published(after, "step.picked_up")) == 1

    async def test_a_restored_pass_is_remembered(self, tmp_path) -> None:
        filtered = {"workflow_steps": {"event_filters": {"area": "frontend"}}}
        before = step_broker(tmp_path, **filtered)
        await _take(before)
        after = step_broker(tmp_path, **filtered)

        after._restore_durable_workflow_steps(self._frames(before))

        assert after._offer_decisions == {OFFER_ID: OFFER_PASSED}
        assert await _take(after) == OFFER_REPEAT
        assert len(_passed_frames(after)) == 0

    async def test_a_closed_step_is_restored_closed_with_its_ending(self, tmp_path) -> None:
        before = step_broker(tmp_path)
        await _take(before)
        await before.close_workflow_step(_close_args(before))
        after = step_broker(tmp_path)

        after._restore_durable_workflow_steps(self._frames(before))

        assert after._open_workflow_step() is None
        [step] = after._workflow_steps.values()
        assert step.state == "closed"
        assert step.ending["verdict"] == "implemented"

    async def test_a_restored_waiting_step_accepts_a_later_close(self, tmp_path) -> None:
        before = step_broker(tmp_path)
        await _take(before)
        await before.close_workflow_step(
            WorkflowStepCloseArgs(
                activation_id=_activation_id(before), ending="waiting", waiting_on=WAITING_ON
            )
        )
        after = step_broker(tmp_path)
        after._restore_durable_workflow_steps(self._frames(before))

        await after.close_workflow_step(_close_args(after))

        assert next(iter(after._workflow_steps.values())).state == "closed"
        assert _published(after, "step.outcome")[0][0]["ending"] == "verdict"

    async def test_the_mesh_coming_up_announces_a_held_step_again(self, tmp_path) -> None:
        before = step_broker(tmp_path)
        await _take(before)
        after = step_broker(tmp_path)
        after._restore_durable_workflow_steps(self._frames(before))

        await after.workflow_mesh_ready()

        assert [r["event"] for r in _reports(after)] == ["listening", "picked_up"]
        [(payload, _)] = _published(after, "step.picked_up")
        assert payload["activation_id"] == _activation_id(before)
        # It is the event the first start published, so a receiver treats it as the same.
        first = [e.event_id for e, _ in before._mesh_adapter.published]
        assert [e.event_id for e, _ in after._mesh_adapter.published] == first

    async def test_a_closed_step_is_not_announced_again(self, tmp_path) -> None:
        before = step_broker(tmp_path)
        await _take(before)
        await before.close_workflow_step(_close_args(before))
        after = step_broker(tmp_path)
        after._restore_durable_workflow_steps(self._frames(before))

        await after.workflow_mesh_ready()

        assert [r["event"] for r in _reports(after)] == ["listening"]

    async def test_frames_of_other_kinds_are_ignored(self, tmp_path) -> None:
        broker = step_broker(tmp_path)

        broker._restore_durable_workflow_steps(
            [SimpleNamespace(kind="room_member", payload={"type": "room_member"})]
        )

        assert broker._workflow_steps == {}

    async def test_the_restore_runs_with_the_broker_runtime_state(self, tmp_path) -> None:
        before = step_broker(tmp_path)
        await _take(before)
        after = step_broker(tmp_path)

        after._restore_durable_runtime_state(self._frames(before))

        assert after._open_workflow_step() is not None


class TestForgeMcpHost:
    async def test_the_host_closes_the_open_step_and_refuses_when_none_is_open(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path)
        host = BrokerForgeMcpHost(broker, MagicMock())
        assert host.workflow_step_host() is True
        with pytest.raises(WorkflowStepCloseError, match="no workflow step is open"):
            await host.close_workflow_step(WorkflowStepCloseArgs(activation_id="x", **CLOSE))
        await _take(broker)

        answer = await host.close_workflow_step(_close_args(broker))

        assert answer["state"] == "closed"
        assert broker._open_workflow_step() is None

    async def test_the_tool_is_listed_for_the_whole_session_of_a_step_host(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        toolbox = broker._forge_mcp_toolbox()
        assert "workflow_step_close" in [t["name"] for t in toolbox.list_tools()]
        await _take(broker)
        assert "workflow_step_close" in [t["name"] for t in toolbox.list_tools()]

        await broker.close_workflow_step(_close_args(broker))

        assert "workflow_step_close" in [t["name"] for t in toolbox.list_tools()]

    async def test_a_call_with_no_open_step_is_refused_and_records_nothing(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        toolbox = broker._forge_mcp_toolbox()

        result = await toolbox.call("workflow_step_close", {"activation_id": "x", **CLOSE})

        assert result.is_error
        assert "no workflow step is open on this session" in result.text
        assert _step_frames(broker) == []
        assert _reports(broker, "closed") == []

    async def test_a_call_for_another_activation_is_refused_and_records_nothing(
        self, tmp_path
    ) -> None:
        broker = step_broker(tmp_path)
        await _take(broker)
        frames = len(_step_frames(broker))
        toolbox = broker._forge_mcp_toolbox()

        result = await toolbox.call("workflow_step_close", {"activation_id": "other", **CLOSE})

        assert result.is_error
        assert "activation other is not the open step" in result.text
        assert len(_step_frames(broker)) == frames
        assert _reports(broker, "closed") == []

    async def test_a_bad_close_is_refused_and_a_good_one_goes_through(self, tmp_path) -> None:
        broker = step_broker(tmp_path)
        toolbox = broker._forge_mcp_toolbox()
        await _take(broker)
        arguments = {"activation_id": _activation_id(broker), **CLOSE}

        result = await toolbox.call("workflow_step_close", {**arguments, "verdict": "shipped"})

        assert result.is_error
        assert "verdict 'shipped' is not valid for this step" in result.text

        result = await toolbox.call("workflow_step_close", arguments)

        assert not result.is_error
        assert json.loads(result.text)["state"] == "closed"

    async def test_a_session_that_is_not_a_step_host_never_lists_the_tool(self, tmp_path) -> None:
        broker = Broker(SkuldSettings(session={"id": SESSION_ID, "workspace_dir": str(tmp_path)}))
        toolbox = broker._forge_mcp_toolbox()

        assert "workflow_step_close" not in [t["name"] for t in toolbox.list_tools()]
        result = await toolbox.call("workflow_step_close", {"activation_id": "x", **CLOSE})
        assert result.is_error


class TestStartupConfiguration:
    def test_a_member_without_the_durable_log_is_fatal(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="needs the durable event log"):
            Broker(
                SkuldSettings(
                    session={"id": SESSION_ID, "workspace_dir": str(tmp_path), "persona": PERSONA},
                    mesh={"enabled": True},
                    workflow_steps={
                        "enabled": True,
                        "run_id": RUN_ID,
                        "member": "coder",
                        "persona_alias": ALIAS,
                        "orchestrator_peer_id": ROOT_ID,
                    },
                )
            )

    def test_a_member_without_a_mesh_is_fatal(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="needs the run's mesh"):
            Broker(
                SkuldSettings(
                    session={"id": SESSION_ID, "workspace_dir": str(tmp_path), "persona": PERSONA},
                    volundr_api_url="http://forge.test",
                    workflow_steps={
                        "enabled": True,
                        "run_id": RUN_ID,
                        "member": "coder",
                        "persona_alias": ALIAS,
                        "orchestrator_peer_id": ROOT_ID,
                    },
                )
            )

    def test_a_member_without_a_pinned_persona_is_fatal(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="needs the member's pinned persona"):
            Broker(
                SkuldSettings(
                    session={"id": SESSION_ID, "workspace_dir": str(tmp_path)},
                    volundr_api_url="http://forge.test",
                    mesh={"enabled": True},
                    workflow_steps={
                        "enabled": True,
                        "run_id": RUN_ID,
                        "member": "coder",
                        "persona_alias": ALIAS,
                        "orchestrator_peer_id": ROOT_ID,
                    },
                )
            )

    @pytest.mark.parametrize(
        "steps, message",
        [
            ({"enabled": True}, "workflow_steps.enabled needs workflow_steps.run_id"),
            (
                {"enabled": True, "run_id": "r"},
                "workflow_steps.member, workflow_steps.persona_alias, "
                "workflow_steps.orchestrator_peer_id",
            ),
            ({"root": True}, "workflow_steps.root needs workflow_steps.run_id"),
            ({"root": True, "enabled": True, "run_id": "r"}, "are exclusive"),
        ],
    )
    def test_an_incomplete_role_is_refused_naming_what_is_missing(self, steps, message) -> None:
        with pytest.raises(ValueError, match=re.escape(message)):
            SkuldSettings(workflow_steps=steps)

    def test_a_session_with_neither_role_needs_nothing(self) -> None:
        assert SkuldSettings().workflow_steps.enabled is False
