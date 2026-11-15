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
    async def list_session_people(self, session_id: str) -> list[dict[str, Any]]:
        """``GET /sessions/{id}/people``: the members of the session's project.

        Forge answers 409 (raised as :class:`ForgeApiError`) when the session has no
        project; an empty list would read as "nobody is on it"."""

    @abstractmethod
    async def create_person_question(
        self, session_id: str, *, user_id: str, question: str, request_id: str
    ) -> dict[str, Any]:
        """``POST /sessions/{id}/person-questions``: put a question to one project member.

        Returns ``{question_id, notification_id, addressee}``. Forge refuses (409) a session
        with no project and (422) a person who is not on it, raised as
        :class:`ForgeApiError`. The same ``request_id`` is the same question."""

    @abstractmethod
    async def answer_person_question(
        self, session_id: str, question_id: str, *, answered_by_user_id: str, answered_by_name: str
    ) -> dict[str, Any]:
        """``POST /sessions/{id}/person-questions/{question_id}/answer``: settle the
        addressee's notification, naming who answered."""

    @abstractmethod
    async def get_conversation(
        self, session_id: str, *, turns: int, instance_id: str | None
    ) -> dict[str, Any]:
        """``GET /sessions/{id}/conversation`` windowed to the last ``turns`` turns."""

    @abstractmethod
    async def send_message(
        self,
        session_id: str,
        *,
        content: str,
        request_id: str,
        instance_id: str | None,
        in_reply_to: str = "",
        urgent: bool = False,
    ) -> dict[str, Any]:
        """``POST /sessions/{id}/messages`` with a caller-stable ``request_id``.

        Forge stamps the sender from the caller's session credential. ``in_reply_to`` names
        the message this one answers; ``urgent`` lets it interrupt a running turn."""

    @abstractmethod
    async def post_room_message(
        self, host_session_id: str, *, room_id: str, content: str
    ) -> dict[str, Any]:
        """``POST /sessions/{host}/room/session-message``: speak in a room the host runs.

        Forge stamps the calling session; the host accepts it only from a member of the room."""

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

    def ask_wait_max_s(self) -> float:
        """The longest one ``ask`` may wait (``ask.wait_max_s``); a larger deadline is refused."""
        raise ForgeApiError(
            "ask needs a Forge session's own MCP (the answer arrives in that session's inbox)",
            status=501,
        )

    async def ask_for_answer(
        self,
        *,
        recipient_id: str,
        instance_id: str | None,
        message_id: str,
        request_id: str,
        delivery_confirmed: bool,
        deadline_s: float,
        mesh_peer: str | None = None,
    ) -> dict[str, Any]:
        """Wait for ``recipient_id``'s reply to ``message_id``; return ``{outcome, ...}``.

        ``delivery_confirmed`` is whether the send was confirmed delivered (Forge's answer, or the
        recipient's Skuld over the mesh; a pending, unconfirmed or timed-out send is not).
        ``mesh_peer`` is the recipient's peer id when the send went over the mesh: its state
        then comes from the mesh, not Forge. Only a session's own broker holds the inbox the answer
        arrives in; other hosts refuse."""
        raise ForgeApiError(
            "ask needs a Forge session's own MCP (the answer arrives in that session's inbox)",
            status=501,
        )

    async def ask_person_answer(
        self,
        *,
        addressee: dict[str, Any],
        question: str,
        question_id: str,
        request_id: str,
        deadline_s: float,
    ) -> dict[str, Any]:
        """Show ``question`` in the session's room addressed to ``addressee`` and wait for its
        answer; return ``{outcome, ...}``.

        ``addressee`` is Forge's description of the person (``user_id``, ``display_name``,
        ``role``). The room shows the question once per ``question_id`` (a repeat only waits
        again). An answer that arrives after the wait is delivered at the next turn boundary.
        Only a session's own broker has the room and the inbox the answer arrives in; other
        hosts refuse."""
        raise ForgeApiError(
            "ask_person needs a Forge session's own MCP (the question is shown in that "
            "session's room and the answer arrives in its inbox)",
            status=501,
        )

    def mesh_host(self) -> bool:
        """Whether this session has the mesh enabled, so the mesh tools are listed."""
        return False

    async def mesh_peer_for(self, *, session_id: str | None, name: str | None) -> str | None:
        """The mesh peer id of a recipient named by session id or name, or ``None``.

        ``None`` means the recipient is not in this session's mesh peer table (or this host has
        no mesh), so the send goes through Forge. A session whose mesh is enabled but not
        running raises instead: it cannot tell, and it never falls back to Forge."""
        return None

    async def send_mesh_message(
        self,
        peer: str,
        content: str,
        *,
        expect_reply: bool,
        request_id: str,
        in_reply_to: str,
        urgent: bool = False,
        thread: str = "",
    ) -> dict[str, Any]:
        """Send a peer message to a mesh peer, stamped with this session's identity.

        Only a session's own broker has the mesh; other hosts refuse."""
        raise ForgeApiError(
            "mesh_send needs a Forge session's own MCP (the mesh belongs to that session's broker)",
            status=501,
        )

    async def mesh_peers(
        self, *, role: str, capability: str, consumes: str, produces: str
    ) -> dict[str, Any]:
        """The peers this session's mesh discovery knows, with what each announced.

        Only a session's own broker has the mesh; other hosts refuse."""
        raise ForgeApiError(
            "mesh_peers needs a Forge session's own MCP (the mesh belongs to that session's "
            "broker)",
            status=501,
        )

    async def publish_mesh_event(
        self,
        topic: str,
        content: str,
        *,
        request_id: str,
        correlation_id: str,
        in_reply_to: str,
    ) -> dict[str, Any]:
        """Publish an event onto the mesh topic, stamped with this session's identity.

        Only a session's own broker has the mesh; other hosts refuse."""
        raise ForgeApiError(
            "mesh_publish needs a Forge session's own MCP (the mesh belongs to that session's "
            "broker)",
            status=501,
        )

    async def post_room_message(
        self, room_id: str, content: str, *, host_session_id: str = "", request_id: str = ""
    ) -> dict[str, Any]:
        """Say something in a room this session is in. Only a session's own broker knows them."""
        raise ForgeApiError(
            "room_post needs a Forge session's own MCP (the session's broker knows its rooms)",
            status=501,
        )

    async def mesh_ask(
        self,
        content: str,
        *,
        producers_of: str,
        peers: list[str],
        request_id: str,
        wait_s: float,
    ) -> dict[str, Any]:
        """Put a question to the peers that produce a topic, or to chosen peers.

        Only a session's own broker has the mesh; other hosts refuse."""
        raise ForgeApiError(
            "mesh_ask needs a Forge session's own MCP (the mesh belongs to that session's broker)",
            status=501,
        )

    async def mesh_subscribe(self, *, topic: str, correlation_id: str) -> dict[str, Any]:
        """Start listening to a mesh topic or follow a thread. Only a session's own broker."""
        raise ForgeApiError(
            "mesh_subscribe needs a Forge session's own MCP (the mesh belongs to that session's "
            "broker)",
            status=501,
        )

    async def mesh_unsubscribe(self, *, topic: str, correlation_id: str) -> dict[str, Any]:
        """Stop following a topic or thread followed at runtime. Only a session's own broker."""
        raise ForgeApiError(
            "mesh_unsubscribe needs a Forge session's own MCP (the mesh belongs to that "
            "session's broker)",
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
