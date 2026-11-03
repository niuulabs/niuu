"""Ports the Forge MCP tools call. Adapters live with each host (Skuld broker, Forge)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from niuu.domain.artifact_reports import ArtifactReport
from niuu.domain.notifications import NotificationDraft
from niuu.forge_mcp.models import ForgeApiError


class ForgeClient(ABC):
    """Forge REST (``/api/v1/forge``) as the tools need it.

    Implementations raise :class:`niuu.forge_mcp.models.ForgeApiError` for HTTP
    failures or an unreachable Forge; they never return an error body as data.
    ``instance_id`` names the owning Guild node when the caller knows it; a
    facade routes session-scoped calls to the owner either way.
    """

    @abstractmethod
    async def list_notifications(self, params: dict[str, str]) -> dict[str, Any]:
        """``GET /notifications`` with already-encoded query parameters."""

    @abstractmethod
    async def list_sessions(self, params: dict[str, str]) -> list[dict[str, Any]]:
        """``GET /sessions``."""

    @abstractmethod
    async def get_session(self, session_id: str, *, instance_id: str | None) -> dict[str, Any]:
        """``GET /sessions/{id}``."""

    @abstractmethod
    async def get_conversation(
        self, session_id: str, *, turns: int, instance_id: str | None
    ) -> dict[str, Any]:
        """``GET /sessions/{id}/conversation`` windowed to the last ``turns`` turns."""

    @abstractmethod
    async def send_message(
        self, session_id: str, *, content: str, request_id: str, instance_id: str | None
    ) -> dict[str, Any]:
        """``POST /sessions/{id}/messages`` with a caller-stable ``request_id``."""

    @abstractmethod
    async def get_message_delivery(
        self, session_id: str, *, request_id: str, instance_id: str | None
    ) -> dict[str, Any]:
        """``GET /sessions/{id}/message-deliveries/{request_id}``."""

    @abstractmethod
    async def create_session(self, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /sessions`` (creates and starts)."""

    @abstractmethod
    async def start_session(self, session_id: str, *, instance_id: str | None) -> dict[str, Any]:
        """``POST /sessions/{id}/start``."""

    @abstractmethod
    async def stop_session(self, session_id: str, *, instance_id: str | None) -> dict[str, Any]:
        """``POST /sessions/{id}/stop``."""

    @abstractmethod
    async def submit_notification(
        self,
        session_id: str,
        draft: NotificationDraft,
        *,
        idempotency_key: str,
        instance_id: str | None,
    ) -> dict[str, Any]:
        """``POST /sessions/{id}/notifications``: a feed-only direct submit."""

    @abstractmethod
    async def update_description(
        self, session_id: str, changes: dict[str, str | None]
    ) -> dict[str, Any]:
        """``PUT /sessions/{id}/description``: sent keys replace, ``None`` clears."""


class ForgeMcpHost(ABC):
    """The caller's own context: who is asking, and how its notifications are recorded."""

    @property
    @abstractmethod
    def session_id(self) -> str | None:
        """The calling session's Forge id, or ``None`` for an agent outside Forge."""

    @abstractmethod
    async def environment(self) -> dict[str, Any]:
        """Describe the caller's runtime. Must never include credentials."""

    @abstractmethod
    async def notify(self, draft: NotificationDraft) -> dict[str, Any]:
        """Record a notification; return ``{notification_id, turn_id, session_seq, state}``."""

    async def report_artifact(self, report: ArtifactReport) -> dict[str, Any]:
        """Record an explicit artifact report in the caller's durable log.

        Only a session's own broker can; other hosts refuse."""
        raise ForgeApiError(
            "report_artifact needs a Forge session's own MCP (it writes that session's log)",
            status=501,
        )


class ForgeMcpFlock(ABC):
    """A Ravn flock the caller brokers. Transport only: its members do the work.

    Every outcome an implementation returns must be one a member published on
    the mesh, attributed to the member it arrived from. Implementations raise
    :class:`niuu.forge_mcp.models.FlockError` when a hand-off is refused.
    """

    @property
    @abstractmethod
    def max_wait_s(self) -> float:
        """Longest one :meth:`activity` call may wait."""

    @abstractmethod
    async def members(self) -> list[dict[str, Any]]:
        """The flock's Ravns: ``peer_id``, ``persona`` and live ``status`` each."""

    @abstractmethod
    async def delegate(self, peer_id: str, content: str) -> dict[str, Any]:
        """Hand ``content`` to one member as its next task over the mesh.

        Returns ``{message_id, peer_id, persona, cursor}``; ``cursor`` marks the
        activity log position before the hand-off, for :meth:`activity`.
        """

    @abstractmethod
    async def activity(self, *, after: int, peer_id: str, wait_s: float) -> dict[str, Any]:
        """``{cursor, activity}``: outcomes and failures members published after ``after``.

        Waits up to ``wait_s`` for the first one; an empty ``activity`` means
        none arrived in that time.
        """
