"""Shared domain models for Niuu modules."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

LINEAR_API_URL = "https://api.linear.app/graphql"


@dataclass(frozen=True)
class Principal:
    """Authenticated identity extracted from JWT."""

    user_id: str
    email: str
    tenant_id: str
    roles: list[str]


class IntegrationType(StrEnum):
    """Category of integration."""

    SOURCE_CONTROL = "source_control"
    ISSUE_TRACKER = "issue_tracker"
    MESSAGING = "messaging"
    AI_PROVIDER = "ai_provider"
    CODE_FORGE = "code_forge"


class InstanceKind(StrEnum):
    """Supported registered runtime instance kinds."""

    VOLUNDR = "volundr"
    TING = "ting"
    MIMIR = "mimir"
    BIFROST = "bifrost"
    RAVN = "ravn"
    OBSERVATORY = "observatory"
    GENERIC = "generic"


class InstanceVisibility(StrEnum):
    """Visibility / ownership scope for a registered runtime instance."""

    SYSTEM = "system"
    TENANT = "tenant"
    USER = "user"


@dataclass(frozen=True)
class IntegrationConnection:
    """A configured integration connection (e.g., issue tracker)."""

    id: str
    owner_id: str
    integration_type: IntegrationType
    adapter: str  # fully-qualified class path
    credential_name: str  # reference to stored credential
    config: dict  # adapter-specific config
    enabled: bool
    created_at: datetime
    updated_at: datetime
    slug: str = ""  # references IntegrationDefinition.slug


@dataclass(frozen=True)
class RegisteredInstance:
    """A registered runtime instance visible to one or more principals."""

    id: str
    kind: InstanceKind
    slug: str
    name: str
    base_url: str
    visibility: InstanceVisibility
    owner_id: str | None
    tenant_id: str | None
    enabled: bool
    is_default: bool
    config: dict
    created_at: datetime
    updated_at: datetime
    tags: list[str] = field(default_factory=list)


class SecretType(StrEnum):
    """Type of stored credential."""

    API_KEY = "api_key"
    OAUTH_TOKEN = "oauth_token"
    GIT_CREDENTIAL = "git_credential"
    SSH_KEY = "ssh_key"
    TLS_CERT = "tls_cert"
    GENERIC = "generic"


@dataclass(frozen=True)
class StoredCredential:
    """Metadata for a stored credential (never contains secret values)."""

    id: str
    name: str
    secret_type: SecretType
    keys: tuple[str, ...]
    metadata: dict
    owner_id: str
    owner_type: str  # "user" | "tenant"
    created_at: datetime
    updated_at: datetime


class GitProviderType(StrEnum):
    """Type of git hosting provider."""

    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"
    GENERIC = "generic"


@dataclass(frozen=True)
class RepoInfo:
    """Information about a git repository."""

    provider: GitProviderType
    org: str
    name: str
    clone_url: str
    url: str  # Web URL for the repo
    default_branch: str = "main"
    branches: tuple[str, ...] = ()


class PullRequestStatus(StrEnum):
    """Status of a pull request."""

    OPEN = "open"
    MERGED = "merged"
    CLOSED = "closed"


class CIStatus(StrEnum):
    """CI pipeline status."""

    PASSING = "passing"
    FAILING = "failing"
    PENDING = "pending"
    UNKNOWN = "unknown"


class ReviewStatus(StrEnum):
    """Code review status."""

    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    PENDING = "pending"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PullRequest:
    """A pull request / merge request from a git provider."""

    number: int
    title: str
    url: str
    repo_url: str
    provider: GitProviderType
    source_branch: str
    target_branch: str
    status: PullRequestStatus
    description: str | None = None
    ci_status: CIStatus | None = None
    review_status: ReviewStatus | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class PersonalAccessToken:
    """A personal access token for API authentication."""

    id: UUID
    owner_id: str
    name: str
    created_at: datetime
    last_used_at: datetime | None = None


class CacheEntry:
    """Simple TTL cache entry."""

    __slots__ = ("value", "expires_at")

    def __init__(self, value: object, ttl: float) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at
