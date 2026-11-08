"""A run on the real step engine, session adapter and sweep, with a fake Forge and a clock.

The engine, the run service, the Forge step-session adapter, the campaign projector and
the stuck-step sweep are real; the ledger storage, Forge and the campaign repository are
in memory, and time is a clock the test moves. The workflow is ``go -> make -> done``
where ``make`` is a parallel stage of two members (``alpha`` and ``bravo``) that each hand
off ``work.item``, so one branch can be stuck while the other carries on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from ting.adapters.campaign_step_definitions import CampaignStepRunDefinitions
from ting.adapters.forge_step_sessions import ForgeStepSessions
from ting.adapters.memory_event_bus import InMemoryEventBus
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.services.workflow_campaign_projector import WorkflowCampaignProjector
from ting.domain.services.workflow_runs import WorkflowRuns
from ting.domain.services.workflow_step_engine import WorkflowStepEngine
from ting.domain.services.workflow_step_watch import StuckStepSweeper
from ting.domain.step_engine import EngineSettings
from ting.domain.step_stuck import StuckSettings
from ting.domain.workflow_steps import (
    FlagKind,
    RunEvent,
    RunState,
    StepFlag,
    StepState,
    WorkflowStep,
)
from ting.ports.event_bus import TingEvent
from ting.ports.volundr import StepSessionRequest, VolundrSession

from .fake_forge import FakeForge, FakeForgeFactory, FakeSagas
from .graph_builders import edge, end, stage, trigger
from .step_support import InMemoryStepRepository

OWNER = "user-1"
SLUG = "add-endpoint-0123456789ab"
START = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
STEP_DEADLINE_SECONDS = 600.0
START_GRACE_SECONDS = 120.0
SWEEP_INTERVAL_SECONDS = 60.0


class Clock:
    """Time the test moves; every service reads it."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class KnowingForge(FakeForge):
    """A Forge whose sessions are running and know the steps delivered to them."""

    def __init__(self) -> None:
        super().__init__()
        self.forgotten: set[tuple[str, str]] = set()
        self.unreachable: set[str] = set()

    async def create_step_session(
        self, request: StepSessionRequest, *, auth_token=None, principal=None
    ):
        created = await super().create_step_session(request)
        self.set_status(created.id, "running")
        return created

    async def deliver_workflow_step(
        self, session_id, activation, *, auth_token=None, principal=None
    ):
        await super().deliver_workflow_step(session_id, activation)
        self.forgotten.discard((session_id, activation["activation_id"]))

    async def get_workflow_step(
        self, session_id, activation_id, *, auth_token=None, principal=None
    ):
        if session_id in self.unreachable:
            raise ConnectionError(f"session {session_id} does not answer")
        if (session_id, activation_id) in self.forgotten:
            return None
        explicit = self.step_states.get((session_id, activation_id))
        if explicit is not None:
            return explicit
        for delivered_to, body in self.deliveries:
            if delivered_to == session_id and body["activation_id"] == activation_id:
                return {
                    "activation_id": activation_id,
                    "run_id": body["run_id"],
                    "attempt": body["attempt"],
                    "state": "active",
                    "ending": None,
                }
        return None

    async def get_session(self, session_id, *, auth_token=None, principal=None):
        return self.sessions.get(session_id)

    def delete_session(self, session_id: str) -> None:
        del self.sessions[session_id]


class Campaigns:
    """The campaign repository, in memory."""

    def __init__(self) -> None:
        self.items: dict[UUID, WorkflowCampaign] = {}

    async def save_campaign(self, campaign: WorkflowCampaign) -> WorkflowCampaign:
        self.items[campaign.id] = campaign
        return campaign

    async def get_campaign(self, campaign_id: UUID) -> WorkflowCampaign | None:
        return self.items.get(campaign_id)

    async def get_campaign_by_slug(self, slug: str, *, owner_id: str) -> WorkflowCampaign | None:
        return next(
            (c for c in self.items.values() if c.slug == slug and c.owner_id == owner_id), None
        )

    async def list_active_campaigns(self) -> list[WorkflowCampaign]:
        return [
            c
            for c in self.items.values()
            if c.status not in {WorkflowCampaignStatus.COMPLETED, WorkflowCampaignStatus.FAILED}
        ]

    async def list_active_owner_ids(self) -> list[str]:
        return sorted({c.owner_id for c in await self.list_active_campaigns()})


