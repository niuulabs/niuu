"""Close the persistent coordinator a ticket works through, and place sessions under it.

A ticket's coordinator was a Ravn resident that started the ticket's workflows
itself over A2A. That runtime is gone and workflow steps have no runner yet, so
asking a ticket's coordinator to start a workflow is refused
(:meth:`TicketCoordinatorService.start_workflow`).
"""

from __future__ import annotations

from dataclasses import dataclass

from niuu.domain.models import Principal
from ting.domain.exceptions import WorkflowStepsHaveNoRunnerError
from ting.domain.models import Saga, TrackerIssue
from ting.domain.ticket_coordinator import TicketCoordinator
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository
from ting.ports.volundr import SessionMembership, VolundrPort

COORDINATOR_ROLE = "coordinator"
WORKER_ROLE = "worker"
_FLOCK_PEER_PREFIX = "flock-"
_WORK_PROJECT_LABEL_PREFIX = "work-project:"


@dataclass(frozen=True)
class TicketWorkflow:
    """The workflow a ticket should run next."""

    id: str
    name: str


def coordinator_peer_id(persona: str) -> str:
    """The peer a coordinator session's single persona answers as."""
    return f"{_FLOCK_PEER_PREFIX}{persona}"


def work_project_label(saga: Saga) -> str:
    """Names the tracker link on a session, since one Forge project can hold several."""
    return f"{_WORK_PROJECT_LABEL_PREFIX}{saga.id}"


async def ticket_membership(
    volundr: VolundrPort,
    saga: Saga,
    coordinator: TicketCoordinator | None,
    objective: str,
    *,
    auth_token: str | None,
    principal: Principal,
) -> SessionMembership:
    """Where a workflow session of a ticket joins Forge's project: under its coordinator."""
    project = await volundr.ensure_project(
        str(saga.forge_project_id), saga.name, auth_token=auth_token, principal=principal
    )
    labels = (work_project_label(saga),)
    if coordinator is None:
        return SessionMembership(
            project_id=project.id, role=WORKER_ROLE, objective=objective, labels=labels
        )
    # Coordinators from before tickets joined projects are not members yet.
    await volundr.join_project(
        coordinator.session_id,
        project.id,
        role=COORDINATOR_ROLE,
        auth_token=auth_token,
        principal=principal,
    )
    return SessionMembership(
        project_id=project.id,
        role=WORKER_ROLE,
        parent_instance_id=project.instance_id,
        parent_session_id=coordinator.session_id,
        objective=objective,
        labels=labels,
    )


async def child_membership(
    volundr: VolundrPort,
    parent_forge: VolundrPort,
    parent_session_id: str,
    objective: str,
    *,
    auth_token: str | None,
    principal: Principal,
) -> SessionMembership | None:
    """A child session follows its parent; a parent outside any project keeps it outside."""
    project_id = await parent_forge.session_project(
        parent_session_id, auth_token=auth_token, principal=principal
    )
    if project_id is None:
        return None
    home = await parent_forge.get_project(project_id, auth_token=auth_token, principal=principal)
    if home is None:
        raise LookupError(f"Session {parent_session_id} names project {project_id}, which is gone")
    project = await volundr.ensure_project(
        project_id, home.name, auth_token=auth_token, principal=principal
    )
    return SessionMembership(
        project_id=project.id,
        role=WORKER_ROLE,
        parent_instance_id=home.instance_id,
        parent_session_id=parent_session_id,
        objective=objective,
    )


class TicketCoordinatorService:
    def __init__(
        self,
        *,
        repo: TicketCoordinatorRepository,
        volundr: VolundrPort,
        principal: Principal,
        auth_token: str | None,
    ) -> None:
        self._repo = repo
        self._volundr = volundr
        self._principal = principal
        self._auth_token = auth_token

    async def start_workflow(
        self,
        saga: Saga,
        issue: TrackerIssue,
        workflow: TicketWorkflow,
    ) -> TicketCoordinator:
        """Ask the ticket's coordinator to start a workflow.

        :raises WorkflowStepsHaveNoRunnerError: Always. The coordinator was a Ravn
            resident and the workflow it started ran on Ravn; neither exists.
        """
        raise WorkflowStepsHaveNoRunnerError()

    async def close(self, coordinator: TicketCoordinator) -> None:
        """End a coordinator with its ticket."""
        await self._volundr.stop_session(
            coordinator.session_id,
            auth_token=self._auth_token,
            principal=self._principal,
        )
        await self._repo.close(coordinator.saga_id, coordinator.issue_id)
