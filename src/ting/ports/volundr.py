"""Volundr port — interface for session lifecycle management."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from niuu.domain.delivery import (
    AcceptancePolicy,
    CandidateEvidence,
    CheckReceipt,
    EvidenceValidationReport,
    IntegrationCandidateInspection,
    IntegrationReceipt,
    MergeReceipt,
    MergeRequest,
    ResolvedRef,
    ReviewCandidate,
    WorkspaceAllocation,
)
from niuu.domain.models import Principal
from ravn.domain.persona_document import PortablePersonaDefinition
from ting.domain.models import PRStatus


@dataclass(frozen=True)
class SpawnRequest:
    """Everything needed to spawn a Volundr session for a run.

    ``workload_config`` accepts two persona formats — both are valid on
    the wire and normalised to the dict form inside
    ``RavnFlockContributor.contribute()``:

    **Legacy (list[str])** — all personas share the global ``llm_config``::

        workload_config = {
            "personas": ["coordinator", "reviewer"],
            "llm_config": {...},
        }

    **New (list[dict])** — per-persona overrides merged on top of
    the global ``llm_config`` via ``niuu.domain.llm_merge.merge_llm``::

        workload_config = {
            "personas": [
                {"name": "coordinator"},
                {"name": "reviewer", "llm": {"primary_alias": "powerful"}},
            ],
            "llm_config": {...},  # global fallback
        }

    See ``niuu.domain.llm_merge`` for merge semantics.
    """

    name: str
    repo: str
    branch: str
    model: str
    tracker_issue_id: str
    tracker_issue_url: str
    system_prompt: str
    initial_prompt: str
    base_branch: str
    workload_type: str = "default"
    workload_config: dict = field(default_factory=dict)
    definition: str | None = None
    profile: str | None = None
    integration_ids: list[str] = field(default_factory=list)
    credential_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VolundrSession:
    """Minimal session info returned from Volundr."""

    id: str
    name: str
    status: str
    tracker_issue_id: str | None
    chat_endpoint: str | None = None
    cluster_name: str = ""
    repo: str = ""
    branch: str = ""
    base_branch: str = ""
    workload_type: str = "default"
    activity_state: str | None = None
    activity_metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ActivityEvent:
    """An activity or session lifecycle event received from Volundr SSE.

    For activity events: state is "active"/"idle"/"tool_executing"/"error",
    session_status is empty.
    For session lifecycle events: session_status is "stopped"/"failed"/etc., state is empty.
    """

    session_id: str
    state: str
    metadata: dict
    owner_id: str
    session_status: str = ""


@dataclass(frozen=True)
class ActivityStreamConnected:
    """Sentinel ``subscribe_activity()`` yields once the SSE connection is
    actually open — the underlying request succeeded and the server
    accepted the stream — before any ``ActivityEvent``. A connect that
    hangs, times out, or fails raises before ever yielding this.

    This is the signal a caller needs to start a "how long has this
    genuinely been connected" clock: starting it at task start would count
    a connect timeout as time spent healthily connected (the timeout and a
    plausible "stable" threshold can be the same order of magnitude), and
    waiting for the first real ``ActivityEvent`` never fires for a cluster
    that legitimately has no sessions right now.
    """


@dataclass(frozen=True)
class PublicSessionLogEntry:
    """One public Forge session-log record.

    Ting deliberately consumes the default public log view. Implementations must
    not request internal tool or reasoning blocks when serving this contract.
    """

    session_id: str
    seq: int
    kind: str
    payload: dict
    ts: datetime
    role: str | None = None
    request_id: str | None = None


@dataclass(frozen=True)
class PublicSessionLogPage:
    """One public log page with progress through Forge's raw cursor space."""

    entries: tuple[PublicSessionLogEntry, ...]
    scanned_through: int
    has_more: bool