def _document(alias: str, produces: dict[str, str], consumes: list[str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": alias,
        "revision": "r1",
        "definition": {
            "produces": {"event_type_map": produces},
            "consumes": {"event_types": consumes},
        },
    }


def make_snapshot() -> dict[str, Any]:
    """``go -> make (alpha, bravo in parallel) -> done``."""
    graph = {
        "nodes": [
            trigger("work.requested", "go"),
            stage(
                "make",
                [{"personaId": "alpha"}, {"personaId": "bravo"}],
                execution="parallel",
            ),
            end("done", joinMode="any"),
        ],
        "edges": [
            edge("e1", "go", "make", "work.requested"),
            edge("e2", "make", "done", "work.item"),
        ],
    }
    documents = {
        alias: _document(alias, {"ok": "work.item"}, ["work.requested"])
        for alias in ("alpha", "bravo")
    }
    return {
        "name": "Make things",
        "graph": graph,
        "persona_definitions": documents,
        "persona_dependencies": {
            alias: {"id": alias, "revision": "r1", "digest": "sha256:" + "0" * 64}
            for alias in documents
        },
    }


def make_campaign() -> WorkflowCampaign:
    return WorkflowCampaign(
        id=uuid4(),
        slug=SLUG,
        name="add-endpoint",
        owner_id=OWNER,
        workflow_id=uuid4(),
        workflow_version="1.0.0",
        workflow_name="Make things",
        workflow_snapshot=make_snapshot(),
        session_id="",
        session_name="",
        status=WorkflowCampaignStatus.PENDING,
        active_stage_id=None,
        stage_state=[],
        metadata={"repo": "https://github.com/niuulabs/volundr.git", "model": "claude-test"},
        created_at=START,
        updated_at=START,
        connection_id="cluster-1",
    )


class StuckHarness:
    """One run, its services and the sweep, on fakes and a clock."""

    def __init__(self) -> None:
        self.clock = Clock()
        self.forge = KnowingForge()
        self.campaigns = Campaigns()
        self.steps = InMemoryStepRepository()
        self.bus = InMemoryEventBus()
        factory = FakeForgeFactory(self.forge)
        self.sessions = ForgeStepSessions(
            volundr_factory=factory,  # type: ignore[arg-type]
            campaigns=self.campaigns,  # type: ignore[arg-type]
            sagas=FakeSagas(),
        )
        self.projector = WorkflowCampaignProjector(
            repo=self.campaigns,  # type: ignore[arg-type]
            event_bus=self.bus,
            run_launched=self._launched,
        )
        self.engine = WorkflowStepEngine(
            repository=self.steps,
            sessions=self.sessions,
            definitions=CampaignStepRunDefinitions(self.campaigns),  # type: ignore[arg-type]
            settings=EngineSettings(
                step_deadline_seconds=STEP_DEADLINE_SECONDS, session_poll_seconds=0.01
            ),
            clock=self.clock,
        )
        self.runs = WorkflowRuns(
            engine=self.engine,
            steps=self.steps,
            campaigns=self.campaigns,  # type: ignore[arg-type]
            sink=self.projector,
            volundr_factory=factory,  # type: ignore[arg-type]
        )
        self.sweeper = StuckStepSweeper(
            engine=self.engine,
            runs=self.runs,
            steps=self.steps,
            campaigns=self.campaigns,  # type: ignore[arg-type]
            sessions=self.sessions,
            event_bus=self.bus,
            settings=StuckSettings(start_grace_seconds=START_GRACE_SECONDS),
            interval_seconds=SWEEP_INTERVAL_SECONDS,
            clock=self.clock,
        )
        self.campaign = make_campaign()

    async def _launched(self, run_id: UUID) -> bool:
        return (await self.steps.load(run_id)).launched

    # -- driving ---------------------------------------------------------------

    async def launch(self) -> None:
        await self.campaigns.save_campaign(self.campaign)
        await self.runs.launch(self.campaign, {"prompt": "add an endpoint"})

    async def sweep(self) -> dict[FlagKind, int]:
        return await self.sweeper.sweep_once()

    def advance(self, seconds: float) -> None:
        self.clock.advance(seconds)

    # -- reading ---------------------------------------------------------------

    @property
    def state(self) -> RunState:
        return self.steps.snapshot(self.campaign.id)

    @property
    def current(self) -> WorkflowCampaign:
        return self.campaigns.items[self.campaign.id]

    def step(self, member: str, attempt: int | None = None) -> WorkflowStep:
        found = [
            step
            for step in self.state.steps
            if step.member == member and (attempt is None or step.attempt == attempt)
        ]
        assert found, f"no step for {member}"
        return found[-1]

    def flags(self, *, live: bool = True) -> list[StepFlag]:
        return list(self.state.live_flags() if live else self.state.flags)

    def kinds(self) -> list[str]:
        return sorted(flag.kind.value for flag in self.flags())

    def events(self, event_type: str) -> list[RunEvent]:
        return [e for e in self.state.events if e.event_type == event_type]

    def notifications(self) -> list[TingEvent]:
        return [e for e in self.bus.get_log(1000) if e.event == "workflow.step_flagged"]

    def session_of(self, member: str) -> str:
        return self.step(member).session_id or ""

    def session(self, session_id: str) -> VolundrSession:
        return self.forge.sessions[session_id]

    def deliveries_of(self, member: str) -> list[dict[str, Any]]:
        return [body for _, body in self.forge.deliveries if body["member"] == member]

    def with_state(self, step: WorkflowStep, state: StepState, **changes: Any) -> None:
        """Rewrite a step in the ledger, to stage a crash or a half-finished start."""
        self.steps.steps[step.id] = step.with_state(state, **changes)
