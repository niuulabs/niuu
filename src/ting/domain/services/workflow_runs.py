"""Runs of workflows on the step engine, and the campaigns that record them.

``WorkflowRuns`` is how the rest of Ting starts, closes, decides and cancels a run.
Each call goes through ``WorkflowStepEngine`` (the only writer of the step ledger)
and then projects the ledger it returns onto the run's campaign: status, active
stage, stage rail, open gates and how the run ended. A campaign is therefore never
told what happened; it is read off the ledger.

The campaign must exist before the run launches: the ledger's ``run_id`` is the
campaign id and the engine reads the run's definition from the campaign's pinned
snapshot.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from niuu.domain.workflow_step import WORKFLOW_STEP_FRAME
from niuu.observability import get_observability
from ting.domain.campaign_run_projection import RunProjection, open_gates, project_run, run_gates
from ting.domain.models import WorkflowCampaign
from ting.domain.services.gate_notifications import GateNotifier
from ting.domain.services.run_room_notices import RunRoomNotifier
from ting.domain.services.workflow_step_engine import (
    Recovered,
    StepRunRequest,
    WorkflowStepEngine,
)
from ting.domain.step_engine import unit_root, unit_sessions
from ting.domain.workflow_steps import (
    ActorKind,
    Ending,
    EndingKind,
    GateActor,
    GateDecision,
    HandoffRefusedError,
    InvalidEndingError,
    RunState,
    RunStateError,
    StepFencingError,
    StepKind,
    StepState,
    WaitingOn,
    WaitingOnKind,
    WorkflowStep,
)
from ting.ports.volundr import VolundrFactory
from ting.ports.workflow_campaign_repository import WorkflowCampaignRepository
from ting.ports.workflow_steps import WorkflowStepRepository

logger = logging.getLogger(__name__)


#: Why a run still open on the old HTTP step path is canceled at the upgrade. A run's steps
#: are offered on the mesh to the members of its unit; a run launched before has neither.
UPGRADE_CANCEL_REASON = "canceled at upgrade: step handoff moved to the mesh"


class RunProjectionSink(Protocol):
    """Where a run's projection is written to its campaign (the campaign projector)."""

    async def apply_run_projection(
        self, campaign: WorkflowCampaign, projection: RunProjection
    ) -> None: ...


def ending_of(report: Mapping[str, Any], *, now: datetime) -> Ending:
    """The engine's ``Ending`` for the ending a session reported when it closed a step.

    ``report`` is the ``ending`` object of the contract: ``kind``, ``verdict``,
    ``outcome``, ``note``, ``evidence`` (a list), ``waiting_on`` and
    ``failure_reason``. A malformed report raises ``InvalidEndingError``.
    """
    try:
        kind = EndingKind(str(report.get("kind") or ""))
    except ValueError as exc:
        valid = ", ".join(item.value for item in EndingKind)
        raise InvalidEndingError(
            f"ending kind {report.get('kind')!r} is not one of {valid}"
        ) from exc
    outcome = report.get("outcome") or {}
    if not isinstance(outcome, Mapping):
        raise InvalidEndingError("a close's outcome must be an object")
    note = str(report.get("note") or "")
    evidence = report.get("evidence")
    if evidence is not None and not isinstance(evidence, list):
        raise InvalidEndingError("a close's evidence must be a list")
    packed = {"items": list(evidence)} if evidence else {}
    if kind is EndingKind.FAILED:
        reason = str(report.get("failure_reason") or "").strip()
        if not reason:
            raise InvalidEndingError("a failed ending needs failure_reason")
        return Ending(EndingKind.FAILED, note=reason, evidence=packed, outcome=dict(outcome))
    if kind is EndingKind.WAITING:
        raw = report.get("waiting_on")
        if not isinstance(raw, Mapping):
            raise InvalidEndingError("a waiting ending needs waiting_on")
        try:
            waiting = WaitingOn(
                kind=WaitingOnKind(str(raw.get("kind") or "")),
                ref=str(raw.get("ref") or ""),
                since=now,
            )
        except ValueError as exc:
            raise InvalidEndingError(f"waiting_on.kind {raw.get('kind')!r} is not valid") from exc
        reason = str(raw.get("reason") or "").strip()
        if reason:
            note = f"{note}\nWaiting: {reason}".strip()
        return Ending(
            EndingKind.WAITING,
            note=note,
            evidence=packed,
            outcome=dict(outcome),
            waiting_on=waiting,
        )
    return Ending(
        kind,
        verdict=str(report.get("verdict") or "") or None,
        outcome=dict(outcome),
        note=note,
        evidence=packed,
    )