class VolundrPort(ABC):
    """Abstract interface for Volundr session management."""

    @property
    def name(self) -> str:
        """Human-readable adapter name (used for connection_id targeting)."""
        return ""

    @property
    def target_id(self) -> str:
        """Stable identifier used for explicit target selection."""
        return self.name

    @property
    def tags(self) -> list[str]:
        """Tags of the registered instance (for label-based targeting)."""
        return []

    @property
    def base_url(self) -> str:
        """Base URL of the target Forge cluster (for diagnostics/logging)."""
        return ""

    @abstractmethod
    async def spawn_session(
        self,
        request: SpawnRequest,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> VolundrSession:
        raise NotImplementedError

    async def resolve_delivery_ref(
        self,
        repository: str,
        ref: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> ResolvedRef:
        """Resolve a provider ref to an immutable commit through Forge."""
        raise NotImplementedError("This Volundr adapter does not support delivery ref resolution")

    async def validate_delivery_evidence(
        self,
        evidence: CandidateEvidence,
        *,
        policy_id: str,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> EvidenceValidationReport:
        """Validate authenticated evidence using a server-registered acceptance policy."""
        raise NotImplementedError("This Volundr adapter does not support delivery evidence")

    async def reconcile_delivery_merge(
        self,
        request: MergeRequest,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> MergeReceipt:
        """Read authoritative remote publication state for an exact candidate."""
        raise NotImplementedError("This Volundr adapter does not support delivery publication")

    async def describe_delivery_policy(
        self,
        *,
        campaign_id: str,
        repository: str,
        policy_id: str,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> AcceptancePolicy:
        """Read one server-configured delivery policy for deterministic observation."""
        raise NotImplementedError("This Volundr adapter does not support delivery policies")

    async def inspect_delivery_candidate(
        self,
        repository: str,
        review_number: int,
        *,
        campaign_id: str,
        policy_id: str,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> tuple[ReviewCandidate, CheckReceipt]:
        """Read exact remote review identity and configured checks."""
        raise NotImplementedError("This Volundr adapter does not support delivery inspection")

    async def inspect_delivery_integration(
        self,
        allocation: WorkspaceAllocation,
        receipt: IntegrationReceipt,
        *,
        policy_id: str,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> IntegrationCandidateInspection:
        """Verify a signed integration receipt against the live workspace candidate."""
        raise NotImplementedError("This Volundr adapter does not support integration inspection")

    async def inspect_delivery_integration_chain(
        self,
        allocation: WorkspaceAllocation,
        receipts: tuple[IntegrationReceipt, ...],
        *,
        policy_id: str,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> IntegrationCandidateInspection:
        """Verify the complete signed integration chain and its live final workspace."""
        raise NotImplementedError(
            "This Volundr adapter does not support integration-chain inspection"
        )

    @abstractmethod
    async def get_session(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> VolundrSession | None:
        raise NotImplementedError

    @abstractmethod
    async def list_sessions(
        self,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> list[VolundrSession]:
        raise NotImplementedError

    @abstractmethod
    async def get_pr_status(self, session_id: str) -> PRStatus:
        raise NotImplementedError

    @abstractmethod
    async def get_chronicle_summary(self, session_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    async def send_message(
        self,
        session_id: str,
        message: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> None:
        """Send a human message to a running Volundr session."""
        raise NotImplementedError

    async def send_directed_room_message(
        self,
        session_id: str,
        target_peer_id: str,
        message: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> None:
        """Send a directed human message to a specific room participant.

        Implementations that do not expose room-level addressing can safely
        fall back to the generic session message channel.
        """
        await self.send_message(
            session_id,
            message,
            auth_token=auth_token,
            principal=principal,
        )

    async def publish_workflow_event(
        self,
        session_id: str,
        event_type: str,
        content: str,
        *,
        payload: dict | None = None,
        request_id: str,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> None:
        """Publish an idempotently identified event into a running workflow mesh."""
        raise NotImplementedError("This Volundr adapter cannot publish workflow events")

    async def get_current_portable_persona(
        self,
        persona_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> PortablePersonaDefinition | None:
        """Return the caller-scoped current portable persona source."""
        raise NotImplementedError(
            f"{type(self).__name__} does not expose caller-scoped portable personas"
        )

    async def get_workflow_gates(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> list[dict]:
        """Return workflow gates for a running Volundr session."""
        return []

    async def resolve_workflow_gate(
        self,
        session_id: str,
        gate_id: str,
        decision: str,
        *,
        notes: str = "",
        source: str = "ting",
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> dict:
        """Resolve a workflow gate for a running Volundr session."""
        raise NotImplementedError

    async def get_help_requests(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> list[dict]:
        """Return peer help requests (agent questions) for a running session."""
        return []

    async def answer_help_request(
        self,
        session_id: str,
        request_id: str,
        answer: str,
        *,
        source: str = "ting",
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> dict:
        """Answer a pending peer help request in a running session."""
        raise NotImplementedError

    @abstractmethod
    async def stop_session(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> None:
        """Stop a running Volundr session."""
        raise NotImplementedError

    @abstractmethod
    async def list_integration_ids(
        self,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> list[str]:
        """Return the IDs of the user's enabled integrations on this Volundr instance."""
        raise NotImplementedError

    @abstractmethod
    async def list_repos(
        self,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> list[dict]:
        """Return configured repos from Volundr, each with at least 'org', 'name', 'url'."""
        raise NotImplementedError

    @abstractmethod
    async def get_last_assistant_message(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> str:
        """Return the last assistant message from the session's conversation history."""
        raise NotImplementedError

    @abstractmethod
    async def get_conversation(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> dict:
        """Return the full conversation history for a session."""
        raise NotImplementedError

    async def get_public_session_log_page(
        self,
        session_id: str,
        *,
        after: int,
        limit: int,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> PublicSessionLogPage:
        """Return one cursor page from Forge's public session log."""
        raise NotImplementedError("This Volundr adapter does not expose public session logs")

    @abstractmethod
    async def subscribe_activity(
        self,
    ) -> AsyncGenerator[ActivityEvent | ActivityStreamConnected, None]:
        """Subscribe to the Volundr SSE stream for session_activity events.

        Yields ``ActivityStreamConnected`` exactly once, as the first item,
        once the connection is genuinely open — before any
        ``ActivityEvent``. A connect that never succeeds (hangs to timeout,
        refused, etc.) raises without ever yielding it.
        """
        raise NotImplementedError
        yield  # type: ignore[misc]  # pragma: no cover


class VolundrFactory(Protocol):
    """Protocol for resolving per-owner Volundr adapters.

    Returns all Guild-discovered Volundr targets visible to an owner.
    Never falls back to an unauthenticated adapter unless the concrete
    factory is explicitly configured for anonymous local development.
    """

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        """Return all authenticated Volundr adapters for *owner_id*.

        Returns an empty list when Guild has no visible Volundr targets.
        Callers must treat an empty result as a hard error or skip the
        operation with an explicit warning.
        """
        raise NotImplementedError

    async def for_owner_with_unresolved(self, owner_id: str) -> tuple[list[VolundrPort], int]:
        """Like ``for_owner``, but also reports how many of the owner's
        registered instances could not be resolved into a usable adapter
        this call (e.g. a missing credential, or adapter construction
        failed).

        A caller that must know it saw *every* registered cluster — not
        just every cluster that happened to resolve cleanly — uses this
        instead of ``for_owner``: treating a silently-skipped instance the
        same as "this owner has no such cluster" is exactly the silent
        degradation ``.claude/rules/no-fallbacks.md`` forbids. A factory
        that never silently drops a registered instance (e.g. local/mini
        mode) reports 0 unresolved.
        """
        raise NotImplementedError

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        """Return the primary (first) authenticated adapter, or ``None``."""
        raise NotImplementedError

    async def for_connection(self, owner_id: str, connection_id: str) -> VolundrPort | None:
        """Return the owner's adapter for a specific connection id or name.

        Campaign-scoped reads must target the Volundr instance the session
        was launched on; ``None`` when the connection no longer resolves.
        """
        raise NotImplementedError

    async def for_principal(self, principal: Principal) -> list[VolundrPort]:
        """Return all visible adapters for a fully scoped principal."""
        raise NotImplementedError

    async def primary_for_principal(self, principal: Principal) -> VolundrPort | None:
        """Return the primary visible adapter for a fully scoped principal."""
        raise NotImplementedError
