"""A workflow run as one thread in Forge: the project, role, objective and label of its sessions.

A run's sessions (one per stage member) join the run's Forge project through Forge's
session coordination, so Forge's Sessions page shows them together under the project,
each with the run as its objective and the member as its role. Every session carries the
``workflow-run:<slug>`` label; its ``workflow:<slug>`` tag is what links it back to the run.

The run's project is the one it was launched into (``projectId`` on launch), else the
project of the ticket it was started for: Ting shares Forge's project under the same id
(``Saga.forge_project_id``). A run that belongs to no project has no coordination: its
sessions are created exactly as before.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from niuu.cap_telemetry import record_truncation
from ting.domain.models import WorkflowCampaign
from ting.domain.ticket_coordinator import ticket_of
from ting.ports.saga_repository import SagaRepository

#: Campaign metadata key holding the Forge project a run was launched into.
RUN_PROJECT_KEY = "forge_project_id"
#: Prefix of the label every session of a run carries.
RUN_LABEL_PREFIX = "workflow-run:"
#: Forge's limit on a session's objective (``SessionCoordination.objective``).
FORGE_OBJECTIVE_MAX = 4000
_RUN_PROMPT_KEY = "prompt"


class RunProjectError(LookupError):
    """The run names a project that cannot be found; the message says which and why."""


@dataclass(frozen=True)
class RunProject:
    """The Forge project a run's sessions join.

    ``name`` is set when Ting may create the project under its id (a ticket's project,
    created lazily the way ticket work always has been); ``None`` when the run was
    launched into a project that must already exist.
    """

    id: str
    name: str | None = None


def run_label(campaign: WorkflowCampaign) -> str:
    """The label every session of the run carries."""
    return f"{RUN_LABEL_PREFIX}{campaign.slug}"


def run_objective(campaign: WorkflowCampaign) -> str:
    """What the run is about, as each of its sessions states its objective."""
    lines = str(campaign.metadata.get(_RUN_PROMPT_KEY) or "").strip().splitlines()
    title = lines[0].strip() if lines else ""
    objective = f"{campaign.workflow_name}: {title}" if title else campaign.workflow_name
    if len(objective) <= FORGE_OBJECTIVE_MAX:
        return objective
    marker = "…"
    kept = objective[: FORGE_OBJECTIVE_MAX - len(marker)] + marker
    record_truncation(
        f"forge session objective limit ({FORGE_OBJECTIVE_MAX} characters)",
        what=f"the objective of run {campaign.slug}'s sessions",
        kept=len(kept),
        total=len(objective),
        remedy="the full brief is on the run's review page",
    )
    return kept


async def run_project(campaign: WorkflowCampaign, sagas: SagaRepository) -> RunProject | None:
    """The project the run belongs to, or ``None`` when it belongs to none."""
    launched_into = str(campaign.metadata.get(RUN_PROJECT_KEY) or "")
    if launched_into:
        return RunProject(id=launched_into)
    ticket = ticket_of(dict(campaign.metadata))
    if ticket is None:
        return None
    saga_id = ticket[0]
    try:
        saga = await sagas.get_saga(UUID(saga_id), owner_id=campaign.owner_id)
    except ValueError as exc:
        raise RunProjectError(
            f"run {campaign.slug} was started for a ticket of project {saga_id!r}, which is "
            "not a project id"
        ) from exc
    if saga is None:
        raise RunProjectError(
            f"run {campaign.slug} was started for a ticket of project {saga_id}, which its "
            "owner no longer has; its sessions cannot join it"
        )
    return RunProject(id=str(saga.forge_project_id), name=saga.name)


__all__ = [
    "FORGE_OBJECTIVE_MAX",
    "RUN_LABEL_PREFIX",
    "RUN_PROJECT_KEY",
    "RunProject",
    "RunProjectError",
    "run_label",
    "run_objective",
    "run_project",
]
