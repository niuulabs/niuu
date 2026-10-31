"""Start and close the persistent coordinator a ticket works through.

Ting records which session coordinates a ticket and hands it the request. What
to do with that request is the coordinator's judgment: it is a Ravn, and it
starts workflows itself over A2A. The launch defaults set here are what tie
those runs back to the ticket, so the link never depends on the model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from niuu.domain.models import Principal
from ting.domain.models import Saga, TrackerIssue
from ting.domain.ticket_coordinator import (
    BRIEF_MESSAGE_METADATA,
    TICKET_METADATA_KEY,
    TicketCoordinator,
    TicketCoordinatorStatus,
    ticket_metadata,
)
from ting.ports.ticket_coordinator_repository import TicketCoordinatorRepository
from ting.ports.volundr import SpawnRequest, VolundrPort, VolundrSession

_SESSION_NAME_PREFIX = "coordinator"
_FLOCK_PEER_PREFIX = "flock-"
AGENT_CARD_PATH = "/.well-known/agent-card.json"
_BRANCH_UNSAFE = re.compile(r"[^a-z0-9._-]+")
_LISTENING_SESSION_STATUS = "running"
_BOOTING_SESSION_STATUSES = frozenset({"starting", "provisioning", "created"})
_RUNNING_SESSION_STATUSES = frozenset({"running", *_BOOTING_SESSION_STATUSES})


@dataclass(frozen=True)
class TicketWorkflow:
    """The workflow a ticket should run next."""

    id: str
    name: str


def ticket_branch(saga: Saga, issue: TrackerIssue) -> str:
    """A ticket's own branch, so no ticket can overwrite another's commits.

    It sits beside the project's branch, not under it: git cannot hold a ref
    ``feat/x/niu-1`` while ``feat/x`` exists. Forge creates it from the base
    branch the first time a session asks for it.
    """
    identifier = _BRANCH_UNSAFE.sub("-", issue.identifier.lower()).strip("-.")
    return f"{saga.feature_branch}-{identifier}"


def coordinator_peer_id(persona: str) -> str:
    """The peer a coordinator session's single persona answers as."""
    return f"{_FLOCK_PEER_PREFIX}{persona}"


def _standing_context(saga: Saga, issue: TrackerIssue) -> str:
    """What a coordinator knows on every turn, whatever woke it."""
    return (
        f"The ticket you coordinate is {issue.identifier}: {issue.title}\n"
        f"URL: {issue.url}\n"
        f"Repository: {saga.repos[0]}\n\n"
        f"{issue.description}"
    )


def coordinator_brief(issue: TrackerIssue, *, repo: str, workflow: TicketWorkflow) -> str:
    """The request a coordinator receives when its ticket should start a workflow."""
    return (
        f"Ticket {issue.identifier}: {issue.title}\n"
        f"URL: {issue.url}\n"
        f"Repository: {repo}\n\n"
        f"{issue.description}\n\n"
        f"Start the workflow {workflow.name!r} (skill {workflow.id}) for this ticket now."
    )


