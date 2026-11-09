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
from ting.domain.step_engine import EngineSettings, unit_root, unit_sessions
from ting.domain.workflow_steps import (
    EVENT_OFFER_PASSED,
    EVENT_UNIT_LAUNCHED,
    ActorKind,
    EndingKind,
    GateActor,
    GateDecision,
    InvalidEndingError,
    RunState,
    RunStateError,
    StepFencingError,
    StepKind,
    StepState,
    WaitingOnKind,
    WorkflowStep,
)
from ting.ports.volundr import ActivityEvent

from .fake_forge import FakeForge, FakeForgeFactory, FakeSagas
from .graph_builders import edge, end, stage, trigger
from .step_support import FakeSigner, InMemoryStepRepository, Recorder, minted_activation_id

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
    """A run on the real engine and Forge adapter, with the members played over the activity stream.

    ``play`` is what the members' Skuld does: report each member listening (so the engine
    publishes its waiting offers to the root's room) and pick up every offer the root was handed.
    Every report goes through the real subscriber, as it would come off Forge's stream.
    """

    def __init__(self, snapshot: dict | None = None, *, catch_up_page_size: int = 200) -> None:
        self.forge = FakeForge()
        self.campaigns = _Campaigns()
        self.steps = InMemoryStepRepository()
        self.bus = InMemoryEventBus()
        self.factory = FakeForgeFactory(self.forge)
        self.projector = WorkflowCampaignProjector(
            repo=self.campaigns,  # type: ignore[arg-type]
            event_bus=self.bus,
            run_launched=lambda run_id: self._launched(run_id),
        )
        self.signer = FakeSigner()
        self.engine = WorkflowStepEngine(
            repository=self.steps,
            sessions=ForgeStepSessions(
                volundr_factory=self.factory,  # type: ignore[arg-type]
                campaigns=self.campaigns,  # type: ignore[arg-type]
                sagas=FakeSagas(),
            ),
            offers=self.signer,
            definitions=CampaignStepRunDefinitions(self.campaigns),  # type: ignore[arg-type]
            settings=EngineSettings(step_deadline_seconds=600.0),
        )
        self._page_size = catch_up_page_size
        self.runs = self._new_runs()
        self.campaign = _campaign(snapshot)
        self._listening: set[str] = set()
        self._taken: set[tuple[str, str]] = set()

    def _new_runs(self) -> WorkflowRuns:
        runs = WorkflowRuns(
            engine=self.engine,
            steps=self.steps,
            campaigns=self.campaigns,  # type: ignore[arg-type]
            sink=self.projector,
            volundr_factory=self.factory,  # type: ignore[arg-type]
            gate_notifier=GateNotifier(
                volundr_factory=self.factory,  # type: ignore[arg-type]
                public_origin="https://niuu.example/",
            ),
            catch_up_page_size=self._page_size,
        )
        self.subscriber = SessionActivitySubscriber(
            volundr_factory=self.factory,  # type: ignore[arg-type]
            tracker_factory=SimpleNamespace(),  # type: ignore[arg-type]
            dispatcher_repo=SimpleNamespace(),  # type: ignore[arg-type]
            event_bus=self.bus,
            config=WatcherConfig(),
            workflow_runs=runs,
        )
        return runs

    def restart_ting(self) -> None:
        """A new process: the run service starts again with no memory of how far it read."""
        self.runs = self._new_runs()

    async def _launched(self, run_id: UUID) -> bool:
        return (await self.steps.load(run_id)).launched

    async def launch(self, *, pickups: bool = True) -> None:
        await self.campaigns.save_campaign(self.campaign)
        await self.runs.launch(self.campaign, {"prompt": "add an endpoint"})
        await self.play(pickups=pickups)

    @property
    def current(self) -> WorkflowCampaign:
        return self.campaigns.items[self.campaign.id]

    async def state(self) -> RunState:
        return await self.steps.load(self.campaign.id)

    async def members(self) -> dict[str, str]:
        """Member alias -> the session of the run's unit that plays it."""
        return unit_sessions(await self.state())

    @property
    def offers(self) -> list[dict]:
        """The ``step.offered`` payload of every offer handed to the root's room, in order."""
        return [body["offer"] for _, body in self.forge.room_offers]

    def offer_for(self, node_id: str, attempt: int | None = None) -> dict:
        found = [
            offer
            for offer in self.offers
            if offer["node_id"] == node_id and (attempt is None or offer["attempt"] == attempt)
        ]
        assert found, f"no offer for {node_id!r} (attempt {attempt}) was published"
        return found[-1]

    async def report(self, session_id: str, event: str, **fields) -> None:
        """Feed the subscriber one ``workflow_step`` report, as Forge's stream would carry it."""
        await self.subscriber._on_activity_event(
            ActivityEvent(
                session_id=session_id,
                state="idle",
                metadata={
                    "workflow_step": {
                        "event": event,
                        "run_id": str(self.campaign.id),
                        **fields,
                    }
                },
                owner_id=OWNER,
            ),
            self.forge,
            OWNER,
        )

    async def play(self, *, pickups: bool = True) -> None:
        """Every member listens once; with ``pickups``, each takes up each offer addressed to it."""
        members = await self.members()
        for member, session_id in members.items():
            if member in self._listening:
                continue
            self._listening.add(member)
            await self.report(session_id, "listening", member=member, start_id="start-1")
        if not pickups:
            return
        for offer in list(self.offers):
            for contract in offer["contracts"]:
                member = contract["persona"]["alias"]
                if (offer["offer_id"], member) in self._taken:
                    continue
                self._taken.add((offer["offer_id"], member))
                await self.report(
                    members[member],
                    "picked_up",
                    member=member,
                    offer_id=offer["offer_id"],
                    activation_id=str(activation_id(offer, members[member])),
                    attempt=offer["attempt"],
                )

    async def open_step(self, member: str | None = None) -> WorkflowStep:
        """The one step a member holds open right now."""
        held = [
            step
            for step in (await self.state()).steps
            if step.is_open
            and step.kind is not StepKind.OFFER
            and step.member is not None
            and (member is None or step.member == member)
        ]
        assert len(held) == 1, [(s.node_id, s.member, s.state.value) for s in held]
        return held[0]

    def closed_report(self, step: WorkflowStep, ending: dict) -> dict:
        return {
            "offer_id": step.offer_id,
            "activation_id": str(step.id),
            "attempt": step.attempt,
            "ending": ending,
        }

    async def harness_closes(
        self, ending: dict, *, member: str | None = None, session_id: str | None = None
    ) -> None:
        """The member closes the step it holds, as Skuld would report it; the others then play."""
        step = await self.open_step(member)
        await self.report(
            session_id or step.session_id or "", "closed", **self.closed_report(step, ending)
        )
        await self.play()

    async def harness_says(self, verdict: str, *, member: str | None = None, **fields) -> None:
        await self.harness_closes({"kind": "verdict", "verdict": verdict, **fields}, member=member)

    def frame(self, step: dict) -> dict:
        """A ``workflow_step`` record of a session's durable log, as Skuld writes it."""
        return {"type": "workflow_step", "step": {"run_id": str(self.campaign.id), **step}}

    def passed_frame(self, offer: dict, member: str, reason: str = "filters") -> dict:
        return {
            "type": "workflow_step",
            "passed": {
                "offer_id": offer["offer_id"],
                "run_id": str(self.campaign.id),
                "node_id": offer["node_id"],
                "member": member,
                "reason": reason,
            },
        }

    def pickup_frame(
        self, offer: dict, member: str, session_id: str, *, state: str = "active", **extra
    ) -> dict:
        return self.frame(
            {
                "activation_id": str(activation_id(offer, session_id)),
                "attempt": offer["attempt"],
                "offer_id": offer["offer_id"],
                "member": member,
                "state": state,
                **extra,
            }
        )


