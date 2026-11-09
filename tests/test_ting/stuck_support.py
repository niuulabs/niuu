"""A run on the real step engine, run service and sweep, with fake sessions and a clock.

The engine, the run service, the campaign projector and the stuck-step sweep are real; the
ledger storage, the run's unit of sessions, the offer signer and the campaign repository are
in memory (``step_support``), and time is a clock the test moves. A Forge stand-in serves the
durable session logs the sweep reads back (``LogForge``). The workflow is
``go -> make -> done`` where ``make`` is a parallel stage of two members (``alpha`` and
``bravo``) that take the same work and each hand off ``work.item``, so one pickup can be
stuck while the other carries on.

``harness.run`` plays the members the way their Skuld does (listening, then picking up every
offer published); set ``harness.run.auto = False``, ``passers`` or ``deaf`` before
``launch`` to play them otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from niuu.domain.workflow_step import WORKFLOW_STEP_FRAME
from ting.adapters.campaign_step_definitions import (
    CampaignStepRunDefinitions,
    definition_of_snapshot,
)
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
    StepKind,
    StepState,
    WorkflowStep,
)
from ting.ports.event_bus import TingEvent
from ting.ports.volundr import PublicSessionLogEntry, PublicSessionLogPage

from .graph_builders import edge, end, stage, trigger
from .step_support import FakeSessions, FakeSigner, InMemoryStepRepository, Run

OWNER = "user-1"
SLUG = "add-endpoint-0123456789ab"
START = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)
STEP_DEADLINE_SECONDS = 600.0
PICKUP_GRACE_SECONDS = 120.0
SWEEP_INTERVAL_SECONDS = 60.0
CATCH_UP_PAGE_SIZE = 50


class Clock:
    """Time the test moves; every service reads it."""

    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class LogForge:
    """The slice of a Forge the run service reads: each session's durable log, in pages."""

    def __init__(self) -> None:
        self.logs: dict[str, list[PublicSessionLogEntry]] = {}
        #: Sessions whose log cannot be read (Forge does not answer for them).
        self.unreachable: set[str] = set()
        self.reads: list[tuple[str, int]] = []

    def append(self, session_id: str, frame: dict[str, Any]) -> None:
        """A ``workflow_step`` frame Skuld wrote into the session's durable log."""
        entries = self.logs.setdefault(session_id, [])
        entries.append(
            PublicSessionLogEntry(
                session_id=session_id,
                seq=len(entries) + 1,
                kind=WORKFLOW_STEP_FRAME,
                payload=frame,
                ts=START,
            )
        )

    async def get_public_session_log_page(
        self, session_id: str, *, after: int, limit: int, auth_token=None, principal=None
    ) -> PublicSessionLogPage:
        if session_id in self.unreachable:
            raise ConnectionError(f"Forge does not answer for session {session_id}")
        self.reads.append((session_id, after))
        pending = [entry for entry in self.logs.get(session_id, []) if entry.seq > after]
        page = pending[:limit]
        scanned = page[-1].seq if page else after
        return PublicSessionLogPage(
            entries=tuple(page), scanned_through=scanned, has_more=len(pending) > limit
        )