#: Frames asked of Forge's session log per request when catching up. It bounds nothing: the
#: catch-up reads pages until the log has no more. Matches ``workflow_steps.catch_up_page_size``.
DEFAULT_CATCH_UP_PAGE_SIZE = 200


class WorkflowRuns:
    """Starts, closes, decides and cancels runs, and keeps their campaigns in step."""

    def __init__(
        self,
        *,
        engine: WorkflowStepEngine,
        steps: WorkflowStepRepository,
        campaigns: WorkflowCampaignRepository,
        sink: RunProjectionSink,
        volundr_factory: VolundrFactory,
        gate_notifier: GateNotifier | None = None,
        room_notifier: RunRoomNotifier | None = None,
        catch_up_page_size: int = DEFAULT_CATCH_UP_PAGE_SIZE,
    ) -> None:
        if catch_up_page_size < 1:
            raise ValueError("catch_up_page_size must be at least 1")
        self._engine = engine
        self._steps = steps
        self._campaigns = campaigns
        self._sink = sink
        self._factory = volundr_factory
        self._gate_notifier = gate_notifier
        self._room_notifier = room_notifier
        self._page_size = catch_up_page_size
        #: Session id -> how far into its durable log this process has read (log sequence).
        self._log_cursors: dict[str, int] = {}

    async def launch(
        self,
        campaign: WorkflowCampaign,
        payload: dict[str, Any] | None = None,
        *,
        trigger_node_id: str | None = None,
    ) -> RunState:
        """Start the campaign's run: record its trigger and activate the first steps."""
        state = await self._engine.launch(
            StepRunRequest(
                run_id=campaign.id,
                payload=dict(payload or {}),
                trigger_node_id=trigger_node_id,
            )
        )
        await self._project(campaign.id, state)
        return state

    async def record_step_closed(
        self,
        *,
        run_id: UUID,
        activation_id: UUID,
        session_id: str,
        attempt: int,
        report: Mapping[str, Any],
    ) -> RunState:
        """Apply the close a session reported. ``session_id`` is the authenticated one.

        Raises ``StepFencingError`` when the activation is not this session's open
        step, and ``InvalidEndingError`` when the ending is not one the step allows;
        the engine has counted and logged both.
        """
        await self._require_run(run_id, activation_id)
        ending = ending_of(report, now=datetime.now(UTC))
        state = await self._engine.record_close(activation_id, session_id, attempt, ending)
        await self._project(run_id, state)
        return state

    async def record_step_unclosed(
        self,
        *,
        run_id: UUID,
        activation_id: UUID,
        session_id: str,
        attempt: int,
        turn: int | None = None,
    ) -> RunState:
        """Record that a session ended a turn (``turn``, when known) with the step still open."""
        await self._require_run(run_id, activation_id)
        return await self._engine.note_unclosed(activation_id, session_id, attempt, turn)

    async def record_listening(
        self, *, run_id: UUID, session_id: str, member: str, start_id: str
    ) -> RunState:
        """Record that a member of the run's unit listens; its waiting offers then go out."""
        state = await self._engine.note_listening(
            run_id=run_id, session_id=session_id, member=member, start_id=start_id
        )
        await self._project(run_id, state)
        return state

    async def record_offer_published(
        self, *, run_id: UUID, session_id: str, offer_id: UUID
    ) -> RunState:
        """Record that the run's root put an offer on the mesh."""
        state = await self._engine.note_offer_published(
            run_id=run_id, session_id=session_id, offer_id=offer_id
        )
        await self._project(run_id, state)
        return state

    async def record_picked_up(
        self,
        *,
        run_id: UUID,
        session_id: str,
        member: str,
        offer_id: UUID,
        activation_id: UUID,
        attempt: int,
    ) -> RunState:
        """Record a member's pickup of an offer as a step of its own."""
        state = await self._engine.note_picked_up(
            run_id=run_id,
            session_id=session_id,
            member=member,
            offer_id=offer_id,
            activation_id=activation_id,
            attempt=attempt,
        )
        await self._project(run_id, state)
        return state

    async def record_passed(
        self, *, run_id: UUID, session_id: str, member: str, offer_id: UUID, reason: str
    ) -> RunState:
        """Record that a member received an offer and passed it."""
        state = await self._engine.note_passed(
            run_id=run_id, session_id=session_id, member=member, offer_id=offer_id, reason=reason
        )
        await self._project(run_id, state)
        return state

    async def record_offer_refused(
        self,
        *,
        run_id: UUID,
        session_id: str,
        member: str,
        offer_id: str,
        reason: str,
        detail: str,
    ) -> RunState:
        """Record that a member's Skuld refused an offer."""
        state = await self._engine.note_offer_refused(
            run_id=run_id,
            session_id=session_id,
            member=member,
            offer_id=offer_id,
            reason=reason,
            detail=detail,
        )
        await self._project(run_id, state)
        return state

    async def resolve_gate(
        self,
        campaign: WorkflowCampaign,
        node_id: str,
        decision: GateDecision,
        *,
        by: GateActor,
        note: str = "",
    ) -> RunState:
        """Record a decision on the run's open gate at ``node_id``."""
        state = await self._engine.resolve_gate(campaign.id, node_id, decision, by, note)
        await self._project(campaign.id, state)
        return state

    async def cancel(
        self, campaign: WorkflowCampaign, reason: str, *, by: GateActor | None = None
    ) -> RunState:
        """Cancel the run: its open steps are canceled and its sessions stopped.

        With ``by``, the run's log records who aborted it, and why.
        """
        state = await self._engine.cancel(campaign.id, reason, by)
        await self._project(campaign.id, state)
        return state

    async def retry_step(
        self, campaign: WorkflowCampaign, activation_id: UUID, *, by: GateActor, reason: str
    ) -> Recovered:
        """Replace a failed or stuck step by a new attempt on the member's session."""
        recovered = await self._engine.retry_step(campaign.id, activation_id, by=by, reason=reason)
        await self._project(campaign.id, recovered.state)
        return recovered

    async def reassign_step(
        self, campaign: WorkflowCampaign, activation_id: UUID, *, by: GateActor, reason: str
    ) -> Recovered:
        """Move an open or failed step to a fresh session of the same member."""
        recovered = await self._engine.reassign_step(
            campaign.id, activation_id, by=by, reason=reason
        )
        await self._project(campaign.id, recovered.state)
        return recovered

    async def refresh(self, run_id: UUID) -> RunState:
        """Project the run's ledger onto its campaign again, and return it."""
        state = await self._steps.load(run_id)
        await self._project(run_id, state)
        return state

    async def apply_reported_close(
        self, run_id: UUID, step: WorkflowStep, reported: Mapping[str, Any] | None
    ) -> bool:
        """Record the ending a step's session holds for it, if it has one; True if applied.

        ``reported`` is the session's durable record of the step. A step it closed (or
        parked as waiting, when the ledger has it active) is recorded exactly as a
        close report from the activity stream would be, fenced the same way; a fencing or
        ending rejection has been logged and counted by the engine and applies nothing.
        """
        report = _closed_report(reported)
        if reported is None or report is None:
            return False
        if report.get("kind") == EndingKind.WAITING.value and step.state is not StepState.ACTIVE:
            # A waiting ending parks an active step; an older frame of one already parked or
            # closed is history, not a close to apply.
            return False
        try:
            await self.record_step_closed(
                run_id=run_id,
                activation_id=step.id,
                session_id=step.session_id or "",
                attempt=int(reported.get("attempt", step.attempt)),
                report=report,
            )
        except (StepFencingError, InvalidEndingError):
            return False
        return True

    async def close_wait(
        self,
        run_id: UUID,
        node_id: str,
        verdict: str,
        *,
        outcome: Mapping[str, Any],
        note: str = "",
    ) -> RunState:
        """Close the run's waiting ``wait`` or ``subworkflow`` step at ``node_id``.

        These steps have no session: whatever they wait on (a clock, an outside
        observation, a child execution) reports to Ting, and Ting closes the step with
        the verdict the engine defines for it. Closing a step that is already closed
        with this verdict changes nothing, so a redelivered observation is harmless;
        any other state is an error.
        """
        state = await self._steps.load(run_id)
        steps = [
            step for step in state.steps if step.node_id == node_id and step.kind is StepKind.WAIT
        ]
        if not steps:
            raise RunStateError(f"run {run_id} has no wait or subworkflow step at {node_id!r}")
        step = max(steps, key=lambda item: (item.created_at, str(item.id)))
        if step.state is StepState.DONE and step.verdict == verdict:
            return state
        closed = await self._engine.record_close(
            step.id,
            step.session_id,
            step.attempt,
            Ending.with_verdict(verdict, outcome=dict(outcome), note=note),
        )
        await self._project(run_id, closed)
        return closed

    async def state(self, run_id: UUID) -> RunState:
        """The run's ledger, as it is now."""
        return await self._steps.load(run_id)

    async def gates(self, campaign: WorkflowCampaign) -> list[dict[str, Any]]:
        """The run's gates, newest first, in the shape the board and the gate API read."""
        return run_gates(campaign, await self._steps.load(campaign.id))

    async def pending_gates(self, campaign: WorkflowCampaign) -> list[dict[str, Any]]:
        return open_gates(campaign, await self._steps.load(campaign.id))

    async def cancel_runs_open_before_mesh(self) -> list[UUID]:
        """Cancel the runs launched before step handoff moved to the mesh; returns their ids.

        A run launched by the mesh engine records its unit when it starts, and every
        workflow with stage members has one. A launched, unfinished run of such a workflow
        with no unit was launched by the old engine, whose steps were delivered to its
        sessions over HTTP; nothing can offer them on the mesh, so it is canceled with
        ``UPGRADE_CANCEL_REASON`` and its sessions are stopped. Runs with no stage members,
        runs that finished and runs of the mesh engine are left alone, so calling this at
        every start cancels nothing once the old runs are gone.

        A run that cannot be canceled is logged at error and counted, and the others are
        still canceled; it is tried again at the next start, because it is still open.
        """
        canceled: list[UUID] = []
        for campaign in await self._campaigns.list_active_campaigns():
            try:
                state = await self._steps.load(campaign.id)
                if not state.launched or state.finished or unit_root(state):
                    continue
                if not await self._engine.expects_unit(campaign.id):
                    continue
                await self.cancel(campaign, UPGRADE_CANCEL_REASON)
            except Exception as exc:
                logger.error(
                    "Could not cancel run %s, launched before step handoff moved to the "
                    "mesh: %s: %s; it is tried again at the next start",
                    campaign.slug,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                get_observability().count(
                    "ting_workflow_run_upgrade_cancel_errors_total",
                    description=(
                        "Workflow runs launched before step handoff moved to the mesh that "
                        "could not be canceled at the upgrade."
                    ),
                )
                continue
            logger.warning("Canceled run %s: %s", campaign.slug, UPGRADE_CANCEL_REASON)
            canceled.append(campaign.id)
        return canceled

    async def reconcile_owner(self, owner_id: str) -> None:
        """Catch up on reports missed while the activity stream was down.

        For each active run of the owner, read what the run's member sessions recorded in
        their durable logs and record the pickups, passes and closes Ting never received.
        Reports Ting already holds are repeats and change nothing.
        """
        for campaign in await self._campaigns.list_active_campaigns():
            if campaign.owner_id != owner_id:
                continue
            try:
                await self.catch_up_run(campaign)
            except Exception as exc:
                # One broken run must not stop the others being caught up, but it is seen:
                # logged at error with the run and the exception, and counted.
                logger.error(
                    "Catching up run %s with its sessions failed: %s: %s",
                    campaign.slug,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                get_observability().count(
                    "ting_workflow_run_reconcile_errors_total",
                    description=(
                        "Workflow runs that could not be caught up with their sessions' "
                        "durable logs after the activity stream reconnected."
                    ),
                )

    async def catch_up_run(self, campaign: WorkflowCampaign) -> None:
        """Read the ``workflow_step`` frames of the run's member sessions and record them.

        Skuld keeps each listening report, pickup, pass and close in the session's durable
        event log, which
        Forge serves (``GET /sessions/{id}/log``), so a report the activity stream dropped is
        read back from there. A frame already recorded is a repeat; one the run refuses is
        logged at error and skipped, so one stale frame never hides the ones after it.
        """
        state = await self._steps.load(campaign.id)
        if not state.launched or state.finished:
            return
        sessions = unit_sessions(state)
        if not sessions:
            return
        forge = await self._forge(campaign)
        for session_id in sessions.values():
            await self._catch_up_session(campaign, forge, session_id)

    async def _catch_up_session(self, campaign: WorkflowCampaign, forge, session_id: str) -> None:
        after = self._log_cursors.get(session_id, 0)
        while True:
            page = await forge.get_public_session_log_page(
                session_id, after=after, limit=self._page_size
            )
            for entry in page.entries:
                if entry.kind == WORKFLOW_STEP_FRAME:
                    await self._apply_frame(campaign, session_id, entry.payload)
            if page.has_more and page.scanned_through <= after:
                raise RuntimeError(
                    f"Forge's log of session {session_id} says it has more after sequence "
                    f"{after} but did not advance past it; Ting cannot read it to the end"
                )
            after = max(after, page.scanned_through)
            if not page.has_more:
                break
        self._log_cursors[session_id] = after

    async def _apply_frame(
        self, campaign: WorkflowCampaign, session_id: str, frame: Mapping[str, Any]
    ) -> None:
        """Record what one durable ``workflow_step`` frame says its session did."""
        try:
            listening = frame.get("listening")
            if isinstance(listening, Mapping):
                await self.record_listening(
                    run_id=UUID(str(listening["run_id"])),
                    session_id=session_id,
                    member=str(listening["member"]),
                    start_id=str(listening["start_id"]),
                )
                return
            passed = frame.get("passed")
            if isinstance(passed, Mapping):
                await self.record_passed(
                    run_id=UUID(str(passed["run_id"])),
                    session_id=session_id,
                    member=str(passed["member"]),
                    offer_id=UUID(str(passed["offer_id"])),
                    reason=str(passed.get("reason") or ""),
                )
                return
            step = frame.get("step")
            if not isinstance(step, Mapping):
                return
            run_id = UUID(str(step["run_id"]))
            activation_id = UUID(str(step["activation_id"]))
            attempt = int(step["attempt"])
            if step.get("offer_id"):
                await self.record_picked_up(
                    run_id=run_id,
                    session_id=session_id,
                    member=str(step["member"]),
                    offer_id=UUID(str(step["offer_id"])),
                    activation_id=activation_id,
                    attempt=attempt,
                )
            held = await self._steps.find_step(activation_id)
            if held is None:
                return
            await self.apply_reported_close(
                run_id,
                held,
                {
                    "state": step.get("state"),
                    "ending": step.get("ending"),
                    "attempt": attempt,
                },
            )
        except (StepFencingError, InvalidEndingError, HandoffRefusedError) as exc:
            logger.error(
                "Catching up run %s: a frame of session %s was refused: %s",
                campaign.slug,
                session_id,
                exc,
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.error(
                "Catching up run %s: a malformed workflow_step frame of session %s: %s (%r)",
                campaign.slug,
                session_id,
                exc,
                frame,
            )
            get_observability().count(
                "ting_workflow_step_reports_invalid_total",
                description="workflow_step activity reports Ting could not read.",
            )

    async def _forge(self, campaign: WorkflowCampaign):
        if campaign.connection_id:
            forge = await self._factory.for_connection(campaign.owner_id, campaign.connection_id)
        else:
            forge = await self._factory.primary_for_owner(campaign.owner_id)
        if forge is None:
            raise LookupError(f"no Forge connection resolves for run {campaign.slug}")
        return forge

    async def _require_run(self, run_id: UUID, activation_id: UUID) -> None:
        step = await self._steps.find_step(activation_id)
        if step is not None and step.run_id != run_id:
            raise StepFencingError(
                activation_id, f"the activation belongs to run {step.run_id}, not run {run_id}"
            )

    async def _project(self, run_id: UUID, state: RunState) -> None:
        campaign = await self._campaigns.get_campaign(run_id)
        if campaign is None:
            raise LookupError(f"run {run_id} has no campaign to record its state on")
        if self._gate_notifier is not None:
            # Before the no-change check: a sweep's refresh retries what Forge refused.
            await self._gate_notifier.sync(campaign, state)
            await self._gate_notifier.sync_paused(campaign, state)
        projection = project_run(campaign, state, now=datetime.now(UTC))
        if self._gate_notifier is not None:
            await self._gate_notifier.sync_failure(campaign, state, projection.status)
        if self._room_notifier is not None:
            # Also before the no-change check: a sweep's refresh retries what Forge refused.
            await self._room_notifier.sync(campaign, state, projection.status)
        if not projection.differs_from(campaign):
            return
        await self._sink.apply_run_projection(campaign, projection)


def _closed_report(reported: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The ending a session holds for a step, if it has closed or parked it."""
    if reported is None:
        return None
    ending = reported.get("ending")
    if reported.get("state") in {"closed", "waiting"} and isinstance(ending, Mapping):
        return ending
    return None


__all__ = ["ActorKind", "RunProjectionSink", "WorkflowRuns", "ending_of"]
