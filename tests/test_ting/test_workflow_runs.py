"""A run from launch to a completed campaign, through the engine and the real adapters.

The engine, the run service, the Forge step-session adapter and the campaign projector
are real; only the ledger storage, the Forge and the campaign repository are in memory.
The harness sessions' side of the contract is played by feeding the activity subscriber
the ``workflow_step`` metadata Skuld would report.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from ting.adapters.campaign_step_definitions import CampaignStepRunDefinitions
from ting.adapters.forge_step_sessions import ForgeStepSessions
from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.config import WatcherConfig
from ting.domain.campaign_run_projection import PENDING_GATES_KEY
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.activity_subscriber import SessionActivitySubscriber
from ting.domain.services.gate_notifications import GateNotifier
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.domain.services.workflow_runs import WorkflowRuns, ending_of
from ting.domain.services.workflow_step_engine import WorkflowStepEngine
from ting.domain.step_engine import EngineSettings
from ting.domain.workflow_steps import (
    ActorKind,
    EndingKind,
    GateActor,
    GateDecision,
    InvalidEndingError,
    RunStateError,
    StepFencingError,
    StepState,
    WaitingOnKind,
)
from ting.ports.volundr import ActivityEvent

from .fake_forge import FakeForge, FakeForgeFactory, FakeSagas
from .graph_builders import edge, end, stage, trigger
from .step_support import InMemoryStepRepository

OWNER = "user-1"
NOW = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


class _Campaigns:
    """The campaign repository, in memory."""

    def __init__(self) -> None:
        self.items: dict[UUID, WorkflowCampaign] = {}

    async def save_campaign(self, campaign: WorkflowCampaign) -> WorkflowCampaign:
        self.items[campaign.id] = campaign
        return campaign

    async def get_campaign(self, campaign_id: UUID) -> WorkflowCampaign | None:
        return self.items.get(campaign_id)

    async def list_active_campaigns(self) -> list[WorkflowCampaign]:
        return [
            c
            for c in self.items.values()
            if c.status not in {WorkflowCampaignStatus.COMPLETED, WorkflowCampaignStatus.FAILED}
        ]

    async def list_active_owner_ids(self) -> list[str]:
        return sorted({c.owner_id for c in await self.list_active_campaigns()})


def _document(alias: str, produces: dict[str, str], consumes: list[str]) -> dict:
    return {
        "schema_version": 1,
        "id": alias,
        "revision": "r1",
        "definition": {
            "produces": {
                "event_type_map": produces,
                "schema": {"summary": {"type": "string", "description": "what you did"}},
            },
            "consumes": {"event_types": consumes},
        },
    }


def _snapshot() -> dict:
    """implement (coder) -> review (reviewer) -> gate -> end, with two rework edges."""
    gate = {
        "id": "approve",
        "kind": "gate",
        "label": "Approve the change",
        "mode": "human_approval",
        "approvalEvent": "gate.approved",
        "changesRequestedEvent": "gate.changes",
        "instructions": "Approve when the change is safe to merge.",
    }
    graph = {
        "nodes": [
            trigger("work.requested", "go"),
            stage("implement", [{"personaId": "coder", "model": "claude-sonnet-4-6"}]),
            stage("review", [{"personaId": "reviewer"}]),
            gate,
            end("done"),
        ],
        "edges": [
            edge("e1", "go", "implement", "work.requested"),
            edge("e2", "implement", "review", "code.ready"),
            edge("e3", "review", "implement", "review.failed", rework=True, maxIterations=2),
            edge("e4", "review", "approve", "review.passed"),
            edge("e5", "approve", "done", "gate.approved"),
            edge("e6", "approve", "implement", "gate.changes", rework=True, maxIterations=2),
        ],
    }
    documents = {
        "coder": _document(
            "coder",
            {"implemented": "code.ready"},
            ["work.requested", "review.failed", "gate.changes"],
        ),
        "reviewer": _document(
            "reviewer", {"pass": "review.passed", "fail": "review.failed"}, ["code.ready"]
        ),
    }
    return {
        "name": "Build and review",
        "graph": graph,
        "persona_definitions": documents,
        "persona_dependencies": {
            alias: {"id": alias, "revision": "r1", "digest": "sha256:" + "0" * 64}
            for alias in documents
        },
    }


def _campaign(snapshot: dict | None = None) -> WorkflowCampaign:
    return WorkflowCampaign(
        id=uuid4(),
        slug="add-endpoint-0123456789ab",
        name="add-endpoint",
        owner_id=OWNER,
        workflow_id=uuid4(),
        workflow_version="1.0.0",
        workflow_name="Build and review",
        workflow_snapshot=snapshot or _snapshot(),
        session_id="",
        session_name="",
        status=WorkflowCampaignStatus.PENDING,
        active_stage_id=None,
        stage_state=[],
        metadata={
            "repo": "https://github.com/niuulabs/volundr.git",
            "branch": "feat/x",
            "model": "claude-opus-launch",
        },
        created_at=NOW,
        updated_at=NOW,
        connection_id="cluster-1",
    )


class Harness:
    def __init__(self, snapshot: dict | None = None) -> None:
        self.forge = FakeForge()
        self.campaigns = _Campaigns()
        self.steps = InMemoryStepRepository()
        self.bus = InMemoryEventBus()
        factory = FakeForgeFactory(self.forge)
        self.projector = WorkflowCampaignProjector(
            repo=self.campaigns,  # type: ignore[arg-type]
            event_bus=self.bus,
            run_launched=lambda run_id: self._launched(run_id),
        )
        engine = WorkflowStepEngine(
            repository=self.steps,
            sessions=ForgeStepSessions(
                volundr_factory=factory,  # type: ignore[arg-type]
                campaigns=self.campaigns,  # type: ignore[arg-type]
                sagas=FakeSagas(),
            ),
            definitions=CampaignStepRunDefinitions(self.campaigns),  # type: ignore[arg-type]
            settings=EngineSettings(step_deadline_seconds=600.0, session_poll_seconds=0.01),
        )
        self.runs = WorkflowRuns(
            engine=engine,
            steps=self.steps,
            campaigns=self.campaigns,  # type: ignore[arg-type]
            sink=self.projector,
            volundr_factory=factory,  # type: ignore[arg-type]
            gate_notifier=GateNotifier(
                volundr_factory=factory,  # type: ignore[arg-type]
                public_origin="https://niuu.example/",
            ),
        )
        self.subscriber = SessionActivitySubscriber(
            volundr_factory=factory,  # type: ignore[arg-type]
            tracker_factory=SimpleNamespace(),  # type: ignore[arg-type]
            dispatcher_repo=SimpleNamespace(),  # type: ignore[arg-type]
            event_bus=self.bus,
            config=WatcherConfig(),
            workflow_runs=self.runs,
        )
        self.campaign = _campaign(snapshot)

    async def _launched(self, run_id: UUID) -> bool:
        return (await self.steps.load(run_id)).launched

    async def launch(self) -> None:
        await self.campaigns.save_campaign(self.campaign)
        await self.runs.launch(self.campaign, {"prompt": "add an endpoint"})

    @property
    def current(self) -> WorkflowCampaign:
        return self.campaigns.items[self.campaign.id]

    def last_delivery(self) -> tuple[str, dict]:
        return self.forge.deliveries[-1]

    async def harness_closes(self, ending: dict, *, session_id: str | None = None) -> None:
        """The session answers the step it was last given, as Skuld would report it."""
        delivered_to, body = self.last_delivery()
        await self.subscriber._on_activity_event(
            ActivityEvent(
                session_id=session_id or delivered_to,
                state="idle",
                metadata={
                    "workflow_step": {
                        "event": "closed",
                        "run_id": body["run_id"],
                        "activation_id": body["activation_id"],
                        "attempt": body["attempt"],
                        "ending": ending,
                    }
                },
                owner_id=OWNER,
            ),
            self.forge,
            OWNER,
        )

    async def harness_says(self, verdict: str, **fields) -> None:
        await self.harness_closes({"kind": "verdict", "verdict": verdict, **fields})


@pytest.fixture
def harness() -> Harness:
    return Harness()


async def test_a_run_goes_from_launch_through_a_rework_and_a_gate_to_a_completed_campaign(
    harness: Harness,
) -> None:
    # -- launch: the first member's session is created and handed the first step.
    await harness.launch()

    [coder_request] = harness.forge.create_requests
    assert coder_request.member == "coder"
    assert coder_request.persona_definition == _snapshot()["persona_definitions"]["coder"]
    assert coder_request.tracker_issue_id == "workflow:add-endpoint-0123456789ab"
    assert coder_request.model == "claude-sonnet-4-6"
    coder_session, body = harness.last_delivery()
    assert body["node_id"] == "implement" and body["member"] == "coder"
    assert body["valid_verdicts"] == ["implemented"]
    assert body["outcome_schema"] == {"summary": {"type": "string", "description": "what you did"}}
    assert "add an endpoint" in body["instructions"]
    assert "workflow_step_close" in body["instructions"]
    assert harness.current.status is WorkflowCampaignStatus.RUNNING
    assert harness.current.active_stage_id == "implement"
    assert harness.current.session_id == ""

    # -- the coder implements: the reviewer gets its own session and the coder's outcome.
    await harness.harness_says("implemented", outcome={"summary": "first try"}, note="done")

    reviewer_session, review_body = harness.last_delivery()
    assert reviewer_session != coder_session
    # The reviewer's stage names no model, so it runs on the launch's.
    assert harness.forge.create_requests[1].model == "claude-opus-launch"
    assert review_body["node_id"] == "review" and review_body["member"] == "reviewer"
    assert "first try" in review_body["instructions"]
    assert harness.current.active_stage_id == "review"

    # -- the reviewer rejects: a rework. The coder is activated again on its own session.
    await harness.harness_says("fail", note="the null case crashes")

    again_session, rework_body = harness.last_delivery()
    assert again_session == coder_session
    assert rework_body["member"] == "coder"
    assert (rework_body["attempt"], rework_body["generation"]) == (2, 1)
    assert "the null case crashes" in rework_body["instructions"]
    assert "rework generation 1" in rework_body["instructions"]
    assert harness.current.active_stage_id == "implement"

    # -- the coder fixes it and the reviewer passes it on the same sessions: no new ones.
    await harness.harness_says("implemented", outcome={"summary": "fixed"})
    assert harness.last_delivery()[0] == reviewer_session
    assert harness.last_delivery()[1]["attempt"] == 2
    await harness.harness_says("pass", note="looks right")

    assert len(harness.forge.create_requests) == 2

    # -- the gate waits for a person; the campaign says so, and lists the gate.
    assert harness.current.status is WorkflowCampaignStatus.BLOCKED
    assert harness.current.active_stage_id == "approve"
    [gate] = harness.current.metadata[PENDING_GATES_KEY]
    assert (gate["id"], gate["status"], gate["label"]) == (
        "approve",
        "pending",
        "Approve the change",
    )
    assert gate["instructions"] == "Approve when the change is safe to merge."
    assert gate["summary"] == "looks right"
    assert [g["id"] for g in await harness.runs.pending_gates(harness.current)] == ["approve"]

    # -- the person approves through the engine: the run completes and its sessions stop.
    await harness.runs.resolve_gate(
        harness.current,
        "approve",
        GateDecision.APPROVE,
        by=GateActor(ActorKind.PERSON, "alice"),
        note="ship it",
    )

    assert harness.current.status is WorkflowCampaignStatus.COMPLETED
    assert harness.current.completed_at is not None
    assert harness.current.active_stage_id is None
    assert PENDING_GATES_KEY not in harness.current.metadata
    assert {s.status for s in harness.current.stage_state} == {"complete"}
    assert sorted(harness.forge.stopped) == sorted([coder_session, reviewer_session])
    [answered] = await harness.runs.gates(harness.current)
    assert (answered["status"], answered["decided_by"], answered["notes"]) == (
        "approved",
        "alice",
        "ship it",
    )


async def test_a_gate_request_for_changes_sends_the_work_back_to_the_coder(
    harness: Harness,
) -> None:
    await harness.launch()
    await harness.harness_says("implemented")
    await harness.harness_says("pass")

    await harness.runs.resolve_gate(
        harness.current,
        "approve",
        GateDecision.REQUEST_CHANGES,
        by=GateActor(ActorKind.PERSON, "alice"),
        note="add a test",
    )

    _, body = harness.last_delivery()
    assert body["member"] == "coder" and body["generation"] == 1
    assert "add a test" in body["instructions"]
    assert harness.current.status is WorkflowCampaignStatus.RUNNING


async def test_a_close_from_a_session_that_does_not_own_the_step_is_fenced(
    harness: Harness, caplog
) -> None:
    await harness.launch()

    with caplog.at_level("ERROR", logger="ting.domain.services.activity_subscriber"):
        await harness.harness_says("implemented", outcome={"summary": "x"})
        before = len(harness.forge.deliveries)
        # A different close of the same activation, from the right session: the step is
        # done. (The same close again is a repeat delivery and is accepted silently.)
        delivered_to, _ = harness.forge.deliveries[0]
        body = harness.forge.deliveries[0][1]
        await harness.subscriber._on_activity_event(
            ActivityEvent(
                session_id=delivered_to,
                state="idle",
                metadata={
                    "workflow_step": {
                        "event": "closed",
                        "run_id": body["run_id"],
                        "activation_id": body["activation_id"],
                        "attempt": 1,
                        "ending": {"kind": "failed", "note": "second thoughts"},
                    }
                },
                owner_id=OWNER,
            ),
            harness.forge,
            OWNER,
        )

    assert len(harness.forge.deliveries) == before
    assert any("was rejected" in record.getMessage() for record in caplog.records)


async def test_the_same_close_delivered_again_changes_nothing_and_is_not_rejected(
    harness: Harness, caplog
) -> None:
    await harness.launch()

    with caplog.at_level("ERROR", logger="ting.domain.services.activity_subscriber"):
        await harness.harness_says("implemented", outcome={"summary": "x"})
        state = await harness.steps.load(harness.campaign.id)
        before = len(harness.forge.deliveries)
        delivered_to, body = harness.forge.deliveries[0]
        repeat = ActivityEvent(
            session_id=delivered_to,
            state="idle",
            metadata={
                "workflow_step": {
                    "event": "closed",
                    "run_id": body["run_id"],
                    "activation_id": body["activation_id"],
                    "attempt": 1,
                    "ending": {
                        "kind": "verdict",
                        "verdict": "implemented",
                        "outcome": {"summary": "x"},
                    },
                }
            },
            owner_id=OWNER,
        )
        for _ in range(3):
            await harness.subscriber._on_activity_event(repeat, harness.forge, OWNER)

    assert len(harness.forge.deliveries) == before
    assert await harness.steps.load(harness.campaign.id) == state
    assert not any("was rejected" in record.getMessage() for record in caplog.records)


async def test_a_verdict_the_persona_does_not_declare_changes_nothing(
    harness: Harness, caplog
) -> None:
    await harness.launch()

    with caplog.at_level("ERROR", logger="ting.domain.services.activity_subscriber"):
        await harness.harness_says("approved-by-me")

    assert len(harness.forge.deliveries) == 1
    open_steps = (await harness.steps.load(harness.campaign.id)).open_steps()
    assert [step.node_id for step in open_steps] == ["implement"]
    assert any("was rejected" in record.getMessage() for record in caplog.records)


async def test_a_session_that_cannot_be_created_fails_the_run_and_its_campaign(
    harness: Harness,
) -> None:
    harness.forge.fail_create = RuntimeError("no capacity")

    await harness.launch()

    assert harness.current.status is WorkflowCampaignStatus.FAILED
    assert "no session for 'coder'" in harness.current.metadata["failure_error"]
    assert harness.current.metadata["failure_stage_id"] == "implement"


async def test_a_step_the_session_refuses_fails_the_run_naming_the_member(
    harness: Harness,
) -> None:
    harness.forge.fail_deliver = RuntimeError("409: another step is open")

    await harness.launch()

    assert harness.current.status is WorkflowCampaignStatus.FAILED
    assert "delivery to 'coder' failed" in harness.current.metadata["failure_error"]


async def test_a_session_that_ends_its_turn_without_closing_leaves_the_step_open_and_counted(
    harness: Harness,
) -> None:
    await harness.launch()
    delivered_to, body = harness.last_delivery()

    await harness.subscriber._on_activity_event(
        ActivityEvent(
            session_id=delivered_to,
            state="idle",
            metadata={
                "workflow_step": {
                    "event": "unclosed",
                    "run_id": body["run_id"],
                    "activation_id": body["activation_id"],
                    "attempt": body["attempt"],
                }
            },
            owner_id=OWNER,
        ),
        harness.forge,
        OWNER,
    )

    state = await harness.steps.load(harness.campaign.id)
    assert [step.state for step in state.steps if step.node_id == "implement"] == [StepState.ACTIVE]
    [unclosed] = [e for e in state.events if e.event_type == "engine.step.unclosed"]
    assert unclosed.payload["session_id"] == delivered_to
    assert len(harness.forge.deliveries) == 1


async def test_cancelling_the_run_stops_its_sessions_and_cancels_the_campaign(
    harness: Harness,
) -> None:
    await harness.launch()
    await harness.harness_says("implemented")
    reviewer = harness.last_delivery()[0]

    await harness.runs.cancel(harness.current, "the owner changed their mind")

    assert harness.current.status is WorkflowCampaignStatus.CANCELED
    assert harness.current.metadata["cancel_reason"] == "the owner changed their mind"
    assert "failure_error" not in harness.current.metadata
    assert harness.current.metadata["run_canceled"] is True
    assert reviewer in harness.forge.stopped and len(harness.forge.stopped) == 2


async def test_reconnecting_records_a_close_the_stream_missed(harness: Harness) -> None:
    await harness.launch()
    session, body = harness.last_delivery()
    harness.forge.step_states[(session, body["activation_id"])] = {
        "activation_id": body["activation_id"],
        "state": "closed",
        "attempt": 1,
        "ending": {"kind": "verdict", "verdict": "implemented", "outcome": {"summary": "x"}},
    }

    await harness.runs.reconcile_owner(OWNER)

    assert harness.current.active_stage_id == "review"
    assert harness.last_delivery()[1]["member"] == "reviewer"


async def test_reconnecting_leaves_a_step_the_session_still_holds_open(harness: Harness) -> None:
    await harness.launch()
    session, body = harness.last_delivery()
    harness.forge.step_states[(session, body["activation_id"])] = {
        "activation_id": body["activation_id"],
        "state": "active",
        "attempt": 1,
    }

    await harness.runs.reconcile_owner(OWNER)

    assert len(harness.forge.deliveries) == 1
    assert harness.current.active_stage_id == "implement"


async def test_a_run_that_cannot_be_reconciled_is_loud_and_the_others_still_are(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await harness.launch()
    session, body = harness.last_delivery()
    harness.forge.step_states[(session, body["activation_id"])] = {
        "activation_id": body["activation_id"],
        "state": "closed",
        "attempt": 1,
        "ending": {"kind": "verdict", "verdict": "implemented", "outcome": {"summary": "x"}},
    }
    broken = replace(
        harness.campaign, id=uuid4(), slug="broken-run", created_at=NOW - timedelta(days=1)
    )
    # Listed first: the broken run is reconciled before the healthy one.
    harness.campaigns.items = {broken.id: broken, **harness.campaigns.items}
    counted: list[str] = []
    monkeypatch.setattr(
        "ting.domain.services.workflow_runs.get_observability",
        lambda: SimpleNamespace(count=lambda name, **_: counted.append(name)),
    )
    real = harness.runs._reconcile_run

    async def reconcile(campaign: WorkflowCampaign) -> None:
        if campaign.id == broken.id:
            raise ConnectionError("forge is down")
        await real(campaign)

    monkeypatch.setattr(harness.runs, "_reconcile_run", reconcile)

    with caplog.at_level(logging.ERROR):
        await harness.runs.reconcile_owner(OWNER)

    assert counted == ["ting_workflow_run_reconcile_errors_total"]
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "broken-run" in record.getMessage()
    assert "ConnectionError: forge is down" in record.getMessage()
    assert record.exc_info is not None
    # The healthy run behind it was still caught up.
    assert harness.current.active_stage_id == "review"


async def test_reconnecting_does_not_ask_a_session_that_is_still_starting(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await harness.launch()
    session_id, _ = harness.last_delivery()
    harness.forge.sessions[session_id] = replace(
        harness.forge.sessions[session_id], status="starting"
    )

    async def refused(*_args, **_kwargs):
        raise AssertionError("a starting session's steps are not read")

    monkeypatch.setattr(harness.forge, "get_workflow_step", refused)

    await harness.runs.reconcile_owner(OWNER)

    assert harness.current.active_stage_id == "implement"


async def test_reconnecting_with_a_session_that_never_saw_the_step_changes_nothing(
    harness: Harness,
) -> None:
    await harness.launch()

    await harness.runs.reconcile_owner(OWNER)

    assert harness.current.active_stage_id == "implement"


async def test_a_close_that_names_another_runs_activation_is_fenced(harness: Harness) -> None:
    await harness.launch()
    session, body = harness.last_delivery()

    with pytest.raises(StepFencingError, match="belongs to run"):
        await harness.runs.record_step_closed(
            run_id=uuid4(),
            activation_id=UUID(body["activation_id"]),
            session_id=session,
            attempt=1,
            report={"kind": "verdict", "verdict": "implemented"},
        )


async def test_a_waiting_ending_parks_the_step_and_a_later_close_is_accepted(
    harness: Harness,
) -> None:
    await harness.launch()
    await harness.harness_closes(
        {
            "kind": "waiting",
            "note": "needs the design",
            "waiting_on": {"kind": "person", "ref": "alice", "reason": "which API shape?"},
        }
    )

    assert harness.current.status is WorkflowCampaignStatus.BLOCKED
    [step] = [s for s in (await harness.steps.load(harness.campaign.id)).steps if s.member]
    assert step.state is StepState.WAITING
    assert (step.waiting_on.kind, step.waiting_on.ref) == (WaitingOnKind.PERSON, "alice")
    assert "which API shape?" in step.note

    await harness.harness_says("implemented", outcome={"summary": "after the answer"})

    assert harness.current.status is WorkflowCampaignStatus.RUNNING
    assert harness.last_delivery()[1]["member"] == "reviewer"


# -- the ending a session reports, as the engine reads it -----------------------------------


def test_a_verdict_report_becomes_a_verdict_ending() -> None:
    ending = ending_of(
        {
            "kind": "verdict",
            "verdict": "implemented",
            "outcome": {"summary": "x"},
            "note": "n",
            "evidence": [{"kind": "commit", "ref": "abc", "summary": "s"}],
            "waiting_on": None,
            "failure_reason": None,
        },
        now=NOW,
    )

    assert ending.kind is EndingKind.VERDICT
    assert (ending.verdict, ending.outcome, ending.note) == ("implemented", {"summary": "x"}, "n")
    assert ending.evidence == {"items": [{"kind": "commit", "ref": "abc", "summary": "s"}]}


def test_a_failed_report_carries_its_reason_as_the_note() -> None:
    ending = ending_of({"kind": "failed", "failure_reason": "tests cannot run"}, now=NOW)

    assert (ending.kind, ending.note) == (EndingKind.FAILED, "tests cannot run")


@pytest.mark.parametrize(
    "report, message",
    [
        ({"kind": "bogus"}, "is not one of"),
        ({"kind": "failed"}, "needs failure_reason"),
        ({"kind": "waiting"}, "needs waiting_on"),
        ({"kind": "waiting", "waiting_on": {"kind": "weather", "ref": "x"}}, "is not valid"),
        ({"kind": "verdict", "verdict": "x", "outcome": "text"}, "must be an object"),
        ({"kind": "verdict", "verdict": "x", "evidence": "text"}, "must be a list"),
        ({"kind": "verdict"}, "needs a verdict"),
    ],
)
def test_a_malformed_ending_is_refused_with_the_reason(report: dict, message: str) -> None:
    with pytest.raises(InvalidEndingError, match=message):
        ending_of(report, now=NOW)


def _wait_snapshot() -> dict:
    graph = {
        "nodes": [
            trigger("work.requested", "go"),
            {
                "id": "settle",
                "kind": "wait",
                "label": "Let it settle",
                "conditions": ["timer"],
            },
            end("done"),
        ],
        "edges": [
            edge("e1", "go", "settle", "work.requested"),
            edge("e2", "settle", "done", "settled"),
        ],
    }
    return {"name": "Settle", "graph": graph}


async def test_a_wait_step_is_closed_by_whatever_it_waited_for() -> None:
    harness = Harness(_wait_snapshot())
    await harness.launch()
    assert harness.forge.create_requests == []
    assert harness.current.active_stage_id == "settle"

    await harness.runs.close_wait(
        harness.campaign.id, "settle", "observed", outcome={"waitId": "w1"}
    )

    assert harness.current.status is WorkflowCampaignStatus.COMPLETED


async def test_closing_an_already_closed_wait_with_the_same_verdict_changes_nothing() -> None:
    harness = Harness(_wait_snapshot())
    await harness.launch()
    await harness.runs.close_wait(harness.campaign.id, "settle", "observed", outcome={})
    events = len((await harness.steps.load(harness.campaign.id)).events)

    await harness.runs.close_wait(harness.campaign.id, "settle", "observed", outcome={})

    assert len((await harness.steps.load(harness.campaign.id)).events) == events


async def test_closing_a_wait_with_a_verdict_the_node_does_not_define_is_refused() -> None:
    harness = Harness(_wait_snapshot())
    await harness.launch()

    with pytest.raises(InvalidEndingError, match="closes with"):
        await harness.runs.close_wait(harness.campaign.id, "settle", "joined", outcome={})

    assert harness.current.status is WorkflowCampaignStatus.RUNNING


async def test_closing_a_wait_that_does_not_exist_is_an_error() -> None:
    harness = Harness(_wait_snapshot())
    await harness.launch()

    with pytest.raises(RunStateError, match="no wait or subworkflow step at 'nowhere'"):
        await harness.runs.close_wait(harness.campaign.id, "nowhere", "observed", outcome={})


async def test_an_unclosed_report_from_a_session_that_does_not_own_the_step_is_fenced(
    harness: Harness,
) -> None:
    await harness.launch()
    _, body = harness.last_delivery()

    with pytest.raises(StepFencingError, match="does not own the step"):
        await harness.runs.record_step_unclosed(
            run_id=harness.campaign.id,
            activation_id=UUID(body["activation_id"]),
            session_id="somebody-else",
            attempt=1,
        )

    state = await harness.steps.load(harness.campaign.id)
    assert not [e for e in state.events if e.event_type == "engine.step.unclosed"]


async def test_an_unclosed_report_for_an_unknown_activation_is_fenced(harness: Harness) -> None:
    await harness.launch()

    with pytest.raises(StepFencingError, match="no such activation"):
        await harness.runs.record_step_unclosed(
            run_id=harness.campaign.id,
            activation_id=uuid4(),
            session_id="ses-1",
            attempt=1,
        )


# --- gates in Forge's notification feed --------------------------------------------


async def reach_the_gate(harness: Harness) -> str:
    """Launch and pass the change through review; returns the reviewer's session."""
    await harness.launch()
    await harness.harness_says("implemented", outcome={"summary": "x"})
    reviewer_session = harness.last_delivery()[0]
    await harness.harness_says("pass", note="looks right")
    return reviewer_session


