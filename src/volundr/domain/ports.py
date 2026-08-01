"""Port interfaces for the domain layer.

Ports define the boundaries between the domain and infrastructure.
Adapters implement these interfaces.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from credentials.ports import (  # noqa: F401
    MCPServerProvider,
    SecretAlreadyExistsError,
    SecretManager,
    SecretMountStrategy,
    SecretValidationError,
)
from identity.models import Resource  # noqa: F401
from identity.ports import AuthorizationPort, TenantRepository, UserRepository  # noqa: F401
from niuu.domain.outcome import OutcomeField
from niuu.ports.credentials import CredentialRefreshLockPort, CredentialStorePort  # noqa: F401
from niuu.ports.git import (
    GitAuthError,  # noqa: F401
    GitProvider,  # noqa: F401
    GitRepoNotFoundError,  # noqa: F401
    GitWorkflowProvider,  # noqa: F401
)
from niuu.ports.identity import (
    IdentityPort,  # noqa: F401
    InvalidTokenError,  # noqa: F401
    UserProvisioningError,  # noqa: F401
)
from niuu.ports.integrations import IntegrationRepository  # noqa: F401
from niuu.ports.session_proxy import SessionProxyTarget
from tracker.ports import IssueTrackerProvider, ProjectMappingRepository  # noqa: F401
from volundr.domain.models import (  # noqa: F401
    Chronicle,
    ClusterResourceInfo,
    CommunicationRoute,
    CredentialEnrollment,
    CredentialEnrollmentPoll,
    CredentialMapping,
    DeviceToken,
    ExternalSessionRecord,
    IntegrationConnection,
    LaunchSpec,
    MCPServerConfig,
    Model,
    ModelProvider,
    PodSpecAdditions,
    Principal,
    ProjectMapping,
    PromptScope,
    PushMessage,
    PVCRef,
    RealtimeEvent,
    ResidentBackend,
    ResidentCondition,
    ResidentDeploymentProfile,
    ResidentEndpoint,
    ResidentEngine,
    ResidentLogPage,
    ResidentObservedState,
    ResidentRuntime,
    ResidentSession,
    RoomParticipantInfo,
    SavedPrompt,
    SecretInfo,
    SecretMountSpec,
    SecretType,
    Session,
    SessionCommunicationTarget,
    SessionEvent,
    SessionEventType,
    SessionLogEntry,
    SessionSpan,
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
)

__all__ = [
    "AuthorizationPort",
    "CredentialStorePort",
    "CredentialRefreshLockPort",
    "GitAuthError",
    "GitProvider",
    "GitRepoNotFoundError",
    "GitWorkflowProvider",
    "IntegrationRepository",
    "PATRepository",
    "Resource",
]


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
    async def list_stale_running(self, older_than: datetime) -> list[Session]:
        """Return RUNNING sessions whose last_active is at/before older_than.

        Used by liveness reconciliation to find sessions whose broker has gone
        silent (no activity heartbeat) and should be marked stopped.
        """

    @abstractmethod
    async def delete(self, session_id: UUID) -> bool:
        """Delete a session. Returns True if deleted, False if not found."""


@dataclass(frozen=True)
class OpenShellCredentialGrantToken:
    """OAuth token response returned to an OpenShell sandbox supervisor."""

    access_token: str
    expires_in: int = 300
    token_type: str = "Bearer"


class OpenShellCredentialGrantPort(ABC):
    """Exchange an OpenShell sandbox JWT-SVID for one authorized credential."""

    @abstractmethod
    async def exchange_credential_grant(
        self,
        *,
        client_assertion: str,
        client_assertion_type: str,
        grant_type: str,
        audience: str,
        scope: str,
    ) -> OpenShellCredentialGrantToken:
        """Validate the sandbox identity and return a short-lived credential value."""


class CredentialEnrollmentRepository(ABC):
    """Persistence boundary for interactive credential enrollment attempts."""

    @abstractmethod
    async def save(self, enrollment: CredentialEnrollment) -> CredentialEnrollment:
        """Create or update an enrollment."""

    @abstractmethod
    async def get(self, enrollment_id: UUID) -> CredentialEnrollment | None:
        """Get one enrollment by ID."""

    @abstractmethod
    async def find_active(self, connection_id: str) -> CredentialEnrollment | None:
        """Return the active enrollment for a connection, if one exists."""

    @abstractmethod
    async def list_expired_active(self, now: datetime) -> list[CredentialEnrollment]:
        """Return active enrollments whose user challenge has expired."""


class CredentialEnrollmentRunnerPort(ABC):
    """Launch and inspect a trusted interactive provider-login runtime."""

    @abstractmethod
    def supports_enrollment(self, method: str) -> bool:
        """Return whether this runner implements the configured enrollment method."""

    @abstractmethod
    async def start_enrollment(
        self,
        enrollment: CredentialEnrollment,
    ) -> CredentialEnrollment:
        """Launch the runtime and return the user-facing challenge."""

    @abstractmethod
    async def poll_enrollment(
        self,
        enrollment: CredentialEnrollment,
    ) -> CredentialEnrollmentPoll:
        """Inspect the runtime without exposing secret values to the caller."""

    @abstractmethod
    async def cancel_enrollment(self, enrollment: CredentialEnrollment) -> None:
        """Destroy any runtime resources belonging to the enrollment."""


class ExternalSessionProvider(ABC):
    """Port for discovering CLI sessions that live outside Volundr.

    Implementations scan a harness's native session store (e.g. Claude
    Code's ``~/.claude/projects`` or Codex's ``~/.codex/sessions``) so
    stopped or live sessions can be listed and imported as Volundr
    sessions.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique provider key (e.g. 'claude-code', 'codex')."""
        raise NotImplementedError

    @property
    @abstractmethod
    def harness(self) -> str:
        """CLI harness the sessions belong to ('claude' or 'codex')."""
        raise NotImplementedError

    @abstractmethod
    async def list_sessions(self) -> list[ExternalSessionRecord]:
        """Discover sessions in the harness's store, newest first."""
        raise NotImplementedError

    @abstractmethod
    async def get_session(self, external_id: str) -> ExternalSessionRecord | None:
        """Look up a single session by its native identifier."""
        raise NotImplementedError


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

    def initial_chat_endpoint(self, session: Session) -> str | None:
        """Return the deterministic chat endpoint before pods are ready, if known."""
        return None

    def initial_code_endpoint(self, session: Session) -> str | None:
        """Return the deterministic code endpoint before pods are ready, if known."""
        return None

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

        Returns the backend's current status when the wait ends.
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


