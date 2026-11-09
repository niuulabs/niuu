"""Workflow gates, and runs that failed, in Forge's notification feed.

A gate of the step engine waits for a person in Ting; no session holds it, so Forge
never projects it into its feed the way it projects a session's own ``workflow_gate``
frame. Ting raises it there itself: when a gate opens, an ``attention`` notification
for the run's owner with a link to the run's review page; when the gate is decided (or
the run ends with it open), the same notification is resolved with what happened.

Forge's feed is per session, so a gate's notification is recorded on a session of its
run: the session of the step whose outcome opened the gate, else the run's most recent
session. The notification belongs to that session's owner. Forge never calls Ting for
any of this.

A run that fails is told the same way: an ``error`` notification for its owner naming each
step that failed and why (a member session that could not start says what its broker
reported), recorded on the failed step's session, else the run's newest one.

Every call is idempotent at Forge (the idempotency key names the run and the gate's
activation), so a gate synced again after a restart neither duplicates nor reopens its
notification. A notification Forge could not record is logged at error and counted, and
tried again on the run's next change or sweep: the gate itself is unaffected.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from niuu.cap_telemetry import record_degraded, record_truncation
from niuu.domain.notifications import MAX_BODY_CHARS, MAX_TITLE_CHARS
from niuu.observability import get_observability
from ting.domain.campaign_run_projection import run_gates
from ting.domain.models import WorkflowCampaign, WorkflowCampaignStatus
from ting.domain.step_graph import GATE_MODE_AUTOMATED_APPROVAL
from ting.domain.workflow_board import GATE_STATUS_APPROVED, GATE_STATUS_PENDING
from ting.domain.workflow_steps import RunState, StepKind, StepState, WorkflowStep
from ting.ports.volundr import VolundrFactory, VolundrPort

logger = logging.getLogger(__name__)

#: A gate that waits silently: the workflow's author asked for no notification.
_SILENT = "silent"
_DEGRADED_PATH = "ting.workflow_gate_notification"


def gate_correlation_id(run_id: UUID, activation_id: UUID | str) -> str:
    """The notification's correlation and idempotency key for one opening of a gate."""
    return f"workflow-gate:{run_id}:{activation_id}"


def review_path(campaign: WorkflowCampaign) -> str:
    """The run's review page in the Niuu UI."""
    return f"/ting/review/run/{campaign.slug}"


