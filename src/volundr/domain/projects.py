"""Public, harness-neutral project and session relationship contracts."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from niuu.domain.agent_directory import AgentSkill
from volundr.domain.project_memory import validate_write_prefixes


class SessionReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instance_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.-]+$")
    session_id: UUID


class SessionCoordination(BaseModel):
    """Public metadata only. Runtime credentials never belong in this object."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    project_id: UUID
    role: str = Field(default="worker", pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    parent: SessionReference | None = None
    objective: str = Field(default="", max_length=4000)
    context_revision: str = Field(default="", max_length=200)
    labels: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def validate_labels(self):
        if any(not label or len(label) > 100 for label in self.labels):
            raise ValueError("Labels must contain 1 to 100 characters")
        if len(set(self.labels)) != len(self.labels):
            raise ValueError("Labels must be unique")
        return self


PROJECT_BRIEF_MAX_CHARS = 8192


def canonical_repository_url(remote: str) -> str:
    """One spelling per repository: HTTPS, no ``.git`` suffix, GitHub paths lowercased.

    Validates first; a credential-bearing remote never becomes metadata or error text.
    """
    from urllib.parse import urlsplit, urlunsplit

    ForgeProject.validate_repository_url(remote)
    value = remote
    if value.startswith("git@"):
        host, path = value[4:].split(":", 1)
        value = f"https://{host}/{path}"
    url = urlsplit(value)
    path = url.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not path or path == "/":
        raise ValueError("Git remote must identify a repository")
    if url.hostname == "github.com":
        path = path.lower()
    return urlunsplit(("https", url.netloc.lower(), path, "", ""))


class ForgeProject(BaseModel):
    """A named grouping of sessions. A Git repository is optional and replaceable.

    ``repo_url`` and ``workspace_path`` stay strings (``""`` when absent) so clients
    written for repository-first projects keep decoding every project.
    ``home_instance_id`` is empty on the authoritative copy and names that copy's
    host on a metadata replica the mesh created for sessions elsewhere.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    slug: str = Field(default="", pattern=r"^(|[a-z0-9][a-z0-9-]{0,62})$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    brief: str = Field(default="", max_length=PROJECT_BRIEF_MAX_CHARS)
    memory_write_prefixes: tuple[str, ...] = Field(
        default=(),
        description=(
            "Page path prefixes under which a session of this project may write straight "
            "into the project's own memory layer; any other write becomes a proposal. Set "
            "by a project owner. Empty means every write to the layer is a proposal."
        ),
    )
    repo_url: str = Field(default="", max_length=2048)
    workspace_path: str = Field(default="", max_length=2048)
    home_instance_id: str = Field(default="", max_length=100, pattern=r"^[a-zA-Z0-9_.-]*$")
    status: Literal["active", "archived"] = "active"
    owner_id: str = ""
    tenant_id: str = ""
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("memory_write_prefixes")
    @classmethod
    def _memory_write_prefixes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return validate_write_prefixes(value)

    @model_validator(mode="after")
    def safe_repository(self):
        if self.repo_url:
            self.validate_repository_url(self.repo_url)
        elif self.workspace_path:
            raise ValueError("A project checkout requires its repository URL")
        return self

    @property
    def has_repository(self) -> bool:
        return bool(self.repo_url)

    def document_json(self) -> str:
        """Stored form. Empty lightweight fields are omitted so a rolled-back Forge
        binary can still read every project that does not use them."""
        empty = {
            key
            for key in ("brief", "home_instance_id", "memory_write_prefixes")
            if not getattr(self, key)
        }
        return self.model_dump_json(exclude=empty)

    @staticmethod
    def slug_for(name: str) -> str:
        import re
        import unicodedata

        ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:63].strip("-")
        return slug or "project"

    @staticmethod
    def validate_repository_url(repo_url: str) -> None:
        from urllib.parse import urlsplit

        if repo_url.startswith("git@"):
            import re

            if not re.fullmatch(r"git@[a-zA-Z0-9.-]+:[a-zA-Z0-9_./-]+", repo_url):
                raise ValueError("Invalid Git repository URL")
            return
        url = urlsplit(repo_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("Use an HTTPS or git@ repository URL without embedded credentials")
        if url.query or url.fragment:
            raise ValueError("Repository URLs cannot contain query strings or fragments")


class ProjectReceipt(BaseModel):
    """Durable, caller-keyed handoff. Recording a result does not accept its claims."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    project_id: UUID
    sender: SessionReference
    recipient: SessionReference | None = None
    kind: str = Field(default="handoff", pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    content: str = Field(min_length=1, max_length=32000)
    evidence: list[str] = Field(default_factory=list, max_length=32)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    acknowledged_at: datetime | None = None

    @model_validator(mode="after")
    def bounded_evidence(self):
        if any(len(item) > 2048 for item in self.evidence):
            raise ValueError("Evidence references must be at most 2048 characters")
        return self


class ProjectRole(StrEnum):
    """A member's authority on a project. Roles are a ladder: each includes those below.

    ``viewer`` sees the project and its sessions and chats in them; ``contributor`` also
    starts sessions and edits the project; ``approver`` also resolves gates and permission
    prompts; ``owner`` also manages members and settings. Platform roles are never granted
    by a project.
    """

    VIEWER = "viewer"
    CONTRIBUTOR = "contributor"
    APPROVER = "approver"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        return _PROJECT_ROLE_LADDER.index(self)

    def at_least(self, minimum: "ProjectRole") -> bool:
        return self.rank >= minimum.rank


_PROJECT_ROLE_LADDER = (
    ProjectRole.VIEWER,
    ProjectRole.CONTRIBUTOR,
    ProjectRole.APPROVER,
    ProjectRole.OWNER,
)


class ProjectMember(BaseModel):
    """One person of the project's org and what they may do on it.

    ``expertise`` is free text and ``skills`` use the A2A ``AgentSkill`` shape, so people
    and agents can be listed and ranked the same way. ``display_name`` and ``email`` come
    from the user's profile when the member is listed; they are never stored here.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    user_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    role: ProjectRole
    expertise: str = ""
    skills: list[AgentSkill] = Field(default_factory=list)
    added_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    display_name: str = ""
    email: str = ""


class ProjectMemberCandidate(BaseModel):
    """An active member of the project's org who is not yet on the project."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    display_name: str = ""
    email: str = ""