class DeviceTokenRepository(ABC):
    """Persistence port for per-user push device registrations."""

    @abstractmethod
    async def upsert(self, device: DeviceToken) -> DeviceToken:
        """Register a device, or refresh it if the (owner, token) already exists."""

    @abstractmethod
    async def list_for_owner(self, owner_id: str) -> list[DeviceToken]:
        """Return every device registered by an owner."""

    @abstractmethod
    async def delete(self, owner_id: str, token: str) -> bool:
        """Remove a device registration. Returns True if one was deleted."""


class NotificationChannel(ABC):
    """Outbound port for delivering a push to a user's devices.

    Implementations: APNs (direct), an outbound webhook relay, or a logging
    no-op. Selected via the dynamic-adapter config so adding a channel is
    config-only.
    """

    @abstractmethod
    async def send(self, message: PushMessage, devices: list[DeviceToken]) -> None:
        """Deliver a push. ``devices`` is the owner's registered devices (may be
        empty for relay channels that resolve recipients themselves)."""


class AttentionNotifier(ABC):
    """Port the session service calls when a session needs the user.

    Decouples session logic from the push delivery mechanism: the session
    service emits the intent, an adapter resolves devices and dispatches.
    """

    @abstractmethod
    async def notify_needs_input(
        self,
        session: Session,
        *,
        kind: str,
        prompt: str,
        request_id: str,
    ) -> None:
        """Alert the session owner that the session is blocked awaiting them."""


class LaunchSpecProvider(ABC):
    """Read port for system-scope launch specs (config-seeded, read-only)."""

    @abstractmethod
    def get(self, name: str) -> LaunchSpec | None:
        """Retrieve a system launch spec by name. Returns None if not found."""

    @abstractmethod
    def list(self, workload_type: str | None = None) -> list[LaunchSpec]:
        """Retrieve system launch specs, optionally filtered by workload type."""

    @abstractmethod
    def get_default(self, workload_type: str) -> LaunchSpec | None:
        """Retrieve the default system launch spec for a workload type."""


