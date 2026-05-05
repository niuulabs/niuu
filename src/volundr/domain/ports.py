"""Port interfaces for the domain layer.

Ports define the boundaries between the domain and infrastructure.
Adapters implement these interfaces.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from niuu.ports.credentials import CredentialStorePort  # noqa: F401
from niuu.ports.git import (
    GitAuthError,  # noqa: F401
    GitProvider,  # noqa: F401
    GitRepoNotFoundError,  # noqa: F401
    GitWorkflowProvider,  # noqa: F401
)
from niuu.ports.integrations import IntegrationRepository  # noqa: F401
from volundr.domain.models import (
    Chronicle,
    ClusterResourceInfo,
    CommunicationRoute,
    CredentialMapping,
    ForgeProfile,
    IntegrationConnection,
    MCPServerConfig,
    Model,
    ModelProvider,
    PodSpecAdditions,
    Preset,
    Principal,
    ProjectMapping,
    PromptScope,
    PVCRef,
    RealtimeEvent,
    RoomParticipantInfo,
    SavedPrompt,
    SecretInfo,
    SecretMountSpec,
    SecretType,
    Session,
    SessionCommunicationTarget,
    SessionEvent,
    SessionEventType,
    SessionSpec,
    SessionStatus,
    Stats,
    StorageQuota,
    Tenant,
    TenantMembership,
    TimelineEvent,
    TokenUsageRecord,
    TrackerConnectionStatus,
    TrackerIssue,
    TranslatedResources,
    User,
    Workspace,
    WorkspaceStatus,
    WorkspaceTemplate,
)


@dataclass(frozen=True)
class PodStartResult:
    """Result from starting session pods."""

    chat_endpoint: str
    code_endpoint: str
    pod_name: str


class SessionRepository(ABC):
    """Port for session persistence operations."""

    @abstractmethod
    async def create(self, session: Session) -> Session:
        """Persist a new session."""

    @abstractmethod
    async def get(self, session_id: UUID) -> Session | None:
        """Retrieve a session by ID. Returns None if not found."""

    @abstractmethod
    async def get_many(self, session_ids: list[UUID]) -> dict[UUID, Session]:
        """Retrieve multiple sessions by ID. Returns a dict mapping ID to Session."""

    @abstractmethod
    async def list(
        self,
        status: SessionStatus | None = None,
        tenant_id: str | None = None,
        owner_id: str | None = None,
    ) -> list[Session]:
        """Retrieve sessions, filtered by status/tenant/owner."""

    @abstractmethod
    async def update(self, session: Session) -> Session:
        """Update an existing session."""

    @abstractmethod
    async def delete(self, session_id: UUID) -> bool:
        """Delete a session. Returns True if deleted, False if not found."""


class CommunicationRouteRepository(ABC):
    """Port for external communication route persistence."""

    @abstractmethod
    async def upsert_route(self, route: CommunicationRoute) -> CommunicationRoute:
        """Create or update a communication route."""

    @abstractmethod
    async def get_active_route(
        self,
        platform: str,
        conversation_id: str,
        thread_id: str | None,
    ) -> CommunicationRoute | None:
        """Return the active route for a platform/conversation/thread."""

    @abstractmethod
    async def deactivate_routes_for_session(self, session_id: UUID) -> int:
        """Deactivate all active routes for a session."""

    @abstractmethod
    async def list_routes_for_session(self, session_id: UUID) -> list[CommunicationRoute]:
        """List routes registered for a session."""


class CommunicationCursorRepository(ABC):
    """Port for persistent inbound-consumer cursors."""

    @abstractmethod
    async def get_cursor(self, platform: str, consumer_key: str) -> str | None:
        """Return the stored cursor for a consumer, if any."""

    @abstractmethod
    async def upsert_cursor(self, platform: str, consumer_key: str, cursor: str) -> None:
        """Persist the latest consumer cursor."""


class ChronicleRepository(ABC):
    """Port for chronicle persistence operations."""

    @abstractmethod
    async def create(self, chronicle: Chronicle) -> Chronicle:
        """Persist a new chronicle."""

    @abstractmethod
    async def get(self, chronicle_id: UUID) -> Chronicle | None:
        """Retrieve a chronicle by ID. Returns None if not found."""

    @abstractmethod
    async def get_by_session(self, session_id: UUID) -> Chronicle | None:
        """Retrieve the most recent chronicle for a session."""

    @abstractmethod
    async def list(
        self,
        project: str | None = None,
        repo: str | None = None,
        model: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Chronicle]:
        """Retrieve chronicles with optional filters."""

    @abstractmethod
    async def update(self, chronicle: Chronicle) -> Chronicle:
        """Update an existing chronicle."""

    @abstractmethod
    async def delete(self, chronicle_id: UUID) -> bool:
        """Delete a chronicle. Returns True if deleted, False if not found."""

    @abstractmethod
    async def get_chain(self, chronicle_id: UUID) -> list[Chronicle]:
        """Retrieve the reforge chain for a chronicle.

        Walks the parent_chronicle_id links to build the full chain,
        ordered from oldest ancestor to the given chronicle.
        """


class TimelineRepository(ABC):
    """Port for chronicle timeline event persistence."""

    @abstractmethod
    async def add_event(self, event: TimelineEvent) -> TimelineEvent:
        """Persist a new timeline event."""

    @abstractmethod
    async def get_events(self, chronicle_id: UUID) -> list[TimelineEvent]:
        """Retrieve all timeline events for a chronicle, ordered by t."""

    @abstractmethod
    async def get_events_by_session(self, session_id: UUID) -> list[TimelineEvent]:
        """Retrieve all timeline events for a session, ordered by t."""

    @abstractmethod
    async def delete_by_chronicle(self, chronicle_id: UUID) -> int:
        """Delete all timeline events for a chronicle. Returns count deleted."""


class PodManager(ABC):
    """Port for managing session pods (Skuld, code-server, terminal)."""

    @abstractmethod
    async def start(
        self,
        session: Session,
        spec: SessionSpec,
    ) -> PodStartResult:
        """Start pods for a session.

        Args:
            session: The session to start pods for.
            spec: Merged SessionSpec from the contributor pipeline.

        Returns:
            PodStartResult containing chat_endpoint, code_endpoint, and pod_name.
        """

    @abstractmethod
    async def stop(self, session: Session) -> bool:
        """Stop pods for a session.

        Returns:
            True if stopped successfully.
        """

    @abstractmethod
    async def status(self, session: Session) -> SessionStatus:
        """Get the current status of session pods."""

    @abstractmethod
    async def wait_for_ready(self, session: Session, timeout: float) -> SessionStatus:
        """Block until infrastructure is ready or failed.

        Returns RUNNING if ready, FAILED if failed/timeout.
        Each adapter implements this optimally for its backend.
        """


class StatsRepository(ABC):
    """Port for retrieving aggregate statistics."""

    @abstractmethod
    async def get_stats(self) -> Stats:
        """Retrieve aggregate statistics for the dashboard.

        Returns:
            Stats containing session counts, token usage, and cost for today.
        """


class TokenTracker(ABC):
    """Port for tracking token usage."""

    @abstractmethod
    async def record_usage(
        self,
        session_id: UUID,
        tokens: int,
        provider: ModelProvider,
        model: str,
        cost: float | None = None,
    ) -> TokenUsageRecord:
        """Record token usage for a session.

        Args:
            session_id: The session ID.
            tokens: Number of tokens used.
            provider: The model provider (cloud or local).
            model: The model identifier.
            cost: Cost in USD (only for cloud models).

        Returns:
            The created TokenUsageRecord.
        """

    @abstractmethod
    async def get_session_usage(self, session_id: UUID) -> int:
        """Get total tokens used by a session.

        Args:
            session_id: The session ID.

        Returns:
            Total tokens used by the session.
        """


class PricingProvider(ABC):
    """Port for model pricing and metadata."""

    @abstractmethod
    def get_price(self, model_id: str) -> float | None:
        """Get the price per million tokens for a model.

        Args:
            model_id: The model identifier.

        Returns:
            Price per million tokens in USD, or None if model not found or free.
        """

    @abstractmethod
    def list_models(self) -> list[Model]:
        """List all available models with pricing and metadata.

        Returns:
            List of available models.
        """


# GitProvider, GitWorkflowProvider, GitAuthError, GitRepoNotFoundError
# are re-exported from niuu.ports.git at the top of this file.


class EventBroadcaster(ABC):
    """Port for broadcasting real-time events to connected clients.

    This port enables Server-Sent Events (SSE) functionality for real-time
    updates of session state changes and statistics.
    """

    @abstractmethod
    async def publish(self, event: RealtimeEvent) -> None:
        """Publish an event to all connected subscribers.

        Args:
            event: The event to broadcast.
        """

    @abstractmethod
    async def subscribe(self) -> AsyncGenerator[RealtimeEvent, None]:
        """Subscribe to receive events.

        Returns:
            An async generator that yields events as they are published.
            The generator should be used in an async for loop and will
            continue yielding events until the subscription is cancelled.
        """


class ProfileProvider(ABC):
    """Port for reading forge profiles from configuration.

    Profiles are configuration-driven (YAML, CRDs) rather than
    stored in a database. This port is read-only.
    """

    @abstractmethod
    def get(self, name: str) -> ForgeProfile | None:
        """Retrieve a profile by name. Returns None if not found."""

    @abstractmethod
    def list(self, workload_type: str | None = None) -> list[ForgeProfile]:
        """Retrieve profiles, optionally filtered by workload type."""

    @abstractmethod
    def get_default(self, workload_type: str) -> ForgeProfile | None:
        """Retrieve the default profile for a workload type."""


class MutableProfileProvider(ProfileProvider):
    """Extended profile provider that supports write operations."""

    @abstractmethod
    async def create(self, profile: ForgeProfile) -> ForgeProfile:
        """Create a new profile. Raises ValueError if name already exists."""

    @abstractmethod
    async def update(self, name: str, profile: ForgeProfile) -> ForgeProfile:
        """Update an existing profile. Raises ValueError if not found."""

    @abstractmethod
    async def delete(self, name: str) -> bool:
        """Delete a profile by name. Returns True if deleted."""


class TemplateProvider(ABC):
    """Port for reading workspace templates from configuration.

    Templates are configuration-driven (YAML, CRDs) rather than
    stored in a database. This port is read-only.
    """

    @abstractmethod
    def get(self, name: str) -> WorkspaceTemplate | None:
        """Retrieve a template by name. Returns None if not found."""

    @abstractmethod
    def list(self, workload_type: str | None = None) -> list[WorkspaceTemplate]:
        """Retrieve templates, optionally filtered by workload type."""


class EventSink(ABC):
    """Port for session event sinks.

    Each sink receives raw SessionEvents and maps them to its own wire
    format (SQL rows, AMQP messages, OTel spans, etc.). Sinks are
    fire-and-forget from the pipeline's perspective — failures in one
    sink do not block others.
    """

    @abstractmethod
    async def emit(self, event: SessionEvent) -> None:
        """Emit a single event to this sink."""

    @abstractmethod
    async def emit_batch(self, events: list[SessionEvent]) -> None:
        """Emit a batch of events."""

    @abstractmethod
    async def flush(self) -> None:
        """Flush any buffered events. Called on graceful shutdown."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources (connections, channels, exporters)."""

    @property
    @abstractmethod
    def sink_name(self) -> str:
        """Human-readable sink name for logging/metrics."""

    @property
    @abstractmethod
    def healthy(self) -> bool:
        """Whether this sink is currently accepting events."""