class ForgeFactory:
    """Resolves every owner's connection to the one ``LogForge``."""

    def __init__(self, forge: LogForge) -> None:
        self._forge = forge

    async def for_connection(self, owner_id: str, connection_id: str) -> LogForge:
        return self._forge

    async def primary_for_owner(self, owner_id: str) -> LogForge:
        return self._forge


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
        self.forge = LogForge()
        self.campaigns = Campaigns()
        self.steps = InMemoryStepRepository()
        self.bus = InMemoryEventBus()
        self.sessions = FakeSessions()
        self.signer = FakeSigner()
        self.campaign = make_campaign()
        self.definitions = CampaignStepRunDefinitions(self.campaigns)  # type: ignore[arg-type]
        factory = ForgeFactory(self.forge)
        self.projector = WorkflowCampaignProjector(
            repo=self.campaigns,  # type: ignore[arg-type]
            event_bus=self.bus,
            run_launched=self._launched,
        )
        self.engine = WorkflowStepEngine(
            repository=self.steps,
            sessions=self.sessions,
            offers=self.signer,
            definitions=self.definitions,
            settings=EngineSettings(step_deadline_seconds=STEP_DEADLINE_SECONDS),
            clock=self.clock,
        )
        self.runs = WorkflowRuns(
            engine=self.engine,
            steps=self.steps,
            campaigns=self.campaigns,  # type: ignore[arg-type]
            sink=self.projector,
            volundr_factory=factory,  # type: ignore[arg-type]
            catch_up_page_size=CATCH_UP_PAGE_SIZE,
        )
        self.sweeper = StuckStepSweeper(
            engine=self.engine,
            runs=self.runs,
            steps=self.steps,
            campaigns=self.campaigns,  # type: ignore[arg-type]
            sessions=self.sessions,
            definitions=self.definitions,
            event_bus=self.bus,
            settings=StuckSettings(pickup_grace_seconds=PICKUP_GRACE_SECONDS),
            interval_seconds=SWEEP_INTERVAL_SECONDS,
            clock=self.clock,
        )
        #: The members, played the way their Skuld plays them.
        self.run = Run(
            engine=self.engine,
            repository=self.steps,
            sessions=self.sessions,
            run_id=self.campaign.id,
            definition=definition_of_snapshot(
                self.campaign.workflow_snapshot, run_id=self.campaign.id
            ),
            clock=[self.clock.now],
            signer=self.signer,
        )

    async def _launched(self, run_id: UUID) -> bool:
        return (await self.steps.load(run_id)).launched

    # -- driving ---------------------------------------------------------------

    async def launch(self) -> None:
        """Launch the run; the members then listen and take up what is offered."""
        await self.campaigns.save_campaign(self.campaign)
        await self.runs.launch(self.campaign, {"prompt": "add an endpoint"})
        await self.play()

    async def play(self) -> RunState:
        """Let the members answer what has been published, then project the run."""
        await self.run.pump()
        return await self.runs.refresh(self.campaign.id)

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
        """The member's pickup (the newest attempt unless one is named)."""
        found = [
            step
            for step in self.state.steps
            if step.kind is StepKind.STAGE_MEMBER
            and step.member == member
            and (attempt is None or step.attempt == attempt)
        ]
        assert found, f"no step for {member}"
        return max(found, key=lambda step: step.attempt)

    def offers(self, node_id: str = "make") -> list[WorkflowStep]:
        """The ledger rows of the node's offers, oldest first."""
        return self.run.offer_rows(node_id)

    def offer(self, node_id: str = "make", attempt: int | None = None) -> WorkflowStep:
        found = [o for o in self.offers(node_id) if attempt is None or o.attempt == attempt]
        assert found, f"no offer at {node_id}"
        return max(found, key=lambda offer: offer.attempt)

    def published(self) -> list[dict[str, Any]]:
        """The ``step.offered`` payload of every publication, in order."""
        return self.run.offers

    def flags(self, *, live: bool = True) -> list[StepFlag]:
        return list(self.state.live_flags() if live else self.state.flags)

    def kinds(self) -> list[str]:
        return sorted(flag.kind.value for flag in self.flags())

    def events(self, event_type: str) -> list[RunEvent]:
        return [e for e in self.state.events if e.event_type == event_type]

    def notifications(self) -> list[TingEvent]:
        return [e for e in self.bus.get_log(1000) if e.event == "workflow.step_flagged"]

    def session_of(self, member: str) -> str:
        return self.sessions.units[self.campaign.id].members[member]

    def with_state(self, step: WorkflowStep, state: StepState, **changes: Any) -> None:
        """Rewrite a step in the ledger, to stage a state the engine reached some other way."""
        self.steps.steps[step.id] = step.with_state(state, **changes)

    # -- what a session's durable log holds ---------------------------------------

    def log_pickup(self, offer_payload: dict[str, Any], member: str) -> UUID:
        """The member's Skuld wrote a pickup frame the activity stream never delivered."""
        activation = self.run.activation_id(offer_payload, member)
        self.forge.append(
            self.session_of(member),
            {
                "step": {
                    "run_id": str(self.campaign.id),
                    "activation_id": str(activation),
                    "attempt": offer_payload["attempt"],
                    "member": member,
                    "offer_id": offer_payload["offer_id"],
                    "state": "active",
                    "ending": None,
                }
            },
        )
        return activation

    def log_close(self, step: WorkflowStep, verdict: str = "ok") -> None:
        """The member's Skuld wrote the close of its step into its durable log."""
        self.forge.append(
            step.session_id or "",
            {
                "step": {
                    "run_id": str(self.campaign.id),
                    "activation_id": str(step.id),
                    "attempt": step.attempt,
                    "member": step.member,
                    "offer_id": step.offer_id,
                    "state": "closed",
                    "ending": {"kind": "verdict", "verdict": verdict, "outcome": {}},
                }
            },
        )

    def log_pass(self, offer_payload: dict[str, Any], member: str, reason: str = "filters") -> None:
        """The member's Skuld wrote that it passed the offer."""
        self.forge.append(
            self.session_of(member),
            {
                "passed": {
                    "run_id": str(self.campaign.id),
                    "member": member,
                    "offer_id": offer_payload["offer_id"],
                    "reason": reason,
                }
            },
        )
