"""Persisted question evidence, distinct from live native RPC capabilities."""

import json
import os
from pathlib import Path
from typing import Any


def pending_controls(frames: list[Any]) -> tuple[dict[str, dict], dict[str, dict]]:
    questions: dict[str, dict] = {}
    permissions: dict[str, dict] = {}
    for frame in frames:
        payload = frame.payload
        kind = payload.get("type", frame.kind)
        identity = payload.get("request_id")
        if kind == "result":
            questions.clear()
            permissions.clear()
        if not isinstance(identity, str) or not identity:
            continue
        if kind == "ask_user_question":
            questions[identity] = payload
        elif kind == "control_request":
            permissions[identity] = payload
        elif kind == "ask_user_resolved":
            questions.pop(identity, None)
        elif kind == "permission_resolved":
            permissions.pop(identity, None)
    return questions, permissions


def unrestored_control(frame: dict) -> dict:
    """Native process restarts invalidate RPC/menu addresses, even if text survives."""
    return {
        **frame,
        "answerable": False,
        "recovery_required": True,
        "recovery_reason": "native_control_not_restored",
    }


def save_control_state(path: Path, questions: dict, permissions: dict) -> None:
    """Atomic replacement prevents a crash from leaving a partially written card."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump({"questions": questions, "permissions": permissions}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_control_state(path: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    if not path.exists():
        return {}, {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Invalid persisted control state")
    loaded = []
    for field in ("questions", "permissions"):
        rows = data.get(field, {})
        if not isinstance(rows, dict) or any(
            not isinstance(key, str) or not isinstance(frame, dict) for key, frame in rows.items()
        ):
            raise ValueError("Invalid persisted control state")
        loaded.append({key: unrestored_control(frame) for key, frame in rows.items()})
    return loaded[0], loaded[1]
