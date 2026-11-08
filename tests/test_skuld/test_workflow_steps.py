"""A harness session hosting a workflow run's steps (NIU-1198).

The request, ending and report shapes below are the JSON the contract between Ting and
Forge/Skuld spells out, used verbatim so that drift on either side shows up here.
"""

from __future__ import annotations

import asyncio
import copy
import importlib
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from niuu.domain.workflow_step import (
    WORKFLOW_STEP_FRAME,
    WorkflowStepCloseArgs,
    WorkflowStepCloseError,
)
from niuu.ports.cli.transport import TransportCapabilities
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.forge_mcp.host import BrokerForgeMcpHost
from skuld.workflow_steps import (
    WorkflowStep,
    WorkflowStepConflictError,
    WorkflowStepNotHostedError,
    WorkflowStepRequest,
    WorkflowStepUnavailableError,
    ending_from_close,
    step_turn_id,
)

SESSION_ID = "6f1f0b52-7f4e-4a53-9b0b-3f4a2f2b9a11"
ACTIVATION_ID = "3f2b8c1e-5a47-4d0e-9b61-7c1e2a9d4f10"
OTHER_ACTIVATION_ID = "9c4d2e7a-1b36-4f58-8a02-5d6e7f8091a2"
RUN_ID = "0b7e6d5c-4a39-4281-9f10-e1d2c3b4a596"

#: The contract's activation body (Ting -> Forge -> Skuld).
ACTIVATION = {
    "activation_id": ACTIVATION_ID,
    "run_id": RUN_ID,
    "attempt": 1,
    "generation": 0,
    "node_id": "implement",
    "node_label": "Implement",
    "member": "coder",
    "valid_endings": ["verdict", "waiting", "done", "failed"],
    "valid_verdicts": ["implemented", "blocked"],
    "outcome_schema": {
        "summary": {"type": "string", "description": "what changed"},
        "files_changed": {"type": "number", "description": "files touched", "required": False},
    },
    "deadline_at": "2026-11-08T12:00:00+00:00",
    "instructions": "# Workflow step: Implement\nYou are the coder. Close with the close tool.",
}

#: The contract's close arguments (harness -> workflow_step_close).
CLOSE = {
    "activation_id": ACTIVATION_ID,
    "ending": "verdict",
    "verdict": "implemented",
    "outcome": {"summary": "Added the retry", "files_changed": 2},
    "note": "tests pass",
    "evidence": [{"kind": "commit", "ref": "abc123", "summary": "the change"}],
}

WAITING_ON = {"kind": "person", "ref": "ada", "reason": "needs a product decision"}


def _step_broker(tmp_path, *, activity: str = "idle", **settings) -> Broker:
    broker = Broker(
        SkuldSettings(
            session={"id": SESSION_ID, "workspace_dir": str(tmp_path)},
            volundr_api_url="http://forge.test",
            workflow_steps={"enabled": True},
            transport="subprocess",
            chronicle_watcher_enabled=False,
            **settings,
        )
    )
    transport = MagicMock()
    transport.is_alive = True
    transport.capabilities = TransportCapabilities()
    transport.send_message = AsyncMock(return_value=True)
    broker._transport = transport
    broker._activity_state = activity
    broker._complete_trace_span = AsyncMock()
    broker._start_trace_span = AsyncMock(return_value=None)
    broker._await_event_log_receipt = AsyncMock(return_value=True)
    broker._apply_retrieval_reflex = AsyncMock(side_effect=lambda message: message)
    posted: list[dict] = []

    async def post(url, json=None, **_kwargs):
        posted.append(json)
        return SimpleNamespace(status_code=204, url=url)

    broker._http_client = SimpleNamespace(post=post)
    broker._http_client_jwt = None
    broker._posted = posted
    return broker


def _step_frames(broker: Broker) -> list[dict]:
    return [
        entry["payload"]
        for entry in broker._event_log_buffer
        if entry["kind"] == WORKFLOW_STEP_FRAME
    ]


def _step_reports(broker: Broker) -> list[dict]:
    return [
        payload["metadata"]["workflow_step"]
        for payload in broker._posted
        if "workflow_step" in payload["metadata"]
    ]