def activation_id(offer: dict, session_id: str) -> UUID:
    return minted_activation_id(offer["offer_id"], session_id, offer["attempt"])


@pytest.fixture
def harness() -> Harness:
    return Harness()


async def test_a_run_goes_from_launch_through_a_rework_and_a_gate_to_a_completed_campaign(
    harness: Harness,
) -> None:
    # -- launch: one unit, a root and a session per member, and the first step offered.
    await harness.launch()

    [room] = harness.forge.run_room_requests
    assert room.hosts_members is True
    coder_request, reviewer_request = harness.forge.create_requests
    assert (coder_request.member, reviewer_request.member) == ("coder", "reviewer")
    assert coder_request.hosted_by == reviewer_request.hosted_by
    assert coder_request.persona_definition == _snapshot()["persona_definitions"]["coder"]
    assert coder_request.tracker_issue_id == "workflow:add-endpoint-0123456789ab"
    assert coder_request.model == "claude-sonnet-4-6"
    # The reviewer's stage names no model, so it runs on the launch's.
    assert reviewer_request.model == "claude-opus-launch"
    members = await harness.members()
    assert set(members) == {"coder", "reviewer"}
    [offer] = harness.offers
    assert offer["node_id"] == "implement" and offer["topic"] == "work.requested"
    [contract] = offer["contracts"]
    assert contract["persona"]["alias"] == "coder"
    assert contract["valid_verdicts"] == ["implemented"]
    assert contract["outcome_schema"] == {
        "summary": {"type": "string", "description": "what you did"}
    }
    assert "add an endpoint" in offer["instructions"]
    [(root_session, _)] = harness.forge.room_offers
    assert root_session == (await harness.state()).events[1].payload["root_id"]
    assert harness.current.status is WorkflowCampaignStatus.RUNNING
    assert harness.current.active_stage_id == "implement"
    assert harness.current.session_id == ""

    # -- the coder implements: the reviewer is offered the coder's outcome, on its own session.
    await harness.harness_says("implemented", outcome={"summary": "first try"}, note="done")

    review_offer = harness.offer_for("review")
    assert "first try" in review_offer["instructions"]
    assert [c["persona"]["alias"] for c in review_offer["contracts"]] == ["reviewer"]
    reviewer_step = await harness.open_step("reviewer")
    assert reviewer_step.session_id == members["reviewer"] != members["coder"]
    assert harness.current.active_stage_id == "review"

    # -- the reviewer rejects: a rework. The coder is offered the work again, on its own session.
    await harness.harness_says("fail", note="the null case crashes")

    rework_offer = harness.offer_for("implement", attempt=2)
    assert (rework_offer["attempt"], rework_offer["generation"]) == (2, 1)
    assert "the null case crashes" in rework_offer["instructions"]
    assert "rework generation 1" in rework_offer["instructions"]
    assert (await harness.open_step("coder")).session_id == members["coder"]
    assert harness.current.active_stage_id == "implement"

    # -- the coder fixes it and the reviewer passes it on the same sessions: no new ones.
    await harness.harness_says("implemented", outcome={"summary": "fixed"})
    assert harness.offer_for("review", attempt=2)["attempt"] == 2
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
    assert set(harness.forge.stopped) == {root_session, *members.values()}
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
    await harness.play()

    offer = harness.offer_for("implement", attempt=2)
    assert offer["generation"] == 1
    assert "add a test" in offer["instructions"]
    assert (await harness.open_step("coder")).generation == 1
    assert harness.current.status is WorkflowCampaignStatus.RUNNING