class LaunchSpecRepository(ABC):
    """Persistence port for user-scope launch specs (DB-stored, CRUD)."""

    @abstractmethod
    async def create(self, spec: LaunchSpec) -> LaunchSpec:
        """Persist a new user launch spec."""

    @abstractmethod
    async def get(self, spec_id: UUID) -> LaunchSpec | None:
        """Retrieve a user launch spec by id. Returns None if not found."""

    @abstractmethod
    async def get_by_name(self, name: str) -> LaunchSpec | None:
        """Retrieve a user launch spec by name. Returns None if not found."""

    @abstractmethod
    async def list(
        self,
        cli_tool: str | None = None,
        is_default: bool | None = None,
    ) -> list[LaunchSpec]:
        """List user launch specs with optional filters."""

    @abstractmethod
    async def update(self, spec: LaunchSpec) -> LaunchSpec:
        """Update an existing user launch spec."""

    @abstractmethod
    async def delete(self, spec_id: UUID) -> bool:
        """Delete a user launch spec. Returns True if deleted."""

    @abstractmethod
    async def clear_default(self, cli_tool: str) -> None:
        """Clear is_default for all user launch specs with the given cli_tool."""


class ResidentDeploymentProfileProvider(ABC):
    """Read-only provider for operator-approved resident deployment profiles."""

    @abstractmethod
    def get(self, profile_id: str) -> ResidentDeploymentProfile | None:
        """Return one enabled profile by id."""

    @abstractmethod
    def list(self) -> list[ResidentDeploymentProfile]:
        """Return every enabled deployment profile."""


@dataclass(frozen=True)
class ResidentRuntimeObservation:
    """Normalized backend state returned by a resident deployment controller."""

    observed_state: ResidentObservedState
    backend_ref: dict[str, Any] = field(default_factory=dict)
    endpoints: list[ResidentEndpoint] = field(default_factory=list)
    conditions: list[ResidentCondition] = field(default_factory=list)


