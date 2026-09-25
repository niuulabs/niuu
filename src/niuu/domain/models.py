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
    MCP = "mcp"


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


class InstanceHealthStatus(StrEnum):
    """Server-recorded reachability of a registered runtime instance.

    ``UNKNOWN`` is the honest starting state — no probe has completed yet, so
    nothing is claimed either way. It is never used to mean "probably fine".
    """

    UNKNOWN = "unknown"
    OK = "ok"
    UNREACHABLE = "unreachable"


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
    health: InstanceHealthStatus = InstanceHealthStatus.UNKNOWN
    #: Last time a probe SUCCEEDED — "when did it last work".
    last_seen_at: datetime | None = None
    #: Last time a probe was ATTEMPTED, success or not — "when did we last
    #: look". Distinct from last_seen_at so a never-reachable instance can
    #: still report when it was last checked, without that being confused
    #: for having been seen.
    last_checked_at: datetime | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class RegisteredNode:
    """A machine (K8s cluster, DGX Spark, laptop in mini/docker mode) joined to Guild.

    Node-originated calls (heartbeat, leave) are authenticated by an Ed25519
    signature over the request, never a bearer JWT — ``public_key`` is what
    ``RegisteredNodeVerifier`` checks that signature against.
    """

    id: str
    name: str
    #: Base64-encoded raw 32-byte Ed25519 public key.
    public_key: str
    #: The joining admin's tenant, inherited from the pairing code — instances
    #: this node offers are registered under the same tenant.
    tenant_id: str
    #: user_id of the admin who minted the pairing code this node joined with.
    created_by: str
    created_at: datetime
    last_seen_at: datetime | None = None
    #: Last accepted signed-request timestamp (unix seconds), for strictly
    #: increasing replay protection. ``None`` before the node's first signed
    #: call.
    last_request_at: int | None = None


@dataclass(frozen=True)
class PairingCode:
    """Server-side record of a minted single-use node pairing code.

    The code itself is a scoped workload JWT (``token_use=valkyrie_build``,
    ``scopes=["node_join"]``, see ``.claude/rules/architecture.md``) so entry
    to the join route is gated by ``require_scope("node_join")`` like any
    other scoped workload credential. This row exists *in addition to* that —
    a JWT alone is reusable until it expires, so single-use is enforced here,
    by atomically consuming the row the first (and only the first) time the
    code is presented.
    """

    id: str
    code_hash: str
    created_by: str
    tenant_id: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None
    consumed_by_node_id: str | None = None


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
    tenant_id: str = ""
    scopes: tuple[str, ...] | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class Realm:
    """A Valkyrie's domain — the governance root for its build capability.

    A realm scopes trust grants and capabilities to one Valkyrie / Sleipnir
    domain. It lives in the shared volundr/niuu postgres so ravn can read it
    over HTTP without a ravn-local database.
    """

    id: UUID
    slug: str
    name: str
    sleipnir_domain: str | None
    owner_id: str | None
    instance_id: str | None
    created_at: datetime
    updated_at: datetime
    autonomy_profile: str = "balanced"


@dataclass(frozen=True)
class TrustGrant:
    """An action a realm's Valkyrie is permitted, scoped by class and target.

    ``action_class`` is one of observe|draft|build|test|deploy|mutate|spend.
    The ``build`` grant carries ``limits`` such as ``{"workflow": "tool-builder"}``
    that P3/P4 read to decide which Ting workflow may be commissioned and at what
    autonomy ``level``.
    """

    id: UUID
    realm_id: UUID
    action_class: str
    granted_at: datetime
    target: str = "*"
    level: int = 0
    limits: dict = field(default_factory=dict)
    granted_by: str | None = None


@dataclass(frozen=True)
class Capability:
    """A tool/skill/persona/integration a realm has, is building, or lacks.

    ``kind`` is one of tool|skill|managed_tool|persona|integration;
    ``status`` is one of present|gap|building. A ``gap`` is what a Valkyrie may
    commission a build to fill.
    """

    id: UUID
    realm_id: UUID
    name: str
    kind: str
    created_at: datetime
    updated_at: datetime
    status: str = "gap"
    trust_level: int = 0
    mimir_page_path: str | None = None
    notes: str | None = None


class CacheEntry:
    """Simple TTL cache entry."""

    __slots__ = ("value", "expires_at")

    def __init__(self, value: object, ttl: float) -> None:
        self.value = value
        self.expires_at = time.monotonic() + ttl

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at