async def test_a_close_from_a_session_that_does_not_own_the_step_is_fenced(
    harness: Harness, caplog
) -> None:
    await harness.launch()
    step = await harness.open_step("coder")
    before = await harness.state()

    with caplog.at_level("ERROR", logger="ting.domain.services.activity_subscriber"):
        await harness.report(
            (await harness.members())["reviewer"],
            "closed",
            **harness.closed_report(step, {"kind": "verdict", "verdict": "implemented"}),
        )

    # The ledger keeps a note of the rejection; no step moved.
    assert (await harness.state()).steps == before.steps
    assert any("was rejected" in record.getMessage() for record in caplog.records)


async def test_a_different_close_of_a_step_that_is_done_is_rejected_loudly(
    harness: Harness, caplog
) -> None:
    await harness.launch()
    step = await harness.open_step("coder")
    await harness.harness_says("implemented", outcome={"summary": "x"})
    before = await harness.state()

    # The same session closes the same activation again, but with another ending: not a repeat.
    with caplog.at_level("ERROR", logger="ting.domain.services.activity_subscriber"):
        await harness.report(
            step.session_id or "",
            "closed",
            **harness.closed_report(step, {"kind": "failed", "failure_reason": "second thoughts"}),
        )

    assert (await harness.state()).steps == before.steps
    assert (await harness.state()).step(step.id).state is StepState.DONE  # type: ignore[union-attr]
    assert any("was rejected" in record.getMessage() for record in caplog.records)


