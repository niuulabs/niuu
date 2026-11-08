"""Ports the Forge MCP tools call. Adapters live with each host (Skuld broker, Forge)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from niuu.domain.artifact_reports import ArtifactReport
from niuu.domain.notifications import NotificationDraft
from niuu.domain.peer_message import PeerMessage
from niuu.domain.workflow_step import WorkflowStepCloseArgs
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

    @abstractmethod
    async def create_secret_request(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /sessions/{id}/secret-requests``: ``{name, purpose, target?}``.

        Idempotent per (session, name) while a request is open: Forge returns that
        request instead of a second one."""

    @abstractmethod
    async def get_secret_request(self, session_id: str, request_id: str) -> dict[str, Any]:
        """``GET /sessions/{id}/secret-requests/{request_id}``; never carries the value."""

    @abstractmethod
    async def claim_secret_request(self, session_id: str, request_id: str) -> dict[str, Any]:
        """``POST .../{request_id}/claim``: ``{name, value}``, once, after the user fulfilled it."""

    @abstractmethod
    async def cancel_secret_request(self, session_id: str, request_id: str) -> dict[str, Any]:
        """``POST .../{request_id}/cancel``: withdraw a pending or fulfilled request."""


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

    async def request_secret(self, name: str, purpose: str, target: str | None) -> dict[str, Any]:
        """Ask the user for a secret and return ``{status: "ready", name, path}``.

        The value lands in a private file in the caller's session and is never part of the
        result. Only a session's own broker can: it owns the chat card and the file. Other
        hosts refuse."""
        raise ForgeApiError(
            "request_secret needs a Forge session's own MCP (it asks in that session's chat "
            "and writes the value into that session's runtime)",
            status=501,
        )

    async def received_peer_message(self, message_id: str) -> PeerMessage:
        """A peer message this session received, by id, so the ``reply`` tool can answer it.

        Raises :class:`niuu.domain.peer_message.PeerMessageError` when this session never
        received a message with that id. Only a session's own broker keeps its inbox;
        other hosts refuse."""
        raise ForgeApiError(
            "reply needs a Forge session's own MCP (only that session's broker holds the "
            "messages it received)",
            status=501,
        )

    def workflow_step_host(self) -> bool:
        """Whether this session hosts workflow steps, so ``workflow_step_close`` is listed."""
        return False

    async def close_workflow_step(self, args: WorkflowStepCloseArgs) -> dict[str, Any]:
        """Check ``args`` against the open step, record the close and report it.

        With no open step, or for another activation, it raises and records nothing.

        Raises :class:`niuu.domain.workflow_step.WorkflowStepCloseError` with the exact reason
        when the close is not allowed; nothing is recorded then. Only a session's own broker
        hosts steps; other hosts refuse."""
        raise ForgeApiError(
            "workflow_step_close needs a Forge session's own MCP (it closes that session's step)",
            status=501,
        )