class ResidentRuntimeController(ABC):
    """Backend lifecycle port for long-lived resident runtimes."""

    @property
    @abstractmethod
    def backend(self) -> ResidentBackend:
        """Return the backend implemented by this controller."""

    @abstractmethod
    def supports(self, profile: ResidentDeploymentProfile) -> bool:
        """Return whether this controller implements the complete profile."""

    @abstractmethod
    async def deploy(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        """Create or converge the backend resources for a resident."""

    @abstractmethod
    async def reconcile(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        """Converge and observe the backend resources owned by a resident."""

    @abstractmethod
    async def restart(
        self,
        runtime: ResidentRuntime,
        profile: ResidentDeploymentProfile,
    ) -> ResidentRuntimeObservation:
        """Restart a running resident without replacing its durable storage."""

    @abstractmethod
    async def suspend(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        """Suspend a resident while retaining its durable storage."""

    @abstractmethod
    async def resume(self, runtime: ResidentRuntime) -> ResidentRuntimeObservation:
        """Resume a suspended resident."""

    @abstractmethod
    async def delete(self, runtime: ResidentRuntime) -> bool:
        """Delete backend resources, returning whether a resource existed."""


class ResidentRuntimeLogReader(ABC):
    """Optional backend port for normalized resident logs."""

    @abstractmethod
    async def logs(
        self,
        runtime: ResidentRuntime,
        *,
        lines: int,
        sources: tuple[str, ...],
        min_level: str,
    ) -> ResidentLogPage:
        """Return recent logs from the runtime-owning backend."""


class ResidentRuntimeProxyTargetResolver(ABC):
    """Optional backend port for routing resident Skuld traffic."""

    @abstractmethod
    def resident_proxy_target(self, runtime: ResidentRuntime) -> SessionProxyTarget | None:
        """Resolve the backend service reached by the shared session proxy."""


class ResidentDeviceApprover(ABC):
    """Optional runtime capability for approving resident engine devices."""

    @abstractmethod
    async def approve_resident_device(
        self,
        runtime: ResidentRuntime,
        *,
        request_id: str,
        gateway_token: str,
    ) -> None:
        """Approve an authenticated device challenge inside the owning runtime."""


class ResidentChatConnection(ABC):
    """One normalized shared-chat connection to a resident engine session."""

    @abstractmethod
    async def receive(self) -> dict[str, Any]:
        """Receive the next shared-chat frame."""

    @abstractmethod
    async def send(self, frame: dict[str, Any]) -> None:
        """Send one shared-chat command."""

    @abstractmethod
    async def close(self) -> None:
        """Close the engine connection."""


class ResidentSessionController(ABC):
    """Engine protocol port for sessions hosted by a resident runtime."""

    @property
    @abstractmethod
    def engine(self) -> ResidentEngine:
        """Return the resident engine implemented by this controller."""

    @abstractmethod
    async def list_sessions(self, runtime: ResidentRuntime) -> list[ResidentSession]:
        """List engine-owned sessions for one resident."""

    @abstractmethod
    async def create_session(
        self,
        runtime: ResidentRuntime,
        *,
        title: str,
        model: str,
    ) -> ResidentSession:
        """Create one durable engine-owned session."""

    @abstractmethod
    async def delete_session(self, runtime: ResidentRuntime, session_id: UUID) -> None:
        """Delete one engine-owned session and transcript."""

    @abstractmethod
    async def connect_chat(
        self,
        runtime: ResidentRuntime,
        session_id: UUID,
    ) -> ResidentChatConnection:
        """Open a normalized shared-chat connection."""


class ResidentRuntimeRepository(ABC):
    """Persistence port for long-lived resident runtime records."""

    @abstractmethod
    async def create(self, runtime: ResidentRuntime) -> ResidentRuntime:
        """Persist a new resident runtime."""

    @abstractmethod
    async def get(self, runtime_id: UUID) -> ResidentRuntime | None:
        """Return a resident runtime by id."""

    @abstractmethod
    async def get_by_owner_name(self, owner_id: str, name: str) -> ResidentRuntime | None:
        """Return a resident runtime by owner and name."""

    @abstractmethod
    async def list(
        self,
        *,
        tenant_id: str,
        owner_id: str | None = None,
    ) -> list[ResidentRuntime]:
        """List resident runtimes in a tenant, optionally scoped to one owner."""

    @abstractmethod
    async def list_for_reconciliation(self) -> list[ResidentRuntime]:
        """List every resident runtime requiring backend observation."""

    @abstractmethod
    async def update(self, runtime: ResidentRuntime) -> ResidentRuntime:
        """Persist the current resident runtime state."""

    @abstractmethod
    async def add_usage(
        self,
        runtime_id: UUID,
        *,
        tokens: int,
        cost: float,
        message_count: int,
    ) -> ResidentRuntime | None:
        """Atomically add usage totals and return the updated resident."""

    @abstractmethod
    async def delete(self, runtime_id: UUID) -> bool:
        """Delete a resident runtime record."""


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


class SessionEventLogRepository(ABC):
    """Read/write port for the durable, append-only, full-fidelity event log.

    This is the transcript source of truth (see :class:`SessionLogEntry`).
    Writes are idempotent on (session_id, seq) so the producer can retry
    at-least-once without creating duplicates, and reads are cursor-based so any
    client can resume a full replay from the last seq it saw.
    """

    @abstractmethod
    async def append(self, entries: list[SessionLogEntry]) -> int:
        """Append entries idempotently (by session_id+seq).

        Returns the number of entries submitted. Existing (session_id, seq)
        rows are left untouched (ON CONFLICT DO NOTHING).
        """

    @abstractmethod
    async def read_after(
        self,
        session_id: UUID,
        after_seq: int = 0,
        limit: int = 1000,
    ) -> list[SessionLogEntry]:
        """Return entries with seq > after_seq, ordered by seq ascending."""

    @abstractmethod
    async def latest_seq(self, session_id: UUID) -> int:
        """Return the highest seq stored for a session, or 0 if none."""

    async def detect_conflicts(self, entries: list[SessionLogEntry]) -> list[int]:
        """Return the seqs that ALREADY have a stored row with a DISTINCT payload.

        ``append`` is idempotent on ``(session_id, seq)`` (ON CONFLICT DO NOTHING),
        so re-appending the SAME frame is a silent no-op (INV-3 idempotency). That
        same swallow, however, would also hide a genuine bug: a *different* payload
        re-using a seq another frame already owns. This method is the off-hot-path,
        queryable detection signal for that case (INV-3c): it reads back the stored
        rows for the candidate seqs and returns the seqs whose stored frame DIFFERS
        from the candidate (by ``payload``/``kind``/``role``/``request_id``).

        This is a CONCRETE default built on :meth:`read_after` so in-memory fakes
        inherit it for free. It is never called from ``append``; callers opt in on a
        cold/validation path. Returns an empty list when ``entries`` is empty.
        """
        if not entries:
            return []

        by_session: dict[UUID, dict[int, SessionLogEntry]] = {}
        for entry in entries:
            by_session.setdefault(entry.session_id, {})[entry.seq] = entry

        conflicts: list[int] = []
        for session_id, candidates in by_session.items():
            min_seq = min(candidates) - 1
            max_seq = max(candidates)
            stored = await self.read_after(
                session_id,
                after_seq=min_seq,
                limit=max_seq - min_seq,
            )
            for row in stored:
                candidate = candidates.get(row.seq)
                if candidate is None:
                    continue
                if _log_entries_conflict(candidate, row):
                    conflicts.append(row.seq)
        conflicts.sort()
        return conflicts


def _log_entries_conflict(candidate: SessionLogEntry, stored: SessionLogEntry) -> bool:
    """True when two entries share a seq but carry materially different content."""
    return (
        candidate.payload != stored.payload
        or candidate.kind != stored.kind
        or candidate.role != stored.role
        or candidate.request_id != stored.request_id
    )


class SessionSpanRepository(ABC):
    """Read/write port for persisted session trace spans."""

    @abstractmethod
    async def upsert_span(self, span: SessionSpan) -> SessionSpan:
        """Create or replace a span."""

    @abstractmethod
    async def finish_span(
        self,
        span_id: UUID,
        ended_at: datetime,
        status: str,
        attributes: dict | None = None,
    ) -> SessionSpan | None:
        """Finish an existing span and optionally merge terminal attributes."""

    @abstractmethod
    async def list_spans(self, session_id: UUID) -> list[SessionSpan]:
        """List all spans for a session ordered by start time."""

    @abstractmethod
    async def delete_by_session(self, session_id: UUID) -> int:
        """Delete all spans for a session. Returns count deleted."""


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
        """Permanently delete a session-scoped workspace PVC."""

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

    def resolve_session_workspace_path(self, session_id: str) -> str | None:
        """Resolve the local filesystem path for a session workspace if accessible.

        Returns ``None`` when the storage backend does not expose the workspace
        on the current host filesystem.
        """
        return None


class ArchiveStorePort(ABC):
    """Port for persisted session transcript/log archives."""

    @abstractmethod
    def load_manifest(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> dict[str, Any] | None:
        """Load an archive manifest if one has already been materialized."""

    @abstractmethod
    def load_transcript(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> dict[str, Any] | None:
        """Load an archived transcript payload if one exists."""

    @abstractmethod
    def load_aggregated_logs(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> dict[str, Any] | None:
        """Load archived aggregate logs if they exist."""

    @abstractmethod
    def write_archive(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path,
        transcript_payload: dict[str, Any],
        aggregated_logs: dict[str, Any],
        chronicle_payload: dict[str, Any] | None = None,
        timeline_payload: dict[str, Any] | None = None,
        event_source_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Materialize an archive snapshot and return its manifest."""

    @abstractmethod
    def transcript_json_path(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> Path:
        """Return the local JSON transcript artifact path."""

    @abstractmethod
    def transcript_markdown_path(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> Path:
        """Return the local Markdown transcript artifact path."""

    @abstractmethod
    def archive_root(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> Path:
        """Return the root directory for a session archive."""


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
        tenant_id: str | None = None,
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
    launch_spec: str | None = None
    runtime_backend: str = "kubernetes"
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
class SessionPersona:
    """Runtime persona fields Forge can apply without owning Ravn storage."""

    name: str
    system_prompt: str
    consumes_event_types: tuple[str, ...] = ()
    produces_event_type: str = ""
    produces_schema: dict[str, OutcomeField] = field(default_factory=dict)


class SessionPersonaProvider(ABC):
    """Resolve a persona in the launching user's catalog."""

    @abstractmethod
    async def get(self, owner_id: str, name: str) -> SessionPersona | None:
        """Return the selected persona, or None when it does not exist."""


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
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def contribute(
        self,
        session: Session,
        context: SessionContext,
    ) -> SessionContribution:
        raise NotImplementedError

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
