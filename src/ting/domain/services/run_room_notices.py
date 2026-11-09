"""A workflow run's steps, gates and end, as its Coordinator's notices in the run's room.

A run's room is a session of its own (no agent) that every member of the run is nested under;
its members share their work into it. The run's coordinator is Ting, so Ting posts what the
run does there: each member's step starting and closing with its verdict, each gate opening
and being decided, and the run ending. A notice is shown in the room and sent to no one: it
goes through Forge's room ``notice`` operation, never through a step delivery or the mesh.

Each notice is keyed by its step and phase. Forge's room shows a notice posted again once, and
this process posts each key once; a notice Forge could not post is logged at error, counted,
and posted again on the run's next change. A run in no project has no room, so it posts none.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from niuu.observability import get_observability
from ting.domain.campaign_run_projection import run_gates
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.workflow_board import GATE_STATUS_APPROVED, GATE_STATUS_PENDING
from ting.domain.workflow_steps import RunState, StepKind, StepState, WorkflowStep
from ting.ports.volundr import VolundrFactory, VolundrPort, VolundrSession

logger = logging.getLogger(__name__)

_ENDED = {
    WorkflowCampaignStatus.COMPLETED: "The run completed.",
    WorkflowCampaignStatus.FAILED: "The run failed.",
    WorkflowCampaignStatus.CANCELED: "The run was canceled.",
}


@dataclass(frozen=True)
class RoomNotice:
    """One notice: its key (step and phase) and what the room shows."""

    id: str
    text: str


def run_notices(
    campaign: WorkflowCampaign, state: RunState, status: WorkflowCampaignStatus
) -> list[RoomNotice]:
    """What the run's room should show of the run so far, oldest first."""
    labels = _node_labels(campaign)
    gates = {gate["activation_id"]: gate for gate in run_gates(campaign, state)}
    notices: list[RoomNotice] = []
    for step in sorted(state.steps, key=lambda item: item.created_at):
        label = labels.get(step.node_id) or step.node_id
        if step.kind is StepKind.STAGE_MEMBER:
            notices.extend(_member_notices(step, label))
        elif step.kind is StepKind.GATE:
            notices.extend(_gate_notices(step, label, gates.get(str(step.id)) or {}))
    ended = _ENDED.get(status)
    if ended:
        notices.append(RoomNotice(id=f"{campaign.id}:{status.value}", text=ended))
    return notices


def _member_notices(step: WorkflowStep, label: str) -> list[RoomNotice]:
    member = step.member or "A member"
    notices: list[RoomNotice] = []
    if step.activated_at is not None:
        again = f" (attempt {step.attempt})" if step.attempt > 1 else ""
        rework = f", rework {step.generation}" if step.generation else ""
        notices.append(
            RoomNotice(id=f"{step.id}:started", text=f"{member} started {label}{again}{rework}.")
        )
    if step.state is StepState.DONE:
        verdict = step.verdict or "done"
        note = f" {step.note}" if step.note else ""
        notices.append(
            RoomNotice(id=f"{step.id}:closed", text=f"{member} closed {label}: {verdict}.{note}")
        )
    elif step.state is StepState.FAILED:
        reason = f" {step.note}" if step.note else ""
        notices.append(RoomNotice(id=f"{step.id}:failed", text=f"{member} failed {label}.{reason}"))
    return notices


def _gate_notices(step: WorkflowStep, label: str, gate: Mapping[str, Any]) -> list[RoomNotice]:
    if step.state not in (StepState.WAITING, StepState.DONE):
        return []
    notices = [RoomNotice(id=f"{step.id}:open", text=f"{label} is waiting for a decision.")]
    status = str(gate.get("status") or "")
    if step.state is StepState.DONE and status != GATE_STATUS_PENDING:
        who = str(gate.get("decided_by") or "")
        by = f" by {who}" if who else ""
        decided = "approved" if status == GATE_STATUS_APPROVED else "sent back"
        notices.append(RoomNotice(id=f"{step.id}:decided", text=f"{label} {decided}{by}."))
    return notices


def _node_labels(campaign: WorkflowCampaign) -> dict[str, str]:
    graph = campaign.workflow_snapshot.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
    return {
        str(node.get("id") or ""): str(node.get("label") or "")
        for node in nodes or []
        if isinstance(node, Mapping)
    }


def _room_of(sessions: list[VolundrSession], campaign: WorkflowCampaign) -> VolundrSession | None:
    """The run's room: its session in a project that is nested under none."""
    tag = f"workflow:{campaign.slug}"
    for session in sessions:
        if session.tracker_issue_id == tag and session.project_id and not session.parent_session_id:
            return session
    return None


class RunRoomNotifier:
    """Posts a run's steps, gates and end in the run's room, each once."""

    def __init__(self, *, volundr_factory: VolundrFactory) -> None:
        self._factory = volundr_factory
        #: Notice keys posted, or being posted, by this process.
        self._done: set[str] = set()

    async def sync(
        self, campaign: WorkflowCampaign, state: RunState, status: WorkflowCampaignStatus
    ) -> None:
        pending = [
            notice for notice in run_notices(campaign, state, status) if notice.id not in self._done
        ]
        if not pending:
            return
        forge = await self._forge(campaign)
        room = _room_of(await forge.list_sessions(), campaign)
        if room is None:
            # No room yet (its first member is still being created), or the run is in no
            # project and has none: nothing is kept, so a later change posts these.
            return
        for notice in pending:
            self._done.add(notice.id)
            try:
                await forge.post_room_notice(room.id, notice.text, notice_id=notice.id)
            except Exception as exc:
                self._done.discard(notice.id)
                logger.error(
                    "Could not post %s in the room of run %s: %s: %s",
                    notice.id,
                    campaign.slug,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                get_observability().count(
                    "ting_run_room_notice_failures_total",
                    description=(
                        "Run notices Forge could not post in the run's room; posted again on "
                        "the run's next change."
                    ),
                )
                return

    async def _forge(self, campaign: WorkflowCampaign) -> VolundrPort:
        if campaign.connection_id:
            forge = await self._factory.for_connection(campaign.owner_id, campaign.connection_id)
        else:
            forge = await self._factory.primary_for_owner(campaign.owner_id)
        if forge is None:
            raise LookupError(f"no Forge connection resolves for run {campaign.slug}")
        return forge


__all__ = ["RoomNotice", "RunRoomNotifier", "run_notices"]
