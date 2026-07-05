"""session_join tool — membership in other sessions' Skuld rooms.

Lets a resident ravn walk into the room of a session it launched (join),
watch its own memberships (list), speak into a joined room (post), and
walk out (leave). Backed by the process-wide
:class:`~ravn.session_join.SessionJoinManager` so the drive loop and this
tool share one set of memberships.
"""

from __future__ import annotations

import logging

from ravn.adapters.tools.platform_tools import _err, _ok
from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort
from ravn.session_join import SessionJoinManager

logger = logging.getLogger(__name__)

_PERMISSION_PLATFORM = "platform:api"


class SessionJoinTool(ToolPort):
    """Join, observe, post into, and leave other sessions' rooms.

    Actions:
    - ``join``  — join a session's room (requires ``session_id`` and
      ``chat_endpoint`` from the Forge session record).
    - ``leave`` — leave a joined room (requires ``session_id``).
    - ``list``  — describe current memberships.
    - ``post``  — post a message into a joined room (requires
      ``session_id`` and ``text``).
    """

    def __init__(self, manager: SessionJoinManager) -> None:
        self._manager = manager

    @property
    def name(self) -> str:
        return "session_join"

    @property
    def description(self) -> str:
        return (
            "Join another session's chat room as a participant to observe "
            "and interact (e.g. a workflow session you launched). Actions: "
            "join (session_id + chat_endpoint required), leave (session_id "
            "required), list, post (session_id + text required). You can "
            "only join sessions owned by your operator."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["join", "leave", "list", "post"],
                    "description": "Membership operation to perform.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Target session id (required for join/leave/post).",
                },
                "chat_endpoint": {
                    "type": "string",
                    "description": (
                        "The session's chat_endpoint from the Forge session "
                        "record (required for join)."
                    ),
                },
                "text": {
                    "type": "string",
                    "description": "Message to post into the joined room (post).",
                },
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_PLATFORM

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        action = input.get("action", "")
        try:
            match action:
                case "join":
                    return await self._join(input)
                case "leave":
                    return await self._leave(input)
                case "list":
                    return _ok(self._manager.joined())
                case "post":
                    return await self._post(input)
                case _:
                    return _err(f"Unknown action: {action!r}")
        except (ValueError, RuntimeError) as exc:
            return _err(str(exc))

    async def _join(self, input: dict) -> ToolResult:
        session_id = str(input.get("session_id") or "").strip()
        chat_endpoint = str(input.get("chat_endpoint") or "").strip()
        if not session_id or not chat_endpoint:
            return _err("session_id and chat_endpoint are required for join")
        return _ok(await self._manager.join(session_id, chat_endpoint))

    async def _leave(self, input: dict) -> ToolResult:
        session_id = str(input.get("session_id") or "").strip()
        if not session_id:
            return _err("session_id is required for leave")
        left = await self._manager.leave(session_id)
        if not left:
            return _err(f"Not joined to session {session_id}")
        return _ok({"left": session_id})

    async def _post(self, input: dict) -> ToolResult:
        session_id = str(input.get("session_id") or "").strip()
        text = str(input.get("text") or "").strip()
        if not session_id or not text:
            return _err("session_id and text are required for post")
        posted = await self._manager.post(session_id, text)
        if not posted:
            return _err(f"Not joined to session {session_id} — join it first")
        return _ok({"posted": session_id})
