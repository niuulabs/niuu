"""Correlated, non-terminal errors for the browser control protocol."""

from typing import Any


class ControlRecoveryError(ValueError):
    code = "question_recovery_required"


def control_error_frame(error: object, message: Any = None, *, code: str | None = None) -> dict:
    """Keep control rejection separate from completion of an assistant turn."""
    message = message if isinstance(message, dict) else {}
    code = code or getattr(error, "code", None)
    if code is None:
        kind = message.get("type")
        code = {
            "ask_user_answer": "question_answer_rejected",
            "permission_response": "permission_response_rejected",
        }.get(kind if isinstance(kind, str) else "", "control_message_rejected")
    frame = {"type": "error", "content": str(error), "code": code}
    request_id = message.get("request_id")
    if isinstance(request_id, str) and request_id:
        frame["request_id"] = request_id
    return frame