async def test_the_same_close_delivered_again_changes_nothing_and_is_not_rejected(
    harness: Harness, caplog
) -> None:
    await harness.launch()
    step = await harness.open_step("coder")
    ending = {"kind": "verdict", "verdict": "implemented", "outcome": {"summary": "x"}}

    with caplog.at_level("ERROR", logger="ting.domain.services.activity_subscriber"):
        await harness.harness_closes(ending)
        state = await harness.state()
        offers = len(harness.forge.room_offers)
        for _ in range(3):
            await harness.report(
                step.session_id or "", "closed", **harness.closed_report(step, ending)
            )

    assert len(harness.forge.room_offers) == offers
    assert await harness.state() == state
    assert not any("was rejected" in record.getMessage() for record in caplog.records)


async def test_a_pickup_and_a_listening_delivered_again_change_nothing(harness: Harness) -> None:
    await harness.launch()
    state = await harness.state()
    members = await harness.members()
    step = await harness.open_step("coder")

    await harness.report(members["coder"], "listening", member="coder", start_id="start-1")
    await harness.report(
        members["coder"],
        "picked_up",
        member="coder",
        offer_id=step.offer_id,
        activation_id=str(step.id),
        attempt=step.attempt,
    )

    assert await harness.state() == state


async def test_a_verdict_the_persona_does_not_declare_changes_nothing(
    harness: Harness, caplog
) -> None:
    await harness.launch()
    before = await harness.state()

    with caplog.at_level("ERROR", logger="ting.domain.services.activity_subscriber"):
        await harness.harness_says("approved-by-me")

    assert (await harness.state()).steps == before.steps
    assert [step.node_id for step in before.open_steps() if step.member] == ["implement"]
    assert any("was rejected" in record.getMessage() for record in caplog.records)


async def test_a_unit_that_cannot_be_created_stops_the_launch_and_leaves_nothing_recorded(
    harness: Harness,
) -> None:
    harness.forge.fail_create = RuntimeError("no capacity")

    with pytest.raises(RuntimeError, match="no capacity"):
        await harness.launch()

    assert not (await harness.state()).launched
    assert harness.forge.room_offers == []