class TicketCoordinatorService:
    def __init__(
        self,
        *,
        repo: TicketCoordinatorRepository,
        volundr: VolundrPort,
        principal: Principal,
        auth_token: str | None,
        persona: str,
        ravn_config: dict[str, Any],
        platform_origin: str,
        anonymous_platform: bool,
    ) -> None:
        self._repo = repo
        self._volundr = volundr
        self._principal = principal
        self._auth_token = auth_token
        self._persona = persona
        self._ravn_config = ravn_config
        self._platform_origin = platform_origin
        self._anonymous_platform = anonymous_platform

    async def start_workflow(
        self,
        saga: Saga,
        issue: TrackerIssue,
        workflow: TicketWorkflow,
    ) -> TicketCoordinator:
        """Ask the ticket's coordinator to start a workflow, creating it the first time."""
        repo_name = saga.repos[0]
        brief = coordinator_brief(issue, repo=repo_name, workflow=workflow)
        current = await self._repo.get(saga.id, issue.id)
        if current is None or current.status == TicketCoordinatorStatus.CLOSED:
            return await self._spawn(saga, issue, brief, workflow, previous=current)

        session = await self._session(current.session_id)
        if session is None:
            return await self._spawn(saga, issue, brief, workflow, previous=current)
        requested = replace(
            current,
            requested_workflow_id=workflow.id,
            requested_at=datetime.now(UTC),
            start_error=None,
        )
        if session.status not in _RUNNING_SESSION_STATUSES:
            await self._volundr.start_session(
                current.session_id,
                auth_token=self._auth_token,
                principal=self._principal,
            )
        if session.status != _LISTENING_SESSION_STATUS:
            # Nothing is listening yet, so the brief waits for the session to report in.
            return await self._repo.save(replace(requested, pending_brief=brief))
        await self._volundr.send_directed_room_message(
            current.session_id,
            coordinator_peer_id(self._persona),
            brief,
            metadata=BRIEF_MESSAGE_METADATA,
            auth_token=self._auth_token,
            principal=self._principal,
        )
        return await self._repo.save(replace(requested, pending_brief=None))

    async def close(self, coordinator: TicketCoordinator) -> None:
        """End a coordinator with its ticket."""
        await self._volundr.stop_session(
            coordinator.session_id,
            auth_token=self._auth_token,
            principal=self._principal,
        )
        await self._repo.close(coordinator.saga_id, coordinator.issue_id)

    async def _session(self, session_id: str) -> VolundrSession | None:
        return await self._volundr.get_session(
            session_id,
            auth_token=self._auth_token,
            principal=self._principal,
        )

    async def _spawn(
        self,
        saga: Saga,
        issue: TrackerIssue,
        brief: str,
        workflow: TicketWorkflow,
        *,
        previous: TicketCoordinator | None,
    ) -> TicketCoordinator:
        if not self._platform_origin:
            raise RuntimeError(
                "A coordinator cannot be created here because it would not know how to reach "
                "Ting; set a2a.public_base_url"
            )
        session = await self._volundr.spawn_session(
            self._spawn_request(saga, issue, brief),
            auth_token=self._auth_token,
            principal=self._principal,
        )
        coordinator = TicketCoordinator(
            saga_id=saga.id,
            issue_id=issue.id,
            owner_id=self._principal.user_id,
            tenant_id=self._principal.tenant_id,
            session_id=session.id,
            connection_id=saga.instance_id,
            status=TicketCoordinatorStatus.ACTIVE,
            created_at=datetime.now(UTC),
            requested_workflow_id=workflow.id,
            requested_at=datetime.now(UTC),
        )
        if previous is not None:
            coordinator = replace(
                coordinator,
                created_at=previous.created_at,
                advanced_from=previous.advanced_from,
            )
        return await self._repo.save(coordinator)

    def _spawn_request(self, saga: Saga, issue: TrackerIssue, brief: str) -> SpawnRequest:
        return SpawnRequest(
            name=f"{_SESSION_NAME_PREFIX}-{issue.identifier.lower()}",
            repo=saga.repos[0],
            branch=saga.base_branch,
            base_branch=saga.base_branch,
            model="",
            tracker_issue_id=issue.identifier,
            tracker_issue_url=issue.url,
            system_prompt="",
            initial_prompt=brief,
            workload_type="ravn_flock",
            workload_config={
                "personas": [
                    {
                        "name": self._persona,
                        "system_prompt_extra": _standing_context(saga, issue),
                    }
                ],
                "initiative_context": brief,
                "ravn_config": self._launch_defaults(saga, issue),
            },
        )

    def _launch_defaults(self, saga: Saga, issue: TrackerIssue) -> dict[str, Any]:
        """Stamp the ticket on everything this coordinator launches over A2A."""
        configured = self._ravn_config.get("gateway", {}).get("platform", {})
        metadata = {
            **configured.get("a2a_default_metadata", {}),
            "repo": saga.repos[0],
            "branch": ticket_branch(saga, issue),
            "baseBranch": saga.base_branch,
            TICKET_METADATA_KEY: ticket_metadata(
                saga_id=saga.id,
                issue_id=issue.id,
                identifier=issue.identifier,
            ),
        }
        gateway = self._ravn_config.get("gateway", {})
        return {
            **self._ravn_config,
            "gateway": {
                **gateway,
                "platform": {
                    **configured,
                    "a2a_default_metadata": metadata,
                    "a2a_gate_decisions": False,
                    "anonymous_dev_mode": self._anonymous_platform,
                    "a2a_trusted_origins": [
                        *configured.get("a2a_trusted_origins", []),
                        self._platform_origin,
                    ],
                    "a2a_agent_card_urls": [
                        *configured.get("a2a_agent_card_urls", []),
                        f"{self._platform_origin}{AGENT_CARD_PATH}",
                    ],
                },
            },
        }
