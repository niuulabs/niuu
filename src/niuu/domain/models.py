"""Shared domain models for Niuu modules."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from niuu.domain.delivery import CheckConclusion, CheckRecord

LINEAR_API_URL = "https://api.linear.app/graphql"


@dataclass(frozen=True)
class Principal:
    """Authenticated identity extracted from JWT.

    ``token_use``, ``scopes`` and ``bound_session_id`` are set only for a scoped
    credential (e.g. a Forge ``forge_session`` token): what it may do and the one
    session it is bound to. Human and PAT principals leave them empty.

    ``actor`` names the workload acting on behalf of ``user_id`` (the RFC 8693
    ``act.sub`` of an exchanged workload token, e.g. a resident's ServiceAccount);
    empty when the caller speaks for itself. It is attribution only: it grants nothing.
    """

    user_id: str
    email: str
    tenant_id: str
    roles: list[str]
    token_use: str = ""
    scopes: tuple[str, ...] = ()
    bound_session_id: str | None = None
    actor: str = ""


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


#: Slug of a registered push device (``volundr.adapters.outbound.integration_device_tokens``).
#: A push device is a messaging connection only the push sink delivers to: it is not a
#: Ting channel and never a session integration.
PUSH_DEVICE_SLUG = "push-device"


def is_push_device(connection: IntegrationConnection) -> bool:
    """Whether ``connection`` is a registered push device rather than a channel."""
    return getattr(connection, "slug", "") == PUSH_DEVICE_SLUG


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
    #: The Guild-assigned node that owns this instance, or None for an
    #: instance an admin registered directly. A real column, never writable
    #: through InstanceCreateRequest/InstanceUpdateRequest — see migration
    #: 000083. This is the ownership key node registration uses instead of
    #: the operator-chosen node *name*/slug, so a node cannot "adopt" an
    #: existing instance by choosing a colliding name.
    node_id: str | None = None


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
    #: Copied once from the pairing code's own consent at join time — never
    #: writable afterward (there is no node-update endpoint) — so every
    #: later heartbeat re-registration enforces the same operator-granted
    #: transport consent, not a value the node itself supplies.
    allow_plaintext: bool = False
    last_seen_at: datetime | None = None
    #: Last accepted signed-request timestamp (unix MILLISECONDS), for
    #: strictly increasing replay protection. ``None`` before the node's
    #: first signed call. Only ever advanced by the single atomic
    #: conditional UPDATE in ``PostgresNodeRepository.try_advance_watermark``.
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
    #: Operator consent, recorded at mint time, for the joining node to
    #: register a plaintext (http://) instance URL. A node can never grant
    #: this to itself — see ``.claude/rules/no-fallbacks.md``.
    allow_plaintext: bool = False
    #: Operator consent for a node with no identity verification of its own
    #: (host_auth.mode: none) to join a Guild that runs host_auth.mode:
    #: oidc, where Guild would otherwise forward real user bearer tokens to
    #: an instance that trusts everyone.
    allow_untrusted_node_auth: bool = False
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
class ChangedFile:
    """One file touched by a commit or a comparison, as the provider reports it.

    ``additions`` / ``deletions`` are ``None`` when the provider did not say (for
    example a GitLab diff it marked too large). ``patch`` is ``None`` when the
    caller did not ask for patches *or* the provider omitted it; in the latter
    case ``patch_omitted_reason`` says why, so a missing patch is never
    mistaken for an empty change.
    """

    path: str
    status: str
    additions: int | None
    deletions: int | None
    previous_path: str | None = None
    patch: str | None = None
    patch_omitted_reason: str | None = None


@dataclass(frozen=True)
class CommitSummary:
    """A commit in a comparison's list: identity and subject line only."""

    sha: str
    subject: str
    author_name: str
    authored_at: datetime | None
    url: str


@dataclass(frozen=True)
class CommitDetail:
    """One commit with the files it changed.

    ``files`` may be narrowed by a path filter; ``files_total`` is always the
    number of files the commit changed, and ``unmatched_paths`` lists requested
    paths that matched none of them. ``patch_included`` records whether the
    patches were requested at all.
    """

    repo_url: str
    sha: str
    url: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    authored_at: datetime | None
    message: str
    additions: int | None
    deletions: int | None
    files: tuple[ChangedFile, ...]
    files_total: int
    unmatched_paths: tuple[str, ...] = ()
    patch_included: bool = True


@dataclass(frozen=True)
class RefComparison:
    """``base...head`` as the provider reports it.

    ``ahead_by`` is the number of commits on ``head`` that ``base`` lacks and
    ``behind_by`` the reverse. ``files`` / ``files_total`` /
    ``unmatched_paths`` / ``patch_included`` follow :class:`CommitDetail`.
    """

    repo_url: str
    base: str
    head: str
    status: str
    ahead_by: int
    behind_by: int
    merge_base_sha: str
    url: str
    commits: tuple[CommitSummary, ...]
    files: tuple[ChangedFile, ...]
    files_total: int
    unmatched_paths: tuple[str, ...] = ()
    patch_included: bool = True


@dataclass(frozen=True)
class RefChecks:
    """CI / check results for a branch, tag or commit, with or without a PR.

    ``sha`` is the commit the provider resolved ``ref`` to, so the answer stays
    bound to that exact commit even if the branch moves afterwards.
    """

    repo_url: str
    ref: str
    sha: str
    checks: tuple[CheckRecord, ...]

    @property
    def status(self) -> CIStatus:
        """Aggregate verdict: one failing check fails the ref, none reported is unknown."""
        conclusions = {check.conclusion for check in self.checks}
        if not conclusions:
            return CIStatus.UNKNOWN
        if conclusions & {CheckConclusion.FAILING, CheckConclusion.CANCELED}:
            return CIStatus.FAILING
        if CheckConclusion.PENDING in conclusions:
            return CIStatus.PENDING
        if CheckConclusion.UNKNOWN in conclusions:
            return CIStatus.UNKNOWN
        return CIStatus.PASSING

    def counts(self) -> dict[str, int]:
        """Number of checks per conclusion, for a one-line summary."""
        tally: dict[str, int] = {}
        for check in self.checks:
            tally[check.conclusion.value] = tally.get(check.conclusion.value, 0) + 1
        return tally


_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class InvalidImageDigestError(ValueError):
    """An image digest that is not ``sha256:`` followed by 64 hex characters."""


def normalize_image_digest(value: str) -> str:
    """Return *value* as a lower-case ``sha256:<64 hex>`` digest, or raise.

    Only SHA-256 is accepted: it is the algorithm GitHub attestation subjects and
    OCI manifest digests use here, and a digest in another form (a bare hex
    string, a tag, a truncated prefix) names no image, so it is rejected rather
    than guessed at.

    Raises:
        InvalidImageDigestError: *value* is not a full SHA-256 digest.
    """
    digest = value.strip().lower()
    if _SHA256_DIGEST.fullmatch(digest) is None:
        raise InvalidImageDigestError(
            f"{value!r} is not an image digest: expected 'sha256:' followed by 64 hex "
            "characters (the manifest digest, e.g. from `docker buildx imagetools inspect`)"
        )
    return digest


@dataclass(frozen=True)
class ProvenanceAttestation:
    """One Sigstore bundle a provider holds for an artifact digest, not yet verified.

    ``bundle`` is the bundle's JSON text exactly as the provider stored it.
    Nothing here has been checked: it becomes evidence only once a provenance
    verifier accepts it.
    """

    bundle: str


@dataclass(frozen=True)
class VerifiedProvenance:
    """What one cryptographically verified build attestation proves.

    Every field comes from the verified signing certificate or from the signed
    in-toto statement, never from the provider's response around the bundle.
    ``workflow`` is the build signer URI: the workflow file and ref that ran the
    signing job (for a reusable workflow, the reusable workflow's own file).
    """

    subject_name: str
    subject_digest: str
    source_repository: str
    commit_sha: str
    ref: str
    workflow: str
    run_url: str


@dataclass(frozen=True)
class ImageProvenance:
    """The verified provenance of one image digest in one repository.

    ``attestations`` holds every attestation that verified (at least one);
    ``rejected`` holds the reason each attestation the provider returned but the
    verifier refused, so a partially trusted set is visible rather than silently
    reduced.
    """

    repo_url: str
    digest: str
    attestations: tuple[VerifiedProvenance, ...]
    rejected: tuple[str, ...] = ()


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
class WorkloadDelegation:
    """A workload (e.g. a resident's ServiceAccount) allowed to act for one owner.

    The workload exchange admits a caller through a mapping with
    ``owner_from: delegation`` only while an active record names the caller's
    verifier and subject: the token then speaks for ``owner_id`` with the workload
    as its actor (``act.sub``). The verifier, not the issuer, keys the record:
    several clusters can share one issuer string, but each verifier pins its own
    cluster's signing keys.
    """

    id: UUID
    verifier: str
    subject: str
    owner_id: str
    tenant_id: str
    created_by: str
    created_at: datetime
    name: str = ""
    resident_id: str = ""
    revoked_at: datetime | None = None


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