async def test_an_open_gate_raises_one_attention_notification_linking_the_review(
    harness: Harness,
) -> None:
    reviewer_session = await reach_the_gate(harness)

    [(key, (session_id, note))] = harness.forge.notifications.items()
    [gate] = harness.current.metadata[PENDING_GATES_KEY]
    assert key == f"workflow-gate:{harness.campaign.id}:{gate['activation_id']}"
    assert session_id == reviewer_session  # the step whose outcome opened the gate
    assert (note["kind"], note["severity"]) == ("attention", "warning")
    assert note["title"] == "add-endpoint: Approve the change"
    assert note["body"] == "looks right"
    assert note["correlation_id"] == key
    assert note["links"] == [
        {
            "label": "Open the review",
            "url": "https://niuu.example/ting/review/run/add-endpoint-0123456789ab",
            "kind": "url",
        }
    ]
    calls = harness.forge.notification_calls
    await harness.runs.refresh(harness.campaign.id)
    assert harness.forge.notification_calls == calls  # raised once, not on every change
    assert harness.forge.resolved == {}


async def test_deciding_the_gate_settles_its_notification(harness: Harness) -> None:
    await reach_the_gate(harness)

    await harness.runs.resolve_gate(
        harness.current,
        "approve",
        GateDecision.APPROVE,
        by=GateActor(ActorKind.PERSON, "alice"),
        note="ship it",
    )

    [(_, (_, note))] = harness.forge.notifications.items()
    assert harness.forge.resolved == {note["id"]: "Approved by alice."}