class SessionEventRepository(ABC):
    """Read-side port for querying persisted session events."""

    @abstractmethod
    async def get_events(
        self,
        session_id: UUID,
        event_types: list[SessionEventType] | None = None,
        after: datetime | None = None,
        before: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[SessionEvent]:
        """Retrieve events for a session with optional filters."""

    @abstractmethod
    async def get_event_counts(
        self,
        session_id: UUID,
    ) -> dict[str, int]:
        """Get event type counts for a session."""

    @abstractmethod
    async def get_token_timeline(
        self,
        session_id: UUID,
        bucket_seconds: int = 300,
    ) -> list[dict]:
        """Get token usage bucketed over time."""

    @abstractmethod
    async def delete_by_session(self, session_id: UUID) -> int:
        """Delete all events for a session. Returns count deleted."""


class SavedPromptRepository(ABC):
    """Port for saved prompt persistence operations."""

    @abstractmethod
    async def create(self, prompt: SavedPrompt) -> SavedPrompt:
        """Persist a new saved prompt."""

    @abstractmethod
    async def get(self, prompt_id: UUID) -> SavedPrompt | None:
        """Retrieve a saved prompt by ID."""

    @abstractmethod
    async def list(
        self,
        scope: PromptScope | None = None,
        repo: str | None = None,
    ) -> list[SavedPrompt]:
        """List saved prompts with optional scope/repo filter."""

    @abstractmethod
    async def update(self, prompt: SavedPrompt) -> SavedPrompt:
        """Update an existing saved prompt."""

    @abstractmethod
    async def delete(self, prompt_id: UUID) -> bool:
        """Delete a saved prompt. Returns True if deleted."""

    @abstractmethod
    async def search(self, query: str) -> list[SavedPrompt]:
        """Search prompts by name and content (case-insensitive)."""


class PresetRepository(ABC):
    """Port for preset persistence operations."""

    @abstractmethod
    async def create(self, preset: Preset) -> Preset:
        """Persist a new preset."""

    @abstractmethod
    async def get(self, preset_id: UUID) -> Preset | None:
        """Retrieve a preset by ID. Returns None if not found."""

    @abstractmethod
    async def get_by_name(self, name: str) -> Preset | None:
        """Retrieve a preset by name. Returns None if not found."""

    @abstractmethod
    async def list(
        self,
        cli_tool: str | None = None,
        is_default: bool | None = None,
    ) -> list[Preset]:
        """List presets with optional filters."""

    @abstractmethod
    async def update(self, preset: Preset) -> Preset:
        """Update an existing preset."""

    @abstractmethod
    async def delete(self, preset_id: UUID) -> bool:
        """Delete a preset. Returns True if deleted."""

    @abstractmethod
    async def clear_default(self, cli_tool: str) -> None:
        """Clear the is_default flag for all presets with the given cli_tool."""


class MCPServerProvider(ABC):
    """Port for reading available MCP server configurations."""

    @abstractmethod
    def list(self) -> list[MCPServerConfig]:
        """Return all available MCP server configurations."""

    @abstractmethod
    def get(self, name: str) -> MCPServerConfig | None:
        """Return a specific MCP server config by name."""


class SecretManager(ABC):
    """Port for managing Kubernetes secrets available to sessions."""

    @abstractmethod
    async def list(self) -> list[SecretInfo]:
        """List available secrets (filtered by label selector)."""

    @abstractmethod
    async def get(self, name: str) -> SecretInfo | None:
        """Get a specific secret's metadata by name."""

    @abstractmethod
    async def create(self, name: str, data: dict[str, str]) -> SecretInfo:
        """Create a new secret with the given key-value pairs.

        Raises:
            SecretAlreadyExistsError: If a secret with this name already exists.
            SecretValidationError: If the name is invalid.
        """


class SecretAlreadyExistsError(Exception):
    """Raised when attempting to create a secret that already exists."""


class SecretValidationError(Exception):
    """Raised when a secret name fails validation."""


class IssueTrackerProvider(ABC):
    """Port for external issue tracker integration.

    Supports Linear, Jira, GitHub Issues, or any other tracker.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the name of this provider (e.g., 'linear', 'jira')."""

    @abstractmethod
    async def check_connection(self) -> TrackerConnectionStatus:
        """Check the connection status to the issue tracker."""

    @abstractmethod
    async def search_issues(
        self,
        query: str,
        project_id: str | None = None,
    ) -> list[TrackerIssue]:
        """Search issues by query string."""

    @abstractmethod
    async def get_recent_issues(
        self,
        project_id: str,
        limit: int = 10,
    ) -> list[TrackerIssue]:
        """Get recent issues for a project."""

    @abstractmethod
    async def get_issue(self, issue_id: str) -> TrackerIssue | None:
        """Get a single issue by ID or identifier."""

    @abstractmethod
    async def update_issue_status(
        self,
        issue_id: str,
        status: str,
    ) -> TrackerIssue:
        """Update the status of an issue."""


class ProjectMappingRepository(ABC):
    """Port for project mapping persistence (repo URL -> tracker project)."""

    @abstractmethod
    async def create(self, mapping: ProjectMapping) -> ProjectMapping:
        """Persist a new project mapping."""

    @abstractmethod
    async def list(self) -> list[ProjectMapping]:
        """Retrieve all project mappings."""

    @abstractmethod
    async def get_by_repo(self, repo_url: str) -> ProjectMapping | None:
        """Retrieve a mapping by repo URL."""

    @abstractmethod
    async def delete(self, mapping_id: UUID) -> bool:
        """Delete a mapping. Returns True if deleted."""


class TenantRepository(ABC):
    """Port for tenant persistence operations."""

    @abstractmethod
    async def create(self, tenant: Tenant) -> Tenant:
        """Persist a new tenant."""

    @abstractmethod
    async def get(self, tenant_id: str) -> Tenant | None:
        """Retrieve a tenant by ID."""

    @abstractmethod
    async def get_by_path(self, path: str) -> Tenant | None:
        """Retrieve a tenant by its materialized path."""

    @abstractmethod
    async def list(self, parent_id: str | None = None) -> list[Tenant]:
        """List tenants, optionally filtered by parent."""

    @abstractmethod
    async def get_ancestors(self, path: str) -> list[Tenant]:
        """Get all ancestors of a tenant path (root first)."""

    @abstractmethod
    async def update(self, tenant: Tenant) -> Tenant:
        """Update a tenant."""

    @abstractmethod
    async def delete(self, tenant_id: str) -> bool:
        """Delete a tenant. Returns True if deleted."""


class UserRepository(ABC):
    """Port for user persistence operations."""

    @abstractmethod
    async def create(self, user: User) -> User:
        """Persist a new user."""

    @abstractmethod
    async def get(self, user_id: str) -> User | None:
        """Retrieve a user by ID (IDP sub)."""

    @abstractmethod
    async def get_by_email(self, email: str) -> User | None:
        """Retrieve a user by email."""

    @abstractmethod
    async def list(self) -> list[User]:
        """List all users."""

    @abstractmethod
    async def update(self, user: User) -> User:
        """Update a user."""

    @abstractmethod
    async def delete(self, user_id: str) -> bool:
        """Delete a user. Returns True if deleted."""

    @abstractmethod
    async def add_membership(self, membership: TenantMembership) -> TenantMembership:
        """Add a user to a tenant with a role."""

    @abstractmethod
    async def get_memberships(self, user_id: str) -> list[TenantMembership]:
        """Get all tenant memberships for a user."""

    @abstractmethod
    async def get_members(self, tenant_id: str) -> list[TenantMembership]:
        """Get all members of a tenant."""

    @abstractmethod
    async def remove_membership(self, user_id: str, tenant_id: str) -> bool:
        """Remove a user from a tenant. Returns True if removed."""


class IdentityPort(ABC):
    """Port for identity/authentication operations."""

    @abstractmethod
    async def validate_token(self, raw_token: str) -> Principal:
        """Validate a JWT and extract a Principal.

        Raises:
            InvalidTokenError: If the token is invalid or expired.
        """

    @abstractmethod
    async def get_or_provision_user(self, principal: Principal) -> User:
        """Get existing user or provision on first login (JIT).

        Raises:
            UserProvisioningError: If provisioning fails.
        """


class InvalidTokenError(Exception):
    """Raised when a JWT is invalid, expired, or malformed."""


class UserProvisioningError(Exception):
    """Raised when JIT user provisioning fails."""


@dataclass(frozen=True)
class Resource:
    """A resource for authorization checks."""

    kind: str  # "session" | "secret" | "tenant" | "preset"
    id: str
    attr: dict


class AuthorizationPort(ABC):
    """Port for authorization decisions."""

    @abstractmethod
    async def is_allowed(
        self,
        principal: Principal,
        action: str,
        resource: Resource,
    ) -> bool:
        """Check if a principal is allowed to perform an action on a resource."""

    @abstractmethod
    async def filter_allowed(
        self,
        principal: Principal,
        action: str,
        resources: list[Resource],
    ) -> list[Resource]:
        """Filter a list of resources to only those the principal can access."""


class SecretRepository(ABC):
    """Port for secrets storage (OpenBao / Vault compatible)."""

    @abstractmethod
    async def store_credential(
        self,
        path: str,
        data: dict[str, str],
    ) -> None:
        """Store a credential at the given path."""

    @abstractmethod
    async def get_credential(
        self,
        path: str,
    ) -> dict[str, str] | None:
        """Get credential data at the given path.

        Returns None if not found.
        """

    @abstractmethod
    async def delete_credential(self, path: str) -> bool:
        """Delete a credential at the given path."""

    @abstractmethod
    async def list_credentials(
        self,
        path_prefix: str,
    ) -> list[str]:
        """List credential keys under a path prefix."""

    @abstractmethod
    async def provision_user(
        self,
        user_id: str,
        tenant_id: str,
    ) -> None:
        """Create OpenBao policy and K8s auth role for a user.

        Called during JIT user provisioning (NIU-97).
        """

    @abstractmethod
    async def deprovision_user(self, user_id: str) -> None:
        """Remove OpenBao policy and K8s auth role for a user."""

    @abstractmethod
    async def create_session_secrets(
        self,
        session_id: str,
        user_id: str,
        mounts: list[SecretMountSpec],
    ) -> None:
        """Create ephemeral session secrets and Vault Agent config."""

    @abstractmethod
    async def delete_session_secrets(
        self,
        session_id: str,
    ) -> None:
        """Delete ephemeral session secrets."""


class StoragePort(ABC):
    """Port for persistent volume claim management."""

    @property
    def home_mount_path(self) -> str:
        return "/volundr/home"

    @property
    def workspace_mount_path(self) -> str:
        return "/volundr/sessions"

    @abstractmethod
    async def provision_user_storage(
        self,
        user_id: str,
        quota: StorageQuota,
    ) -> PVCRef:
        """Create a home PVC for a user. Idempotent."""

    @abstractmethod
    async def create_session_workspace(
        self,
        session_id: str,
        user_id: str,
        tenant_id: str,
        workspace_gb: int | None = None,
        name: str | None = None,
        source_url: str | None = None,
        source_ref: str | None = None,
    ) -> PVCRef:
        """Create a workspace PVC for a session."""

    @abstractmethod
    async def archive_session_workspace(
        self,
        session_id: str,
    ) -> None:
        """Archive a session's workspace PVC (soft delete)."""

    @abstractmethod
    async def delete_workspace(
        self,
        session_id: str,
    ) -> None:
        """Permanently delete a session's workspace PVC (explicit user action only)."""

    @abstractmethod
    async def get_user_storage_usage(
        self,
        user_id: str,
    ) -> int:
        """Get total storage in GB currently in use by a user."""

    @abstractmethod
    async def deprovision_user_storage(
        self,
        user_id: str,
    ) -> None:
        """Delete a user's home PVC."""

    async def list_workspaces(
        self,
        user_id: str,
        status: WorkspaceStatus | None = None,
    ) -> list[Workspace]:
        """List workspace PVCs for a user, optionally filtered by status."""
        return []

    async def list_all_workspaces(
        self,
        status: WorkspaceStatus | None = None,
    ) -> list[Workspace]:
        """List all workspace PVCs (admin), optionally filtered by status."""
        return []

    async def get_workspace_by_session(
        self,
        session_id: str,
    ) -> Workspace | None:
        """Get the workspace PVC for a session. Returns None if not found."""
        return None


class GatewayPort(ABC):
    """Port for Gateway API resource management.

    Provides gateway configuration that PodManager adapters pass through
    to the Skuld Helm chart so it can create its own HTTPRoute resources.
    The Gateway resource itself (TLS, listeners) is managed by Volundr.
    """

    @abstractmethod
    def get_gateway_config(self) -> dict[str, str]:
        """Return gateway configuration for session routing.

        Returns:
            Dict with gateway_name, gateway_namespace, and any
            JWT/auth config needed by Skuld's HTTPRoute template.
            Empty dict when gateway routing is not configured.
        """


class SecretMountStrategy(ABC):
    """Strategy for mounting a specific secret type into a session pod."""

    @abstractmethod
    def secret_type(self) -> SecretType:
        """Return the secret type this strategy handles."""

    @abstractmethod
    def default_mount_spec(
        self,
        secret_path: str,
        secret_data: dict,
    ) -> SecretMountSpec:
        """Return the default mount spec for this secret type."""

    @abstractmethod
    def validate(self, secret_data: dict) -> list[str]:
        """Validate secret data. Returns list of error messages (empty = valid)."""


class SecretInjectionPort(ABC):
    """Port for generating pod spec additions for secret injection.

    Adapters return pod spec fragments (annotations, volumes, mounts) that
    configure how secrets are injected into session pods.  Volundr never
    sees secret values in production.
    """

    @abstractmethod
    async def pod_spec_additions(
        self,
        user_id: str,
        session_id: str,
    ) -> PodSpecAdditions:
        """Return pod spec contributions for secret injection."""

    @abstractmethod
    async def ensure_secret_provider_class(
        self,
        user_id: str,
        credential_mappings: list[CredentialMapping],
        session_id: str | None = None,
    ) -> None:
        """Create or update backend resources to mount the given credentials.

        Each ``CredentialMapping`` describes a credential and how its fields
        should be rendered (as env vars, files, or both).

        For agent-injector adapters this creates a ConfigMap with Go
        templates that render credentials directly to env vars and files.
        For file-based or in-memory adapters this is a no-op.
        """

    @abstractmethod
    async def provision_user(self, user_id: str) -> None:
        """Create backend resources for a new user."""

    @abstractmethod
    async def deprovision_user(self, user_id: str) -> None:
        """Clean up backend resources for a removed user."""

    async def cleanup_session(self, session_id: str) -> None:
        """Clean up per-session resources (ConfigMaps, etc.). Default no-op."""


class ResourceProvider(ABC):
    """Port for discovering, validating, and translating cluster resources.

    Adapters may query the K8s API (device-plugin or DRA) or return
    static resource types for dev/test environments.
    """

    @abstractmethod
    async def discover(self) -> ClusterResourceInfo:
        """Discover available resource types and cluster capacity."""

    @abstractmethod
    def translate(self, resource_config: dict) -> TranslatedResources:
        """Translate user-friendly resource config to K8s-native primitives.

        Args:
            resource_config: User-friendly format, e.g.
                {"cpu": "4", "memory": "8Gi", "gpu": "1", "gpu_type": "A100"}

        Returns:
            TranslatedResources with requests, limits, nodeSelector,
            tolerations, and runtimeClassName.
        """

    @abstractmethod
    def validate(
        self,
        resource_config: dict,
        cluster_info: ClusterResourceInfo | None = None,
    ) -> list[str]:
        """Validate resource config, optionally against cluster capacity.

        Returns:
            List of validation error messages (empty = valid).
        """


class GitWorkspacePort(ABC):
    """Port for local git operations on session workspace directories.

    Provides git commands that run directly on the filesystem (not via
    a git provider API). Used in mini/local mode where workspaces are
    accessible on the host. K8s mode may provide a remote variant that
    executes commands inside pods.
    """

    @abstractmethod
    async def diff_files(self, workspace_dir: str) -> list[dict[str, str | int]]:
        """Return changed files with addition/deletion stats.

        Runs ``git diff HEAD --numstat`` in the workspace directory.

        Returns:
            List of dicts with keys: path, additions, deletions.
        """

    @abstractmethod
    async def file_diff(
        self,
        workspace_dir: str,
        path: str,
        base_branch: str = "main",
    ) -> str | None:
        """Return unified diff for a single file.

        Runs ``git diff <base_branch>...HEAD -- <path>`` in the workspace.

        Returns:
            Unified diff string, or None if the file has no diff.
        """

    @abstractmethod
    async def commit_log(
        self,
        workspace_dir: str,
        since: str | None = None,
    ) -> list[dict[str, str]]:
        """Return recent commits.

        Runs ``git log`` in the workspace directory.

        Returns:
            List of dicts with keys: hash, short_hash, message.
        """

    @abstractmethod
    async def pr_status(
        self,
        workspace_dir: str,
    ) -> dict[str, Any] | None:
        """Return PR status for the current branch via ``gh pr view``.

        Returns:
            Dict with keys: number, url, state, mergeable, checks.
            None if no PR exists or ``gh`` is not installed.
        """

    @abstractmethod
    async def current_branch(self, workspace_dir: str) -> str | None:
        """Return the current branch name.

        Returns:
            Branch name string, or None on error.
        """


# PATRepository — re-exported from shared niuu module
from niuu.ports.pat_repository import PATRepository  # noqa: F401, E402


@dataclass(frozen=True)
class SessionContext:
    """Read-only context for contributors."""

    principal: Principal | None = None
    definition: str | None = None
    template_name: str | None = None
    profile_name: str | None = None
    terminal_restricted: bool = False
    credential_names: tuple[str, ...] = ()
    integration_ids: tuple[str, ...] = ()
    integration_connections: tuple[IntegrationConnection, ...] = ()
    resource_config: dict = field(default_factory=dict)
    system_prompt: str = ""
    initial_prompt: str = ""
    workload_type: str = "session"
    workload_config: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SessionContribution:
    """Output from a single contributor."""

    values: dict[str, Any] = field(default_factory=dict)
    pod_spec: PodSpecAdditions | None = None


class SessionContributor(ABC):
    """Port for contributing session configuration.

    Each contributor wraps a single port/adapter and produces
    Helm values and/or pod spec additions for session startup.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution: ...

    async def cleanup(self, session: Session, context: SessionContext) -> None:
        """Clean up on stop/delete. Default no-op."""
        return


class SessionRoomPort(ABC):
    """Port for interacting with a live flock room owned by Skuld."""

    @abstractmethod
    async def send_room_message(
        self,
        session_id: UUID,
        text: str,
        *,
        source: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send a plain human message into the session room."""

    @abstractmethod
    async def send_directed_room_message(
        self,
        session_id: UUID,
        target_peer_id: str,
        text: str,
        *,
        source: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send a directed human message to a specific room participant."""

    @abstractmethod
    async def list_room_participants(self, session_id: UUID) -> list[RoomParticipantInfo]:
        """Return the current room participants for a live session."""


class SessionCommunicationPort(ABC):
    """Port for querying live external communication targets from a session."""

    @abstractmethod
    async def list_communication_targets(
        self,
        session_id: UUID,
    ) -> list[SessionCommunicationTarget]:
        """Return active external communication targets for a live session."""