async def _settle() -> None:
    """Let the background injection and delivery tasks the broker started finish."""
    for _ in range(3):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*pending, return_exceptions=True)


async def _accept(broker: Broker, body: dict | None = None) -> tuple[bool, WorkflowStep]:
    result = await broker.accept_workflow_step(WorkflowStepRequest(**(body or ACTIVATION)))
    await _settle()
    return result


class TestStepRequest:
    def test_the_contract_body_is_accepted_verbatim(self) -> None:
        request = WorkflowStepRequest(**ACTIVATION)
        assert request.activation_id == ACTIVATION_ID
        assert request.deadline_at.isoformat() == ACTIVATION["deadline_at"]

    def test_nullable_schema_and_deadline_are_accepted(self) -> None:
        request = WorkflowStepRequest(**{**ACTIVATION, "outcome_schema": None, "deadline_at": None})
        assert request.outcome_schema is None
        assert request.deadline_at is None

    @pytest.mark.parametrize(
        "change, message",
        [
            ({"activation_id": "not-a-uuid"}, "must be a UUID"),
            ({"attempt": 0}, "greater than or equal to 1"),
            ({"valid_endings": []}, "at least 1 item"),
            ({"valid_endings": ["verdict", "verdict"]}, "must not repeat an ending"),
            ({"valid_verdicts": ["a", "a"]}, "must not repeat a verdict"),
            ({"outcome_schema": {"summary": "text"}}, "field 'summary' must be a mapping"),
            ({"outcome_schema": {"summary": {"type": "date"}}}, "unknown type 'date'"),
            ({"outcome_schema": {"kind": {"type": "enum"}}}, "needs a non-empty values list"),
            ({"instructions": ""}, "at least 1 character"),
            ({"unexpected": 1}, "Extra inputs are not permitted"),
        ],
    )
    def test_an_incoherent_body_is_refused_with_its_reason(self, change, message) -> None:
        with pytest.raises(ValidationError, match=message):
            WorkflowStepRequest(**{**ACTIVATION, **change})


