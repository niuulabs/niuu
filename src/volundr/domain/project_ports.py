"""Persistence and filesystem boundaries for project coordination."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from niuu.domain.agent_directory import AgentSkill
from niuu.domain.project_team import ProjectTeamRecord
from volundr.domain.projects import (
    ForgeProject,
    ProjectMember,
    ProjectMemberCandidate,
    ProjectReceipt,
    ProjectRole,
    ProjectTeamSync,
)


class ProjectConflictError(ValueError):
    """A retry conflicts with already stored state."""


class LastOwnerError(ProjectConflictError):
    """The change would leave the project without an owner."""


class ProjectAccessDeniedError(PermissionError):
    """The principal is on the project but its role does not allow this."""


class ProjectMemberNotFoundError(LookupError):
    """The user is not a member of the project."""


class MemberNotAllowedError(ValueError):
    """The person cannot be added to the project.

    One generic error for every reason (no such user, not active, not in the project's
    org), so a project owner cannot probe which user ids exist or which org they are in.
    """


class ProjectRepository(ABC):
    @abstractmethod
    def dispatch_lock(self, key: UUID) -> AbstractAsyncContextManager[None]:
        """Serialize retries across API workers until a launch is persisted."""

    @abstractmethod
    async def claim_dispatch(self, key: UUID, fingerprint: str, session_id: UUID) -> bool:
        """Claim a stable launch identity; return whether it was completed before."""

    @abstractmethod
    async def complete_dispatch(self, key: UUID) -> None: ...

    @abstractmethod
    async def list(self, owner_id: str, tenant_id: str) -> list[ForgeProject]: ...

    @abstractmethod
    async def list_member_projects(self, user_id: str, tenant_id: str) -> list[ForgeProject]:
        """Projects of *tenant_id* that *user_id* is a member of, newest first."""

    @abstractmethod
    async def get(self, project_id: UUID) -> ForgeProject | None: ...

    @abstractmethod
    async def register(self, project: ForgeProject) -> ForgeProject:
        """Store the project and, in the same transaction, its creator as an ``owner`` member.

        A project with no ``owner_id`` (no identity, single-user mode) has no member to record.
        """

    @abstractmethod
    async def update(self, project: ForgeProject, expected_revision: int) -> ForgeProject: ...

    @abstractmethod
    async def put_receipt(self, receipt: ProjectReceipt) -> ProjectReceipt: ...

    @abstractmethod
    async def receipts(self, project_id: UUID, after: int, limit: int) -> list[dict]: ...

    @abstractmethod
    async def acknowledge(self, project_id: UUID, receipt_id: UUID) -> bool: ...


class ProjectMemberRepository(ABC):
    """People on projects. A member belongs to the project's own tenant."""

    @abstractmethod
    async def list_for_project(self, project_id: UUID) -> list[ProjectMember]:
        """Every member of the project, with profile, oldest first."""

    @abstractmethod
    async def list_for_user(self, user_id: str, tenant_id: str) -> list[ProjectMember]:
        """Every project membership *user_id* holds in *tenant_id*."""

    @abstractmethod
    async def get(self, project_id: UUID, user_id: str) -> ProjectMember | None:
        """The member *user_id* of the project, or ``None`` when they are not on it."""

    @abstractmethod
    async def add(self, member: ProjectMember) -> ProjectMember:
        """Insert; raises ``ProjectConflictError`` when the user is already a member."""

    @abstractmethod
    async def update(
        self,
        project_id: UUID,
        user_id: str,
        *,
        role: ProjectRole | None = None,
        expertise: str | None = None,
        skills: list[AgentSkill] | None = None,
    ) -> ProjectMember | None:
        """Change the given fields (``None`` leaves one as it is).

        ``None`` is returned when the user is not a member. Demoting the project's last
        ``owner`` raises ``LastOwnerError``; the check and the write are one transaction.
        """

    @abstractmethod
    async def remove(self, project_id: UUID, user_id: str) -> bool:
        """Remove a member; False when they were not one. The last ``owner`` raises
        ``LastOwnerError``."""

    @abstractmethod
    async def replace(self, project_id: UUID, members: list[ProjectMember]) -> None:
        """Make *members* the project's whole membership, in one transaction.

        For a copy of a project: its home decided who is on it. Raises ``LastOwnerError``
        when none of them is an owner.
        """

    @abstractmethod
    async def candidates(
        self, project_id: UUID, tenant_id: str, query: str
    ) -> list[ProjectMemberCandidate]:
        """Active members of *tenant_id* not yet on the project whose name or email
        contains *query* (every one when it is empty), by name."""


