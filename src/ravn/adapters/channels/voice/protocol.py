"""The agent-service WebSocket wire, as realtime voice clients speak it.

Every frame is ``{type, id, payload}``. A response echoes the request's ``id``;
``payload`` is always an object. An error is ``{type: "error", id, payload:
{error, code}}`` (the client also treats any ``*.error`` type as an error).
"""

from __future__ import annotations

from typing import Any

# Client → resident.
AUTH = "auth"
PING = "ping"
TOOLS_LIST = "tools.list"
TOOLS_CALL = "tools.call"
TOOLS_ENABLE = "tools.enable"
TOOLS_GROUP_TOGGLE = "tools.group.toggle"
PERSONA_GET = "persona.get"
PERSONA_LIST = "persona.list"
SESSION_START = "session.start"
SESSION_LOG = "session.log"
SESSION_END = "session.end"
VOICE_EPHEMERAL = "voice.ephemeral"
GATEWAY_CREDENTIALS = "gateway.credentials"
DICTATION_CREDENTIAL = "dictation.credential"
MEMORY_ENROLLMENT = "memory.enrollment"

# Resident → client.
AUTH_OK = "auth.ok"
PONG = "pong"
TOOLS_LIST_RESULT = "tools.list.result"
TOOLS_CALL_RESULT = "tools.call.result"
TOOLS_CALL_ERROR = "tools.call.error"
TOOLS_ENABLE_RESULT = "tools.enable.result"
PERSONA_DATA = "persona.data"
PERSONA_LIST_RESULT = "persona.list.result"
SESSION_STARTED = "session.started"
SESSION_LOG_ACK = "session.log.ack"
SESSION_ENDED = "session.ended"
VOICE_EPHEMERAL_RESULT = "voice.ephemeral.result"
GATEWAY_CREDENTIALS_RESULT = "gateway.credentials.result"
ERROR = "error"


class ErrorCodes:
    """``payload.code`` values the client reads."""

    UNKNOWN_TYPE = "UNKNOWN_TYPE"
    INVALID_PARAMS = "INVALID_PARAMS"
    INVALID_MESSAGE = "INVALID_MESSAGE"
    UNSUPPORTED_PROVIDER = "UNSUPPORTED_PROVIDER"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    SESSION_ERROR = "SESSION_ERROR"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ProtocolError(Exception):
    """A request the resident answers with an error frame."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def frame(message_type: str, message_id: str, payload: dict[str, Any] | None = None) -> dict:
    return {"type": message_type, "id": message_id, "payload": payload or {}}


def error_frame(message_id: str, message: str, code: str) -> dict:
    return frame(ERROR, message_id, {"error": message, "code": code})


def parse_frame(raw: Any) -> tuple[str, str, dict[str, Any]]:
    """Return ``(type, id, payload)`` of a client frame, or raise :class:`ProtocolError`."""
    if not isinstance(raw, dict):
        raise ProtocolError("message must be a JSON object", ErrorCodes.INVALID_MESSAGE)
    message_type = raw.get("type")
    message_id = raw.get("id")
    if not isinstance(message_type, str) or not message_type:
        raise ProtocolError("message.type is required", ErrorCodes.INVALID_MESSAGE)
    if not isinstance(message_id, str) or not message_id:
        raise ProtocolError("message.id is required", ErrorCodes.INVALID_MESSAGE)
    payload = raw.get("payload") or {}
    if not isinstance(payload, dict):
        raise ProtocolError("message.payload must be an object", ErrorCodes.INVALID_MESSAGE)
    return message_type, message_id, payload