class TestAcceptingAStep:
    async def test_a_new_step_is_persisted_then_handed_to_the_harness(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)

        created, step = await _accept(broker)

        assert created is True
        assert step.status() == {
            "activation_id": ACTIVATION_ID,
            "run_id": RUN_ID,
            "attempt": 1,
            "generation": 0,
            "node_id": "implement",
            "member": "coder",
            "state": "active",
            "ending": None,
            "deadline_at": "2026-11-08T12:00:00+00:00",
            "opened_at": step.opened_at,
            "closed_at": None,
        }
        frames = _step_frames(broker)
        assert frames[0]["type"] == "workflow_step"
        assert frames[0]["step"]["state"] == "active"
        assert frames[0]["step"]["injected"] is False
        assert frames[0]["step"]["instructions"] == ACTIVATION["instructions"]
        assert frames[-1]["step"]["injected"] is True
        sent = broker._transport.send_message.await_args
        assert sent.args[0] == ACTIVATION["instructions"]

    async def test_the_instructions_turn_is_recorded_like_a_user_message(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)

        await _accept(broker)

        [turn] = [t for t in broker._conversation_turns if t.role == "user"]
        assert turn.id == step_turn_id(SESSION_ID, ACTIVATION_ID)
        assert turn.content == ACTIVATION["instructions"]
        assert turn.metadata["workflow_step"] == {
            "activation_id": ACTIVATION_ID,
            "run_id": RUN_ID,
            "node_id": "implement",
            "attempt": 1,
        }

    async def test_redelivering_the_same_activation_changes_nothing(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)
        frames = len(_step_frames(broker))
        sends = broker._transport.send_message.await_count

        created, step = await _accept(broker)

        assert created is False
        assert step.activation_id == ACTIVATION_ID
        assert len(_step_frames(broker)) == frames
        assert broker._transport.send_message.await_count == sends

    async def test_a_different_step_is_refused_while_one_is_open(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)

        with pytest.raises(WorkflowStepConflictError, match="already has an open step"):
            await _accept(broker, {**ACTIVATION, "activation_id": OTHER_ACTIVATION_ID})

    async def test_a_waiting_step_still_counts_as_open(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)
        await broker.close_workflow_step(
            WorkflowStepCloseArgs(
                activation_id=ACTIVATION_ID, ending="waiting", waiting_on=WAITING_ON
            )
        )

        with pytest.raises(WorkflowStepConflictError, match="state waiting"):
            await _accept(broker, {**ACTIVATION, "activation_id": OTHER_ACTIVATION_ID})

    async def test_the_same_activation_with_another_attempt_is_a_conflict(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)

        with pytest.raises(WorkflowStepConflictError, match="with attempt 1"):
            await _accept(broker, {**ACTIVATION, "attempt": 2})

    async def test_a_closed_step_makes_room_for_the_next(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)
        await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        created, step = await _accept(
            broker, {**ACTIVATION, "activation_id": OTHER_ACTIVATION_ID, "attempt": 1}
        )

        assert created is True
        assert step.activation_id == OTHER_ACTIVATION_ID

    async def test_a_session_that_is_not_a_step_host_refuses(self, tmp_path) -> None:
        broker = Broker(SkuldSettings(session={"id": SESSION_ID, "workspace_dir": str(tmp_path)}))

        with pytest.raises(WorkflowStepNotHostedError, match="not a workflow step host"):
            await broker.accept_workflow_step(WorkflowStepRequest(**ACTIVATION))
        with pytest.raises(WorkflowStepNotHostedError):
            broker.get_workflow_step(ACTIVATION_ID)
        assert (broker._open_workflow_step() is not None) is False

    async def test_a_broker_that_failed_to_start_cannot_take_a_step(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        broker._fail_startup(RuntimeError("mesh bind failed"))

        with pytest.raises(WorkflowStepUnavailableError, match="mesh bind failed"):
            await _accept(broker)

    async def test_a_frame_forge_never_stored_fails_the_accept(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        broker._await_event_log_receipt = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="was not stored"):
            await broker.accept_workflow_step(WorkflowStepRequest(**ACTIVATION))

    def test_enabling_without_the_durable_log_is_fatal(self, tmp_path) -> None:
        settings = SkuldSettings(
            session={"id": SESSION_ID, "workspace_dir": str(tmp_path)},
            workflow_steps={"enabled": True},
        )
        with pytest.raises(ValueError, match="needs the durable event log"):
            Broker(settings)


class TestStepApiRoutes:
    @pytest.fixture
    def api(self, monkeypatch):
        module = importlib.import_module("skuld.broker_api")
        return module

    async def test_post_answers_202_then_200_then_409(self, tmp_path, api, monkeypatch) -> None:
        broker = _step_broker(tmp_path)
        monkeypatch.setattr(api, "broker", broker)

        response = Response()
        body = await api.activate_workflow_step(WorkflowStepRequest(**ACTIVATION), response)
        assert response.status_code == 202
        assert body["state"] == "active"
        await _settle()

        response = Response()
        await api.activate_workflow_step(WorkflowStepRequest(**ACTIVATION), response)
        assert response.status_code == 200

        with pytest.raises(HTTPException) as refused:
            await api.activate_workflow_step(
                WorkflowStepRequest(**{**ACTIVATION, "activation_id": OTHER_ACTIVATION_ID}),
                Response(),
            )
        assert refused.value.status_code == 409

    async def test_post_to_a_session_that_is_not_a_step_host_is_404(
        self, tmp_path, api, monkeypatch
    ) -> None:
        plain = Broker(SkuldSettings(session={"id": SESSION_ID, "workspace_dir": str(tmp_path)}))
        monkeypatch.setattr(api, "broker", plain)

        with pytest.raises(HTTPException) as refused:
            await api.activate_workflow_step(WorkflowStepRequest(**ACTIVATION), Response())
        assert refused.value.status_code == 404

    async def test_post_to_a_broker_that_failed_to_start_is_503(
        self, tmp_path, api, monkeypatch
    ) -> None:
        broker = _step_broker(tmp_path)
        broker._fail_startup(RuntimeError("boom"))
        monkeypatch.setattr(api, "broker", broker)

        with pytest.raises(HTTPException) as refused:
            await api.activate_workflow_step(WorkflowStepRequest(**ACTIVATION), Response())
        assert refused.value.status_code == 503

    async def test_get_returns_active_then_the_closed_ending(
        self, tmp_path, api, monkeypatch
    ) -> None:
        broker = _step_broker(tmp_path)
        monkeypatch.setattr(api, "broker", broker)
        await _accept(broker)
        active = await api.get_workflow_step(ACTIVATION_ID)
        assert active["state"] == "active"
        assert active["ending"] is None

        await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        closed = await api.get_workflow_step(ACTIVATION_ID)
        assert closed["state"] == "closed"
        assert closed["ending"]["kind"] == "verdict"
        assert closed["ending"]["verdict"] == "implemented"
        assert closed["closed_at"]

    async def test_get_of_an_unknown_activation_is_404(self, tmp_path, api, monkeypatch) -> None:
        monkeypatch.setattr(api, "broker", _step_broker(tmp_path))

        with pytest.raises(HTTPException) as missing:
            await api.get_workflow_step(OTHER_ACTIVATION_ID)
        assert missing.value.status_code == 404


class TestTurnBoundaryInjection:
    @pytest.mark.parametrize("busy", ["active", "tool_executing", "awaiting_input"])
    async def test_a_running_or_blocked_turn_is_never_interrupted(self, tmp_path, busy) -> None:
        broker = _step_broker(tmp_path, activity=busy)

        await _accept(broker)

        broker._transport.send_message.assert_not_awaited()
        assert broker._open_workflow_step().injected is False

    async def test_the_instructions_arrive_when_the_turn_ends(self, tmp_path) -> None:
        broker = _step_broker(tmp_path, activity="active")
        await _accept(broker)
        broker._transport.send_message.assert_not_awaited()

        await broker._report_activity_state("idle")
        await _settle()

        broker._transport.send_message.assert_awaited_once()
        assert broker._transport.send_message.await_args.args[0] == ACTIVATION["instructions"]

    async def test_a_transport_whose_turn_is_in_flight_is_not_interrupted(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        broker._transport_tracks_turns = lambda: True
        broker._transport_turn_active = lambda: True

        await _accept(broker)

        broker._transport.send_message.assert_not_awaited()

    async def test_the_instructions_are_handed_over_once(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)

        await broker._report_activity_state("active")
        await broker._report_activity_state("idle")
        await _settle()

        broker._transport.send_message.assert_awaited_once()

    async def test_a_not_yet_started_transport_is_started_first(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        broker._transport.is_alive = False

        async def start() -> None:
            broker._transport.is_alive = True

        broker._transport.start = AsyncMock(side_effect=start)

        await _accept(broker)

        broker._transport.start.assert_awaited_once()
        broker._transport.send_message.assert_awaited_once()

    async def test_instructions_the_harness_never_accepted_fail_the_step_loudly(
        self, tmp_path
    ) -> None:
        broker = _step_broker(tmp_path)
        broker._transport.send_message = AsyncMock(side_effect=RuntimeError("pane is wedged"))
        broker._settings.delivery.max_attempts = 1

        await _accept(broker)
        await asyncio.sleep(0.05)

        step = broker.get_workflow_step(ACTIVATION_ID)
        assert step.state == "closed"
        assert step.ending["kind"] == "failed"
        assert "did not accept the step's instructions" in step.ending["failure_reason"]
        [report] = _step_reports(broker)
        assert report["event"] == "closed"
        assert report["ending"]["kind"] == "failed"


class TestStepFailures:
    async def test_a_transport_that_will_not_start_fails_the_step_with_the_reason(
        self, tmp_path
    ) -> None:
        broker = _step_broker(tmp_path)
        broker._transport.is_alive = False
        broker._transport.start = AsyncMock(side_effect=RuntimeError("claude binary missing"))

        await _accept(broker)

        step = broker.get_workflow_step(ACTIVATION_ID)
        assert step.state == "closed"
        assert (
            "could not hand the step to the harness: claude binary missing"
            in (step.ending["failure_reason"])
        )
        [report] = _step_reports(broker)
        assert report["ending"]["kind"] == "failed"
        broker._transport.send_message.assert_not_awaited()

    async def test_a_forge_that_is_slow_to_acknowledge_is_logged_not_fatal(
        self, tmp_path, caplog
    ) -> None:
        broker = _step_broker(tmp_path)
        broker._await_event_log_receipt = AsyncMock(return_value=None)

        created, _ = await _accept(broker)

        assert created is True
        assert "has not yet acknowledged the log flush" in caplog.text

    async def test_a_disabled_event_log_cannot_hold_a_step(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        broker._settings.event_log_enabled = False

        with pytest.raises(RuntimeError, match="would not survive a restart"):
            await broker.accept_workflow_step(WorkflowStepRequest(**ACTIVATION))

    async def test_a_step_closed_before_the_delivery_verdict_is_left_as_it_closed(
        self, tmp_path
    ) -> None:
        broker = _step_broker(tmp_path)
        broker._transport.send_message = AsyncMock(side_effect=RuntimeError("late failure"))
        broker._settings.delivery.max_attempts = 1
        await broker.accept_workflow_step(WorkflowStepRequest(**ACTIVATION))
        await _settle()
        reports = _step_reports(broker)
        assert reports[0]["ending"]["kind"] == "failed"

        # A second failure verdict for the same, already-closed step changes nothing.
        step = broker.get_workflow_step(ACTIVATION_ID)
        task = asyncio.create_task(asyncio.sleep(0))
        await broker._finish_step_delivery(step, task)

        assert len(_step_reports(broker)) == 1


class TestClosingAStep:
    async def test_a_valid_close_is_persisted_reported_and_answered(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)

        answer = await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        assert answer == {
            "activation_id": ACTIVATION_ID,
            "state": "closed",
            "ending": "verdict",
            "reported": True,
        }
        assert _step_frames(broker)[-1]["step"]["state"] == "closed"
        assert _step_frames(broker)[-1]["step"]["ending"]["verdict"] == "implemented"
        assert (broker._open_workflow_step() is not None) is False

    async def test_the_report_has_the_contracts_shape(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)

        await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        assert _step_reports(broker) == [
            {
                "event": "closed",
                "run_id": RUN_ID,
                "activation_id": ACTIVATION_ID,
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

    async def test_a_report_never_enters_the_heartbeat_detail(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)

        await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        assert "workflow_step" not in broker._activity_extra

    @pytest.mark.parametrize(
        "change, message",
        [
            (
                {"activation_id": OTHER_ACTIVATION_ID},
                f"is not the open step {ACTIVATION_ID}",
            ),
            ({"verdict": "shipped"}, "verdict 'shipped' is not valid for this step"),
            ({"verdict": None}, "ending 'verdict' needs `verdict`"),
            ({"outcome": None}, "needs `outcome` (required fields: summary)"),
            ({"outcome": {"files_changed": 2}}, "required field 'summary' is missing"),
            ({"outcome": {"summary": 5}}, "field 'summary': expected string"),
            (
                {"waiting_on": WAITING_ON},
                "`waiting_on` is only for ending 'waiting'",
            ),
            ({"failure_reason": "no"}, "`failure_reason` is only for ending 'failed'"),
        ],
    )
    async def test_a_bad_verdict_close_is_refused_with_the_exact_reason(
        self, tmp_path, change, message
    ) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)
        frames = len(_step_frames(broker))

        with pytest.raises(WorkflowStepCloseError, match=re.escape(message)):
            await broker.close_workflow_step(WorkflowStepCloseArgs(**{**CLOSE, **change}))

        assert len(_step_frames(broker)) == frames
        assert _step_reports(broker) == []
        assert (broker._open_workflow_step() is not None) is True

    async def test_an_ending_the_step_does_not_allow_is_refused(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker, {**ACTIVATION, "valid_endings": ["verdict", "failed"]})

        with pytest.raises(WorkflowStepCloseError, match=r"not allowed for this step. Allowed"):
            await broker.close_workflow_step(
                WorkflowStepCloseArgs(
                    activation_id=ACTIVATION_ID, ending="waiting", waiting_on=WAITING_ON
                )
            )

    async def test_waiting_needs_a_handle_and_failed_needs_a_reason(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)

        with pytest.raises(WorkflowStepCloseError, match="needs `waiting_on`"):
            await broker.close_workflow_step(
                WorkflowStepCloseArgs(activation_id=ACTIVATION_ID, ending="waiting")
            )
        with pytest.raises(WorkflowStepCloseError, match="needs `failure_reason`"):
            await broker.close_workflow_step(
                WorkflowStepCloseArgs(activation_id=ACTIVATION_ID, ending="failed")
            )

    async def test_done_and_failed_endings_are_accepted(self, tmp_path) -> None:
        for ending, extra in (("done", {}), ("failed", {"failure_reason": "tests do not run"})):
            broker = _step_broker(tmp_path)
            await _accept(broker)

            await broker.close_workflow_step(
                WorkflowStepCloseArgs(activation_id=ACTIVATION_ID, ending=ending, **extra)
            )

            [report] = _step_reports(broker)
            assert report["ending"]["kind"] == ending
            assert report["ending"]["failure_reason"] == extra.get("failure_reason")

    async def test_a_step_without_a_schema_takes_any_outcome(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker, {**ACTIVATION, "outcome_schema": None})

        await broker.close_workflow_step(
            WorkflowStepCloseArgs(**{**CLOSE, "outcome": {"anything": [1, 2]}})
        )

        assert _step_reports(broker)[0]["ending"]["outcome"] == {"anything": [1, 2]}

    async def test_empty_valid_verdicts_takes_any_verdict(self, tmp_path) -> None:
        """A persona with one produced event and no verdict map: every verdict leads there."""
        broker = _step_broker(tmp_path)
        await _accept(broker, {**ACTIVATION, "valid_verdicts": [], "outcome_schema": None})

        await broker.close_workflow_step(
            WorkflowStepCloseArgs(**{**CLOSE, "verdict": "shipped", "outcome": None})
        )

        assert _step_reports(broker)[0]["ending"]["verdict"] == "shipped"

    async def test_waiting_keeps_the_step_open_and_it_can_close_again(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)

        await broker.close_workflow_step(
            WorkflowStepCloseArgs(
                activation_id=ACTIVATION_ID, ending="waiting", waiting_on=WAITING_ON
            )
        )

        assert broker.get_workflow_step(ACTIVATION_ID).state == "waiting"
        assert (broker._open_workflow_step() is not None) is True
        assert _step_reports(broker)[0]["ending"]["waiting_on"] == WAITING_ON

        await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        assert broker.get_workflow_step(ACTIVATION_ID).state == "closed"
        assert [r["ending"]["kind"] for r in _step_reports(broker)] == ["waiting", "verdict"]

    async def test_a_closed_step_cannot_be_closed_again(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)
        await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))
        await _accept(broker, {**ACTIVATION, "activation_id": OTHER_ACTIVATION_ID})

        with pytest.raises(WorkflowStepCloseError, match="is not the open step"):
            await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

    async def test_with_no_open_step_the_close_says_so(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)

        with pytest.raises(WorkflowStepCloseError, match="no workflow step is open"):
            await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

    async def test_a_close_whose_frame_was_not_stored_is_refused_and_leaves_the_step_open(
        self, tmp_path
    ) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)
        broker._await_event_log_receipt = AsyncMock(return_value=False)

        with pytest.raises(RuntimeError, match="was not stored"):
            await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        step = broker.get_workflow_step(ACTIVATION_ID)
        assert (step.state, step.ending, step.closed_at) == ("active", None, None)
        assert _step_reports(broker) == []

    def test_the_pure_check_does_not_change_the_step(self) -> None:
        step = WorkflowStep.from_request(WorkflowStepRequest(**ACTIVATION))
        before = copy.deepcopy(step)

        ending = ending_from_close(step, WorkflowStepCloseArgs(**CLOSE))

        assert ending["kind"] == "verdict"
        assert step == before


class TestReportsReachTing:
    async def test_an_undelivered_report_is_kept_and_sent_in_order_later(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)
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
                activation_id=ACTIVATION_ID, ending="waiting", waiting_on=WAITING_ON
            )
        )
        assert len(broker._pending_step_reports) == 1
        assert delivered == []

        await broker._flush_workflow_step_reports()

        assert broker._pending_step_reports == []
        [report] = [p["metadata"]["workflow_step"] for p in delivered]
        assert report["ending"]["kind"] == "waiting"

    async def test_reports_are_stamped_with_the_current_state_each_time(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)
        await broker._report_workflow_step({"event": "unclosed"})
        first = broker._posted[-1]["state_since"]

        broker._set_activity_state("tool_executing")
        broker._set_activity_state("idle")
        await broker._report_workflow_step({"event": "unclosed"})

        assert broker._posted[-1]["state"] == "idle"
        assert broker._posted[-1]["state_since"] >= first

    async def test_with_activity_reporting_off_the_report_is_held_and_logged(
        self, tmp_path, caplog
    ) -> None:
        broker = _step_broker(tmp_path)
        broker._settings.activity_reporting_enabled = False
        await _accept(broker)

        await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        assert len(broker._pending_step_reports) == 1
        assert "workflow step report cannot be sent" in caplog.text


