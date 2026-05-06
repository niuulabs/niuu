"""Volundr port — interface for session lifecycle management."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Protocol

from tyr.domain.models import PRStatus


@dataclass(frozen=True)
class SpawnRequest:
    """Everything needed to spawn a Volundr session for a raid.

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

    @abstractmethod
    async def spawn_session(
        self,
        request: SpawnRequest,
        *,
        auth_token: str | None = None,
    ) -> VolundrSession: ...

    @abstractmethod
    async def get_session(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
    ) -> VolundrSession | None: ...

    @abstractmethod
    async def list_sessions(
        self,
        *,
        auth_token: str | None = None,
    ) -> list[VolundrSession]: ...

    @abstractmethod
    async def get_pr_status(self, session_id: str) -> PRStatus: ...

    @abstractmethod
    async def get_chronicle_summary(self, session_id: str) -> str: ...

    @abstractmethod
    async def send_message(
        self,
        session_id: str,
        message: str,
        *,
        auth_token: str | None = None,
    ) -> None:
        """Send a human message to a running Volundr session."""
        ...

    @abstractmethod
    async def stop_session(
        self,
        session_id: str,
        *,
        auth_token: str | None = None,
    ) -> None:
        """Stop a running Volundr session."""
        ...

    @abstractmethod
    async def list_integration_ids(self, *, auth_token: str | None = None) -> list[str]:
        """Return the IDs of the user's enabled integrations on this Volundr instance."""
        ...

    @abstractmethod
    async def list_repos(self, *, auth_token: str | None = None) -> list[dict]:
        """Return configured repos from Volundr, each with at least 'org', 'name', 'url'."""
        ...

    @abstractmethod
    async def get_last_assistant_message(self, session_id: str) -> str:
        """Return the last assistant message from the session's conversation history."""
        ...

    @abstractmethod
    async def get_conversation(self, session_id: str) -> dict:
        """Return the full conversation history for a session."""
        ...

    @abstractmethod
    async def subscribe_activity(self) -> AsyncGenerator[ActivityEvent, None]:
        """Subscribe to the Volundr SSE stream for session_activity events."""
        ...
        yield  # type: ignore[misc]  # pragma: no cover


class VolundrFactory(Protocol):
    """Protocol for resolving per-owner Volundr adapters.

    Returns all configured **authenticated** Volundr connections for an
    owner.  Never falls back to an unauthenticated adapter — callers must
    handle the empty-list / ``None`` case explicitly.
    """

    async def for_owner(self, owner_id: str) -> list[VolundrPort]:
        """Return all authenticated Volundr adapters for *owner_id*.

        Returns an empty list when no per-user CODE_FORGE connections are
        configured.  Callers must treat an empty result as a hard error
        or skip the operation with an explicit warning.
        """
        ...

    async def primary_for_owner(self, owner_id: str) -> VolundrPort | None:
        """Return the primary (first) authenticated adapter, or ``None``."""
        ...