class GateNotifier:
    """Raises and settles the Forge notification of each gate a person must decide."""

    def __init__(self, *, volundr_factory: VolundrFactory, public_origin: str) -> None:
        self._factory = volundr_factory
        self._origin = public_origin.strip().rstrip("/")
        #: (activation id, phase) done, or being done, in this process. The claim is
        #: taken before Forge is called: a close reaches Ting once per activity stream
        #: that carries its session, and each copy projects the run, so two syncs of
        #: the same gate run side by side. Forge's idempotency key covers a restart.
        self._done: set[tuple[UUID, str]] = set()

    async def sync(self, campaign: WorkflowCampaign, state: RunState) -> None:
        """Bring the feed in line with the run's gates: open ones raised, decided ones settled."""
        gates = {gate["activation_id"]: gate for gate in run_gates(campaign, state)}
        for step in state.steps:
            if step.kind is not StepKind.GATE or not _notifies(campaign, step.node_id):
                continue
            phase = "open" if step.state is StepState.WAITING else "settled"
            if (step.id, phase) in self._done:
                continue
            if phase == "settled" and step.state not in _SETTLED_STATES:
                continue
            self._done.add((step.id, phase))
            try:
                await self._sync_gate(campaign, state, step, gates.get(str(step.id)), phase)
            except Exception as exc:
                logger.error(
                    "Could not %s the notification of gate %s of run %s: %s: %s",
                    "raise" if phase == "open" else "settle",
                    step.node_id,
                    campaign.slug,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                get_observability().count(
                    "ting_workflow_gate_notification_failures_total",
                    attributes={"phase": phase},
                    description=(
                        "Gate notifications Forge could not record or resolve; retried on "
                        "the run's next change."
                    ),
                )
                self._done.discard((step.id, phase))
                continue

    async def sync_failure(
        self, campaign: WorkflowCampaign, state: RunState, status: WorkflowCampaignStatus
    ) -> None:
        """Tell the run's owner the run failed and why, once."""
        if status is not WorkflowCampaignStatus.FAILED:
            return
        claim = (campaign.id, "failed")
        if claim in self._done:
            return
        self._done.add(claim)
        try:
            await self._raise_failure(campaign, state)
        except Exception as exc:
            self._done.discard(claim)
            logger.error(
                "Could not raise the failure notification of run %s: %s: %s",
                campaign.slug,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            get_observability().count(
                "ting_workflow_failure_notification_failures_total",
                description=(
                    "Failed-run notifications Forge could not record; retried on the run's "
                    "next change."
                ),
            )

    async def _raise_failure(self, campaign: WorkflowCampaign, state: RunState) -> None:
        failed = [step for step in state.steps if step.state is StepState.FAILED]
        session_id = _failure_session(state, failed)
        if session_id is None:
            record_degraded(
                _DEGRADED_PATH,
                f"run {campaign.slug} failed before any session of the run existed, so "
                "Forge's per-session feed has nowhere to record it; the failure is on the "
                f"review page {review_path(campaign)}",
            )
            return
        body = (
            " ".join(
                f"{step.member or step.node_id} failed {step.node_id}: {step.note}"
                for step in failed
            )
            or "The run failed."
        )
        key = f"workflow-run-failed:{campaign.id}"
        forge = await self._forge(campaign)
        await forge.submit_session_notification(
            session_id,
            {
                "kind": "error",
                "severity": "critical",
                "title": _fit(f"{campaign.name}: the run failed", MAX_TITLE_CHARS, "title"),
                "body": _fit(body, MAX_BODY_CHARS, "body"),
                "links": [{"label": "Open the run", "url": self._link(campaign), "kind": "url"}],
                "correlation_id": key,
            },
            idempotency_key=key,
        )
        logger.info("Raised the failure notification of run %s", campaign.slug)

    async def _sync_gate(
        self,
        campaign: WorkflowCampaign,
        state: RunState,
        step: WorkflowStep,
        gate: Mapping[str, Any] | None,
        phase: str,
    ) -> None:
        session_id = _anchor_session(state, step)
        if session_id is None:
            record_degraded(
                _DEGRADED_PATH,
                f"gate {step.node_id} of run {campaign.slug} opened before any session of "
                "the run existed, so Forge's per-session feed has nowhere to record it; it is "
                f"waiting on the review page {review_path(campaign)}",
            )
            return
        forge = await self._forge(campaign)
        key = gate_correlation_id(campaign.id, step.id)
        stored = await forge.submit_session_notification(
            session_id, self._draft(campaign, step, gate or {}, key), idempotency_key=key
        )
        if phase == "open":
            logger.info(
                "Raised the notification of gate %s of run %s on session %s",
                step.node_id,
                campaign.slug,
                session_id,
            )
            return
        await forge.resolve_notification(
            str(stored["id"]), resolution=_resolution(step, gate or {})
        )
        logger.info("Settled the notification of gate %s of run %s", step.node_id, campaign.slug)

    def _draft(
        self,
        campaign: WorkflowCampaign,
        step: WorkflowStep,
        gate: Mapping[str, Any],
        key: str,
    ) -> dict[str, Any]:
        label = str(gate.get("label") or step.node_id)
        body = (
            str(gate.get("summary") or "")
            or str(gate.get("instructions") or "")
            or str(gate.get("condition") or "")
        )
        return {
            "kind": "attention",
            "severity": "warning",
            "title": _fit(f"{campaign.name}: {label}", MAX_TITLE_CHARS, "title"),
            "body": _fit(body, MAX_BODY_CHARS, "body"),
            "links": [{"label": "Open the review", "url": self._link(campaign), "kind": "url"}],
            "correlation_id": key,
        }

    def _link(self, campaign: WorkflowCampaign) -> str:
        # Without notification.public_origin the path stays relative: the web app opens it
        # on its own origin, which serves the review page.
        return f"{self._origin}{review_path(campaign)}"

    async def _forge(self, campaign: WorkflowCampaign) -> VolundrPort:
        if campaign.connection_id:
            forge = await self._factory.for_connection(campaign.owner_id, campaign.connection_id)
        else:
            forge = await self._factory.primary_for_owner(campaign.owner_id)
        if forge is None:
            raise LookupError(f"no Forge connection resolves for run {campaign.slug}")
        return forge


#: A gate that left ``waiting``: decided (``done``), or ended with its run.
_SETTLED_STATES = frozenset({StepState.DONE, StepState.CANCELED, StepState.STALE})


def _notifies(campaign: WorkflowCampaign, node_id: str) -> bool:
    """Whether the gate waits for a person who should be told (not an agent, not silent)."""
    graph = campaign.workflow_snapshot.get("graph")
    nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
    for node in nodes or []:
        if isinstance(node, Mapping) and str(node.get("id") or "") == node_id:
            if str(node.get("mode") or "") == GATE_MODE_AUTOMATED_APPROVAL:
                return False
            return str(node.get("pendingBehavior") or "") != _SILENT
    return True


def _failure_session(state: RunState, failed: list[WorkflowStep]) -> str | None:
    """The session of the last failed step that had one, else the run's newest session."""
    for steps in (failed, state.steps):
        sessions = [step.session_id for step in steps if step.session_id]
        if sessions:
            return sessions[-1]
    return None


def _anchor_session(state: RunState, gate: WorkflowStep) -> str | None:
    """The session of the step whose outcome opened the gate, else the run's newest one."""
    producers = {(item.node_id, item.member) for item in gate.inputs}
    with_session = [step for step in state.steps if step.session_id]
    for step in reversed(with_session):
        if (step.node_id, step.member) in producers:
            return step.session_id
    return with_session[-1].session_id if with_session else None


def _resolution(step: WorkflowStep, gate: Mapping[str, Any]) -> str:
    if step.state is not StepState.DONE:
        return f"The run ended with the gate open ({step.state.value})."
    status = str(gate.get("status") or "")
    who = str(gate.get("decided_by") or "")
    by = f" by {who}" if who else ""
    if status == GATE_STATUS_APPROVED:
        return f"Approved{by}."
    if status == GATE_STATUS_PENDING:
        return f"Decided{by}."
    return f"Changes requested{by}."


def _fit(text: str, limit: int, field: str) -> str:
    if len(text) <= limit:
        return text
    marker = "…"
    kept = text[: limit - len(marker)] + marker
    record_truncation(
        f"forge notification {field} limit ({limit} characters)",
        what=f"a workflow gate notification {field}",
        kept=len(kept),
        total=len(text),
        remedy="the full text is on the run's review page",
    )
    return kept


__all__ = ["GateNotifier", "gate_correlation_id", "review_path"]
