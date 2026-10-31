"""Reading an A2A task status the way the protocol shapes it."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def a2a_status_text(status: Mapping[str, Any] | None) -> str:
    """Return the text of a TaskStatus ``message``, else its legacy ``update`` string.

    The protocol carries the reason a task is in its state as a Message whose
    text parts hold the words; rendering that object as a string produced its
    JSON instead of the reason.
    """
    if not isinstance(status, Mapping):
        return ""
    message = status.get("message")
    if isinstance(message, Mapping):
        parts = message.get("parts")
        texts = [
            str(part.get("text") or "").strip()
            for part in parts or []
            if isinstance(part, Mapping) and str(part.get("text") or "").strip()
        ]
        return "\n".join(texts)
    return str(message or status.get("update") or "").strip()