async def test_a_notification_forge_refused_is_counted_and_raised_on_the_next_change(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    counted: list[str] = []
    monkeypatch.setattr(
        "ting.domain.services.gate_notifications.get_observability",
        lambda: SimpleNamespace(count=lambda name, **_: counted.append(name)),
    )
    harness.forge.fail_notify = RuntimeError("forge is down")

    with caplog.at_level("ERROR", logger="ting.domain.services.gate_notifications"):
        await reach_the_gate(harness)

    assert harness.forge.notifications == {}
    assert counted == ["ting_workflow_gate_notification_failures_total"]
    assert any("Could not raise the notification" in r.getMessage() for r in caplog.records)
    assert harness.current.status is WorkflowCampaignStatus.BLOCKED  # the gate still waits

    harness.forge.fail_notify = None
    await harness.runs.refresh(harness.campaign.id)
    assert len(harness.forge.notifications) == 1


async def test_a_gate_an_agent_answers_raises_no_notification() -> None:
    snapshot = _snapshot()
    for node in snapshot["graph"]["nodes"]:
        if node["id"] == "approve":
            node["mode"] = "automated_approval"
    harness = Harness(snapshot)

    await reach_the_gate(harness)

    assert harness.forge.notifications == {}


async def test_the_settled_notification_names_the_person_by_email_not_by_id(
    harness: Harness,
) -> None:
    await reach_the_gate(harness)

    await harness.runs.resolve_gate(
        harness.current,
        "approve",
        GateDecision.APPROVE,
        by=GateActor(ActorKind.PERSON, "76475334-b685", "jozef@niuu.world"),
        note="ship it",
    )

    [(_, (_, note))] = harness.forge.notifications.items()
    assert harness.forge.resolved == {note["id"]: "Approved by jozef@niuu.world."}
    [answered] = await harness.runs.gates(harness.current)
    assert answered["decided_by"] == "jozef@niuu.world"