class TestUnclosedTurns:
    async def test_a_turn_that_ends_with_the_step_open_is_reported_unclosed(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)

        await broker._report_activity_state("active")
        await broker._report_activity_state("idle")

        assert _step_reports(broker) == [
            {
                "event": "unclosed",
                "run_id": RUN_ID,
                "activation_id": ACTIVATION_ID,
                "attempt": 1,
            }
        ]
        step = broker.get_workflow_step(ACTIVATION_ID)
        assert step.state == "active"

    async def test_nothing_is_re_prompted_after_an_unclosed_turn(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)

        await broker._report_activity_state("active")
        await broker._report_activity_state("idle")
        await _settle()

        broker._transport.send_message.assert_awaited_once()

    async def test_a_turn_that_closed_the_step_is_not_unclosed(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)
        await broker._report_activity_state("active")
        await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        await broker._report_activity_state("idle")

        assert [r["event"] for r in _step_reports(broker)] == ["closed"]

    async def test_a_turn_that_parked_the_step_waiting_is_not_unclosed(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)
        await broker._report_activity_state("active")
        await broker.close_workflow_step(
            WorkflowStepCloseArgs(
                activation_id=ACTIVATION_ID, ending="waiting", waiting_on=WAITING_ON
            )
        )

        await broker._report_activity_state("idle")

        assert [r["ending"]["kind"] for r in _step_reports(broker)] == ["waiting"]

    async def test_a_turn_that_never_started_is_not_unclosed(self, tmp_path) -> None:
        broker = _step_broker(tmp_path, activity="provisioning")
        await _accept(broker)

        await broker._report_activity_state("idle")

        assert _step_reports(broker) == []


