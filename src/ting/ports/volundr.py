"""Volundr port — interface for session lifecycle management."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Protocol

from niuu.domain.models import Principal
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


@dataclass(frozen=True)
class ActivityEvent:
    """An activity or session lifecycle event received from Volundr SSE.

    For activity events: state is "active"/"idle"/"tool_executing", session_status is empty.
    For session lifecycle events: session_status is "stopped"/"failed"/etc., state is empty.
    """

    session_id: str
    state: str
    metadata: dict
    owner_id: str
    session_status: str = ""


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

    @abstractmethod
    async def spawn_session(
        self,
        request: SpawnRequest,
        *,
        auth_token: str | None = None,
        principal: Principal | None = None,
    ) -> VolundrSession:
        raise NotImplementedError

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

    @abstractmethod
    async def subscribe_activity(self) -> AsyncGenerator[ActivityEvent, None]:
        """Subscribe to the Volundr SSE stream for session_activity events."""
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