class ProjectTeamSyncRepository(ABC):
    """How each project's team on this Forge stands with the Guild's."""

    @abstractmethod
    async def get(self, project_id: UUID) -> ProjectTeamSync:
        """The project's state; the empty one when it has none yet."""

    @abstractmethod
    async def changed(self, project_id: UUID) -> ProjectTeamSync:
        """The home changed the team: the next version, still to be published."""

    @abstractmethod
    async def published(self, project_id: UUID, version: int, at: datetime) -> ProjectTeamSync:
        """The Guild took *version*; a change made meanwhile is still to be published."""

    @abstractmethod
    async def copied(self, project_id: UUID, version: int, at: datetime) -> ProjectTeamSync:
        """This copy now holds *version*."""

    @abstractmethod
    async def failed(self, project_id: UUID, error: str) -> ProjectTeamSync:
        """The last exchange failed; what is held is unchanged."""


class TeamGuildError(RuntimeError):
    """The Guild could not answer, or refused; the message names the remedy.

    ``route_missing`` is a Guild, or a home Forge, from before project teams.
    """

    def __init__(self, message: str, *, route_missing: bool = False) -> None:
        super().__init__(message)
        self.route_missing = route_missing


class TeamNotVisibleError(TeamGuildError):
    """The Guild holds no team of this project for the caller."""


@dataclass(frozen=True)
class TeamGuildReply:
    """What the Guild answered to a forwarded change, passed on as it is."""

    status: int
    content: bytes = b""
    media_type: str | None = None


@dataclass(frozen=True)
class TeamPublicationReply:
    version: int


class TeamGuild(ABC):
    """The Guild as a Forge's project teams see it. Each call carries the caller's login."""

    @abstractmethod
    async def record(self, project_id: UUID, bearer: str) -> ProjectTeamRecord:
        """The team the Guild holds. Raises ``TeamNotVisibleError`` when it holds none, or
        none this caller may see, and ``TeamGuildError`` when it cannot answer."""

    @abstractmethod
    async def publish(
        self, project_id: UUID, instance_id: str, bearer: str
    ) -> TeamPublicationReply:
        """Have the Guild take the team from its home *instance_id* and copy it on."""

    @abstractmethod
    async def forward(
        self,
        project_id: UUID,
        instance_id: str,
        *,
        method: str,
        operation: str,
        query: str,
        body: bytes,
        bearer: str,
    ) -> TeamGuildReply:
        """Make a change to the project's team on its home *instance_id*. Raises
        ``TeamGuildError`` when the Guild cannot be reached: then nothing was changed."""


class ProjectWorkspace(ABC):
    @abstractmethod
    async def discover(self, workspace_path: str) -> ForgeProject:
        """Inspect a local Git checkout and recover its stable project identity."""

    @abstractmethod
    async def inspect(self, workspace_path: str) -> tuple[str, UUID | None]:
        """Return a checkout's canonical remote and the project UUID its manifest names."""

    @abstractmethod
    async def context(self, project: ForgeProject) -> tuple[str, str]:
        """Return bounded project context and its Git revision."""

    @abstractmethod
    async def archive_receipt(self, project: ForgeProject, receipt: ProjectReceipt) -> None:
        """Save a durable handoff to the meta-repository without committing unrelated edits."""