class TestRestart:
    @staticmethod
    def _frames(*steps: dict) -> list:
        return [
            SimpleNamespace(
                kind=WORKFLOW_STEP_FRAME, payload={"type": WORKFLOW_STEP_FRAME, "step": s}
            )
            for s in steps
        ]

    @staticmethod
    def _step(**changes) -> dict:
        step = WorkflowStep.from_request(WorkflowStepRequest(**ACTIVATION)).frame()["step"]
        return {**step, **changes}

    async def test_the_open_step_is_rebuilt_from_the_log(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)

        broker._restore_durable_workflow_steps(
            self._frames(self._step(), self._step(injected=True))
        )

        step = broker._open_workflow_step()
        assert step.activation_id == ACTIVATION_ID
        assert step.injected is True
        assert (broker._open_workflow_step() is not None) is True

    async def test_a_closed_step_is_restored_closed_with_its_ending(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        ending = {
            "kind": "done",
            "verdict": None,
            "outcome": {},
            "note": None,
            "evidence": [],
            "waiting_on": None,
            "failure_reason": None,
        }

        broker._restore_durable_workflow_steps(
            self._frames(
                self._step(injected=True),
                self._step(
                    injected=True,
                    state="closed",
                    ending=ending,
                    closed_at="2026-11-08T11:00:00+00:00",
                ),
            )
        )

        assert broker._open_workflow_step() is None
        assert broker.get_workflow_step(ACTIVATION_ID).ending == ending

    async def test_a_restored_waiting_step_accepts_a_later_close(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        broker._restore_durable_workflow_steps(
            self._frames(self._step(injected=True, state="waiting"))
        )

        await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        assert broker.get_workflow_step(ACTIVATION_ID).state == "closed"

    async def test_a_step_never_handed_over_is_handed_over_after_the_restart(
        self, tmp_path
    ) -> None:
        broker = _step_broker(tmp_path)
        broker._restore_durable_workflow_steps(self._frames(self._step(injected=False)))

        await broker._maybe_inject_workflow_step()
        await _settle()

        broker._transport.send_message.assert_awaited_once()

    async def test_a_step_already_handed_over_is_not_handed_over_again(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        broker._restore_durable_workflow_steps(self._frames(self._step(injected=True)))

        await broker._maybe_inject_workflow_step()
        await _settle()

        broker._transport.send_message.assert_not_awaited()

    async def test_frames_of_other_kinds_are_ignored(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)

        broker._restore_durable_workflow_steps(
            [SimpleNamespace(kind="room_member", payload={"type": "room_member"})]
        )

        assert (broker._open_workflow_step() is not None) is False

    async def test_the_restore_runs_with_the_broker_runtime_state(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)

        broker._restore_durable_runtime_state(self._frames(self._step()))

        assert (broker._open_workflow_step() is not None) is True


class TestForgeMcpHost:
    async def test_the_host_closes_the_open_step_and_refuses_when_none_is_open(
        self, tmp_path
    ) -> None:
        broker = _step_broker(tmp_path)
        host = BrokerForgeMcpHost(broker, MagicMock())
        assert host.workflow_step_host() is True
        with pytest.raises(WorkflowStepCloseError, match="no workflow step is open"):
            await host.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))
        await _accept(broker)

        answer = await host.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        assert answer["state"] == "closed"
        assert broker._open_workflow_step() is None

    async def test_the_tool_is_listed_for_the_whole_session_of_a_step_host(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        toolbox = broker._forge_mcp_toolbox()
        assert "workflow_step_close" in [t["name"] for t in toolbox.list_tools()]
        await _accept(broker)
        assert "workflow_step_close" in [t["name"] for t in toolbox.list_tools()]

        await broker.close_workflow_step(WorkflowStepCloseArgs(**CLOSE))

        assert "workflow_step_close" in [t["name"] for t in toolbox.list_tools()]

    async def test_a_call_with_no_open_step_is_refused_and_records_nothing(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        toolbox = broker._forge_mcp_toolbox()

        result = await toolbox.call("workflow_step_close", CLOSE)

        assert result.is_error
        assert "no workflow step is open on this session" in result.text
        assert _step_frames(broker) == []
        assert _step_reports(broker) == []

    async def test_a_call_for_another_activation_is_refused_and_records_nothing(
        self, tmp_path
    ) -> None:
        broker = _step_broker(tmp_path)
        await _accept(broker)
        frames = len(_step_frames(broker))
        toolbox = broker._forge_mcp_toolbox()

        result = await toolbox.call(
            "workflow_step_close", {**CLOSE, "activation_id": OTHER_ACTIVATION_ID}
        )

        assert result.is_error
        assert f"activation {OTHER_ACTIVATION_ID} is not the open step {ACTIVATION_ID}" in (
            result.text
        )
        assert len(_step_frames(broker)) == frames
        assert _step_reports(broker) == []
        assert broker._open_workflow_step() is not None

    async def test_a_bad_close_is_refused_and_a_good_one_goes_through(self, tmp_path) -> None:
        broker = _step_broker(tmp_path)
        toolbox = broker._forge_mcp_toolbox()
        await _accept(broker)

        result = await toolbox.call("workflow_step_close", {**CLOSE, "verdict": "shipped"})

        assert result.is_error
        assert "verdict 'shipped' is not valid for this step" in result.text
        assert broker._open_workflow_step() is not None

        result = await toolbox.call("workflow_step_close", CLOSE)

        assert not result.is_error
        assert json.loads(result.text)["state"] == "closed"

    async def test_a_session_that_is_not_a_step_host_never_lists_the_tool(self, tmp_path) -> None:
        broker = Broker(SkuldSettings(session={"id": SESSION_ID, "workspace_dir": str(tmp_path)}))
        toolbox = broker._forge_mcp_toolbox()

        assert "workflow_step_close" not in [t["name"] for t in toolbox.list_tools()]
        result = await toolbox.call("workflow_step_close", CLOSE)
        assert result.is_error
        assert "not a workflow step host" in result.text