async def test_an_offer_the_root_cannot_take_is_loud_counted_and_published_again(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(
        "ting.domain.services.workflow_step_engine.get_observability", lambda: recorder
    )
    harness.forge.fail_offer = RuntimeError("422: the room has no offer operation")

    with caplog.at_level(logging.ERROR, logger="ting.domain.services.workflow_step_engine"):
        await harness.launch()

    assert harness.offers == []
    assert recorder.total("ting_workflow_offer_publish_failures_total") >= 1
    assert any(
        "Could not publish offer" in r.getMessage() and "no offer operation" in r.getMessage()
        for r in caplog.records
    )
    assert harness.current.active_stage_id == "implement"

    harness.forge.fail_offer = None
    assert await harness.engine.publish_ready_offers(harness.campaign.id) == 1
    await harness.play()

    assert [offer["node_id"] for offer in harness.offers] == ["implement"]
    assert (await harness.open_step("coder")).node_id == "implement"


async def test_a_member_that_refuses_an_offer_is_counted_and_the_offer_stays_outstanding(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    recorder = Recorder()
    monkeypatch.setattr(
        "ting.domain.services.workflow_step_engine.get_observability", lambda: recorder
    )
    await harness.launch(pickups=False)
    [offer] = harness.offers
    members = await harness.members()

    with caplog.at_level(logging.ERROR, logger="ting.domain.services.workflow_step_engine"):
        await harness.report(
            members["coder"],
            "offer_refused",
            member="coder",
            offer_id=offer["offer_id"],
            reason="busy",
            detail="another step is open",
        )

    assert recorder.counted("ting_workflow_offer_refusals_total", reason="busy") == 1
    assert any("refused offer" in r.getMessage() for r in caplog.records)
    [row] = [s for s in (await harness.state()).steps if s.kind is StepKind.OFFER]
    assert row.state is StepState.ACTIVE
    assert harness.current.status is WorkflowCampaignStatus.RUNNING


async def test_an_offer_published_report_from_the_root_repeats_what_ting_recorded(
    harness: Harness,
) -> None:
    await harness.launch(pickups=False)
    [offer] = harness.offers
    state = await harness.state()
    root_id = unit_root(state)

    await harness.report(root_id, "offer_published", offer_id=offer["offer_id"])

    assert await harness.state() == state


async def test_an_offer_published_report_from_a_member_is_rejected_loudly(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    await harness.launch(pickups=False)
    [offer] = harness.offers
    state = await harness.state()

    with caplog.at_level(logging.ERROR, logger="ting.domain.services.activity_subscriber"):
        await harness.report(
            (await harness.members())["coder"], "offer_published", offer_id=offer["offer_id"]
        )

    assert await harness.state() == state
    assert any("was rejected" in r.getMessage() for r in caplog.records)


async def test_a_session_that_ends_its_turn_without_closing_leaves_the_step_open_and_counted(
    harness: Harness,
) -> None:
    await harness.launch()
    step = await harness.open_step("coder")

    await harness.report(
        step.session_id or "",
        "unclosed",
        activation_id=str(step.id),
        attempt=step.attempt,
    )

    state = await harness.state()
    assert [s.state for s in state.steps if s.node_id == "implement" and s.member] == [
        StepState.ACTIVE
    ]
    [unclosed] = [e for e in state.events if e.event_type == "engine.step.unclosed"]
    assert unclosed.payload["session_id"] == step.session_id


async def test_cancelling_the_run_stops_its_sessions_and_cancels_the_campaign(
    harness: Harness,
) -> None:
    await harness.launch()
    await harness.harness_says("implemented")
    members = await harness.members()

    await harness.runs.cancel(harness.current, "the owner changed their mind")

    assert harness.current.status is WorkflowCampaignStatus.CANCELED
    assert harness.current.metadata["cancel_reason"] == "the owner changed their mind"
    assert "failure_error" not in harness.current.metadata
    assert harness.current.metadata["run_canceled"] is True
    assert members["reviewer"] in harness.forge.stopped
    assert len(harness.forge.stopped) == 3  # the root and both members


# -- runs still open on the old HTTP step path are canceled at the upgrade ----------------


def _as_launched_before_the_mesh(harness: Harness) -> None:
    """Make the run look as the old engine left it: launched, with no unit recorded."""
    run_id = harness.campaign.id
    harness.steps.events[run_id] = [
        event for event in harness.steps.events[run_id] if event.event_type != EVENT_UNIT_LAUNCHED
    ]


async def test_a_run_launched_before_the_mesh_is_canceled_at_the_upgrade_with_its_reason(
    harness: Harness,
) -> None:
    await harness.launch()
    _as_launched_before_the_mesh(harness)

    canceled = await harness.runs.cancel_runs_open_before_mesh()

    assert canceled == [harness.campaign.id]
    assert harness.current.status is WorkflowCampaignStatus.CANCELED
    assert (
        harness.current.metadata["cancel_reason"]
        == "canceled at upgrade: step handoff moved to the mesh"
    )
    assert (await harness.state()).finished
    assert len(harness.forge.stopped) == 3  # the root and both members


async def test_the_upgrade_cancel_leaves_mesh_runs_finished_runs_and_unlaunched_runs_alone(
    harness: Harness,
) -> None:
    await harness.launch()
    assert await harness.runs.cancel_runs_open_before_mesh() == []
    assert harness.current.status is not WorkflowCampaignStatus.CANCELED

    await harness.runs.cancel(harness.current, "stop")
    _as_launched_before_the_mesh(harness)
    assert await harness.runs.cancel_runs_open_before_mesh() == []

    unlaunched = replace(harness.campaign, id=uuid4(), slug="not-launched")
    await harness.campaigns.save_campaign(unlaunched)
    assert await harness.runs.cancel_runs_open_before_mesh() == []
    assert not (await harness.steps.load(unlaunched.id)).launched


async def test_a_run_the_upgrade_cannot_cancel_is_loud_counted_and_the_others_are_canceled(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await harness.launch()
    _as_launched_before_the_mesh(harness)
    broken = replace(harness.campaign, id=uuid4(), slug="broken-run")
    harness.campaigns.items = {broken.id: broken, **harness.campaigns.items}
    counted: list[str] = []
    monkeypatch.setattr(
        "ting.domain.services.workflow_runs.get_observability",
        lambda: SimpleNamespace(count=lambda name, **_: counted.append(name)),
    )
    real = harness.steps.load

    async def load(run_id: UUID) -> RunState:
        if run_id == broken.id:
            raise ConnectionError("database is down")
        return await real(run_id)

    monkeypatch.setattr(harness.steps, "load", load)

    with caplog.at_level(logging.ERROR):
        canceled = await harness.runs.cancel_runs_open_before_mesh()

    assert canceled == [harness.campaign.id]
    assert counted == ["ting_workflow_run_upgrade_cancel_errors_total"]
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "broken-run" in record.getMessage()
    assert "ConnectionError: database is down" in record.getMessage()
    assert "tried again at the next start" in record.getMessage()


# -- catching up with the sessions' durable logs after the stream was down ---------------


async def test_reconnecting_replays_a_pickup_and_a_close_the_stream_missed(
    harness: Harness,
) -> None:
    await harness.launch(pickups=False)
    [offer] = harness.offers
    members = await harness.members()
    harness.forge.log_entry(
        members["coder"],
        harness.pickup_frame(
            offer,
            "coder",
            members["coder"],
            state="closed",
            ending={"kind": "verdict", "verdict": "implemented", "outcome": {"summary": "x"}},
        ),
    )

    await harness.runs.reconcile_owner(OWNER)

    assert harness.current.active_stage_id == "review"
    assert harness.offer_for("review")["node_id"] == "review"
    step = (await harness.state()).step(activation_id(offer, members["coder"]))
    assert step is not None and step.state is StepState.DONE and step.verdict == "implemented"


async def test_reconnecting_leaves_a_step_the_session_still_holds_open(harness: Harness) -> None:
    await harness.launch(pickups=False)
    [offer] = harness.offers
    members = await harness.members()
    harness.forge.log_entry(
        members["coder"], harness.pickup_frame(offer, "coder", members["coder"], state="active")
    )

    await harness.runs.reconcile_owner(OWNER)

    assert (await harness.open_step("coder")).node_id == "implement"
    assert [o["node_id"] for o in harness.offers] == ["implement"]
    assert harness.current.active_stage_id == "implement"


async def test_reconnecting_replays_a_waiting_frame_then_the_close_after_it(
    harness: Harness,
) -> None:
    await harness.launch(pickups=False)
    [offer] = harness.offers
    members = await harness.members()
    waiting = {
        "kind": "waiting",
        "note": "needs the design",
        "waiting_on": {"kind": "person", "ref": "alice", "reason": "which shape?"},
    }
    harness.forge.log_entry(
        members["coder"],
        harness.pickup_frame(offer, "coder", members["coder"], state="waiting", ending=waiting),
    )

    await harness.runs.reconcile_owner(OWNER)

    assert harness.current.status is WorkflowCampaignStatus.BLOCKED
    assert (await harness.open_step("coder")).state is StepState.WAITING

    harness.forge.log_entry(
        members["coder"],
        harness.pickup_frame(
            offer,
            "coder",
            members["coder"],
            state="closed",
            ending={"kind": "verdict", "verdict": "implemented"},
        ),
    )
    await harness.runs.reconcile_owner(OWNER)

    assert harness.current.active_stage_id == "review"


async def test_a_stale_waiting_frame_of_a_step_that_moved_on_is_skipped(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    await harness.launch()
    [offer] = [o for o in harness.offers if o["node_id"] == "implement"]
    members = await harness.members()
    await harness.harness_says("implemented", outcome={"summary": "x"})
    state = await harness.state()
    harness.forge.log_entry(
        members["coder"],
        harness.pickup_frame(
            offer,
            "coder",
            members["coder"],
            state="waiting",
            ending={
                "kind": "waiting",
                "waiting_on": {"kind": "person", "ref": "alice"},
            },
        ),
    )
    harness.restart_ting()

    with caplog.at_level(logging.ERROR):
        await harness.runs.reconcile_owner(OWNER)

    assert await harness.state() == state
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_replaying_the_same_log_twice_changes_nothing(harness: Harness) -> None:
    await harness.launch(pickups=False)
    [offer] = harness.offers
    members = await harness.members()
    harness.forge.log_entry(
        members["coder"],
        harness.pickup_frame(
            offer,
            "coder",
            members["coder"],
            state="closed",
            ending={"kind": "verdict", "verdict": "implemented"},
        ),
    )
    await harness.runs.reconcile_owner(OWNER)
    state = await harness.state()
    published = len(harness.forge.room_offers)

    harness.restart_ting()  # a new process reads the whole log again
    await harness.runs.reconcile_owner(OWNER)

    assert await harness.state() == state
    assert len(harness.forge.room_offers) == published


async def test_reconnecting_replays_a_pass_the_stream_missed(harness: Harness) -> None:
    await harness.launch(pickups=False)
    [offer] = harness.offers
    members = await harness.members()
    harness.forge.log_entry(members["coder"], harness.passed_frame(offer, "coder", "not mine"))

    await harness.runs.reconcile_owner(OWNER)

    [passed] = [e for e in (await harness.state()).events if e.event_type == EVENT_OFFER_PASSED]
    assert (passed.payload["member"], passed.payload["reason"]) == ("coder", "not mine")
    # The only member the offer was addressed to passed it: the run fails, saying why.
    assert harness.current.status is WorkflowCampaignStatus.FAILED


async def test_the_catch_up_reads_each_page_and_remembers_how_far_it_got() -> None:
    harness = Harness(catch_up_page_size=2)
    await harness.launch(pickups=False)
    [offer] = harness.offers
    members = await harness.members()
    coder = members["coder"]
    for _ in range(3):
        harness.forge.log_entry(coder, {"type": "message"}, kind="message")
    harness.forge.log_entry(coder, harness.pickup_frame(offer, "coder", coder, state="active"))

    await harness.runs.reconcile_owner(OWNER)

    reads = [read for read in harness.forge.log_reads if read[0] == coder]
    assert reads == [(coder, 0, 2), (coder, 2, 2)]
    assert (await harness.open_step("coder")).node_id == "implement"

    harness.forge.log_reads.clear()
    await harness.runs.reconcile_owner(OWNER)

    assert [read for read in harness.forge.log_reads if read[0] == coder] == [(coder, 4, 2)]


async def test_only_workflow_step_records_of_a_log_are_applied(harness: Harness) -> None:
    await harness.launch(pickups=False)
    [offer] = harness.offers
    members = await harness.members()
    harness.forge.log_entry(
        members["coder"],
        harness.pickup_frame(offer, "coder", members["coder"]),
        kind="message",
    )

    await harness.runs.reconcile_owner(OWNER)

    assert not [s for s in (await harness.state()).steps if s.member]


async def test_a_frame_the_run_refuses_is_logged_at_error_and_the_next_one_still_applies(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    await harness.launch(pickups=False)
    [offer] = harness.offers
    members = await harness.members()
    unknown = {**offer, "offer_id": str(uuid4())}
    harness.forge.log_entry(
        members["coder"], harness.pickup_frame(unknown, "coder", members["coder"])
    )
    harness.forge.log_entry(
        members["coder"], harness.pickup_frame(offer, "coder", members["coder"])
    )

    with caplog.at_level(logging.ERROR, logger="ting.domain.services.workflow_runs"):
        await harness.runs.reconcile_owner(OWNER)

    assert any("was refused" in r.getMessage() for r in caplog.records)
    assert (await harness.open_step("coder")).offer_id == offer["offer_id"]


async def test_a_malformed_frame_is_logged_at_error_and_counted(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    await harness.launch(pickups=False)
    members = await harness.members()
    harness.forge.log_entry(
        members["coder"], harness.frame({"member": "coder", "state": "active", "attempt": 1})
    )
    counted: list[str] = []
    monkeypatch.setattr(
        "ting.domain.services.workflow_runs.get_observability",
        lambda: SimpleNamespace(count=lambda name, **_: counted.append(name)),
    )

    with caplog.at_level(logging.ERROR, logger="ting.domain.services.workflow_runs"):
        await harness.runs.reconcile_owner(OWNER)

    assert counted == ["ting_workflow_step_reports_invalid_total"]
    assert any("malformed workflow_step frame" in r.getMessage() for r in caplog.records)


async def test_a_log_that_has_more_but_does_not_advance_is_an_error(harness: Harness) -> None:
    await harness.launch(pickups=False)
    members = await harness.members()
    harness.forge.stalled_logs.add(members["coder"])

    with pytest.raises(RuntimeError, match="did not advance"):
        await harness.runs.catch_up_run(harness.current)


async def test_a_run_whose_log_cannot_be_read_is_loud_and_counted_not_skipped(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await harness.launch(pickups=False)
    harness.forge.fail_log = ConnectionError("forge is down")
    counted: list[str] = []
    monkeypatch.setattr(
        "ting.domain.services.workflow_runs.get_observability",
        lambda: SimpleNamespace(count=lambda name, **_: counted.append(name)),
    )

    with caplog.at_level(logging.ERROR):
        await harness.runs.reconcile_owner(OWNER)

    assert counted == ["ting_workflow_run_reconcile_errors_total"]
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "add-endpoint-0123456789ab" in record.getMessage()
    assert "ConnectionError: forge is down" in record.getMessage()
    assert record.exc_info is not None


async def test_a_run_that_cannot_be_caught_up_is_loud_and_the_others_still_are(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await harness.launch(pickups=False)
    [offer] = harness.offers
    members = await harness.members()
    harness.forge.log_entry(
        members["coder"],
        harness.pickup_frame(
            offer,
            "coder",
            members["coder"],
            state="closed",
            ending={"kind": "verdict", "verdict": "implemented"},
        ),
    )
    broken = replace(
        harness.campaign, id=uuid4(), slug="broken-run", created_at=NOW - timedelta(days=1)
    )
    # Listed first: the broken run is caught up before the healthy one.
    harness.campaigns.items = {broken.id: broken, **harness.campaigns.items}
    counted: list[str] = []
    monkeypatch.setattr(
        "ting.domain.services.workflow_runs.get_observability",
        lambda: SimpleNamespace(count=lambda name, **_: counted.append(name)),
    )
    real = harness.runs.catch_up_run

    async def catch_up(campaign: WorkflowCampaign) -> None:
        if campaign.id == broken.id:
            raise ConnectionError("forge is down")
        await real(campaign)

    monkeypatch.setattr(harness.runs, "catch_up_run", catch_up)

    with caplog.at_level(logging.ERROR):
        await harness.runs.reconcile_owner(OWNER)

    assert counted == ["ting_workflow_run_reconcile_errors_total"]
    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "broken-run" in record.getMessage()
    assert "ConnectionError: forge is down" in record.getMessage()
    assert record.exc_info is not None
    # The healthy run behind it was still caught up.
    assert harness.current.active_stage_id == "review"


async def test_a_run_whose_forge_no_longer_resolves_is_loud_not_skipped(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    await harness.launch(pickups=False)
    harness.factory.forge = None

    with caplog.at_level(logging.ERROR):
        await harness.runs.reconcile_owner(OWNER)

    [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert "no Forge connection resolves" in record.getMessage()


async def test_the_catch_up_ignores_other_owners_runs_and_runs_that_are_not_running(
    harness: Harness,
) -> None:
    await harness.launch(pickups=False)

    await harness.runs.reconcile_owner("someone-else")
    assert harness.forge.log_reads == []

    await harness.runs.cancel(harness.current, "stop")
    harness.forge.log_reads.clear()
    await harness.runs.catch_up_run(harness.current)
    assert harness.forge.log_reads == []


async def test_a_run_that_was_not_launched_has_nothing_to_catch_up_with(harness: Harness) -> None:
    await harness.campaigns.save_campaign(harness.campaign)

    await harness.runs.reconcile_owner(OWNER)

    assert harness.forge.log_reads == []


def test_a_catch_up_page_must_hold_at_least_one_frame(harness: Harness) -> None:
    with pytest.raises(ValueError, match="catch_up_page_size"):
        WorkflowRuns(
            engine=harness.engine,
            steps=harness.steps,
            campaigns=harness.campaigns,  # type: ignore[arg-type]
            sink=harness.projector,
            volundr_factory=harness.factory,  # type: ignore[arg-type]
            catch_up_page_size=0,
        )


async def test_a_close_that_names_another_runs_activation_is_fenced(harness: Harness) -> None:
    await harness.launch()
    step = await harness.open_step("coder")

    with pytest.raises(StepFencingError, match="belongs to run"):
        await harness.runs.record_step_closed(
            run_id=uuid4(),
            activation_id=step.id,
            session_id=step.session_id or "",
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
    step = await harness.open_step("coder")
    assert step.state is StepState.WAITING
    assert (step.waiting_on.kind, step.waiting_on.ref) == (WaitingOnKind.PERSON, "alice")
    assert "which API shape?" in step.note

    await harness.harness_says("implemented", outcome={"summary": "after the answer"})

    assert harness.current.status is WorkflowCampaignStatus.RUNNING
    assert harness.offer_for("review")["contracts"][0]["persona"]["alias"] == "reviewer"


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
    step = await harness.open_step("coder")

    with pytest.raises(StepFencingError, match="does not own the step"):
        await harness.runs.record_step_unclosed(
            run_id=harness.campaign.id,
            activation_id=step.id,
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
    reviewer_session = (await harness.members())["reviewer"]
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
