"""Public, harness-neutral project and session relationship contracts."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class ForgeProject(BaseModel):
    """Index of a Git meta-repository; any number of sessions may coordinate it."""

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    repo_url: str = Field(min_length=1, max_length=2048)
    workspace_path: str = Field(default="", max_length=2048)
    status: Literal["active", "archived"] = "active"
    owner_id: str = ""
    tenant_id: str = ""
    revision: int = Field(default=1, ge=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def safe_repository(self):
        self.validate_repository_url(self.repo_url)
        return self

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
