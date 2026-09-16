"""Observation-ordered presentation of same-turn input, without splitting execution.

Native items remain atomic: an item first observed before a steer keeps its slot
when more text/output arrives. Tool results enrich that call's slot. Only the
derived assistant *rows* are split; native turn/item IDs and stored content stay
intact. Untimed legacy rows are barriers, never positioned by guessed timestamps.
"""

from __future__ import annotations

import uuid
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from typing import Any

TIMELINE_KEY = "conversation_timeline"
TIMELINE_SCHEMA = 1
_NAMESPACE = uuid.UUID("fb39b881-a2dd-49b9-b4f6-79efee6fd4ec")


def observation(seq: int, observed_at: str) -> dict:
    return {"schema": TIMELINE_SCHEMA, "seq": seq, "observed_at": observed_at}


def timeline(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    stamp = value.get(TIMELINE_KEY)
    if not isinstance(stamp, dict) or stamp.get("schema") != TIMELINE_SCHEMA:
        return None
    if type(stamp.get("seq")) is not int or stamp["seq"] <= 0:
        return None
    if not isinstance(stamp.get("observed_at"), str) or not stamp["observed_at"]:
        return None
    try:
        if datetime.fromisoformat(stamp["observed_at"]).tzinfo is None:
            return None
    except ValueError:
        return None
    return stamp


def stamp_parts(parts: list[dict], frame: dict, *, start: int = 0) -> None:
    """Stamp first observation once, including empty text-start anchors."""
    stamp = timeline(frame)
    if stamp is None:
        return
    previous = next((timeline(p) for p in parts[:start] if timeline(p)), None)
    span_seq = previous.get("span_seq", previous["seq"]) if previous else stamp["seq"]
    for part in parts[start:]:
        if timeline(part) is None:
            part[TIMELINE_KEY] = {**stamp, "span_seq": span_seq}


def _user_stamp(turn: dict) -> dict | None:
    return timeline(turn.get("metadata")) if turn.get("role") == "user" else None


def _project_run(turns: list[dict], session_id: str) -> list[dict]:
    boundaries = sorted(stamp["seq"] for t in turns if (stamp := _user_stamp(t)))
    positioned: list[tuple[int, dict]] = []
    for turn in turns:
        user = _user_stamp(turn)
        if user is not None:
            positioned.append((user["seq"], turn))
            continue
        parts = turn["parts"]
        calls = {
            p["id"]: timeline(p)
            for p in parts
            if p.get("type") == "tool_use" and isinstance(p.get("id"), str)
        }
        groups: dict[int, list[dict]] = defaultdict(list)
        anchors: dict[int, dict] = {}
        for part in parts:
            stamp = timeline(part)
            if part.get("type") == "tool_result" and isinstance(part.get("tool_use_id"), str):
                stamp = calls.get(part["tool_use_id"]) or stamp
            slot = bisect_right(boundaries, stamp["seq"])
            groups[slot].append(part)
            if slot not in anchors or stamp["seq"] < anchors[slot]["seq"]:
                anchors[slot] = stamp
        last_slot = max(groups)
        for slot, group in sorted(groups.items()):
            anchor = anchors[slot]
            meta = dict(turn.get("metadata") or {})
            # Accounting belongs to the native terminal span, not every display slice.
            if slot != last_slot:
                for key in (
                    "usage",
                    "cost",
                    "stop_reason",
                    "status",
                    "is_error",
                    "error",
                    "messageType",
                ):
                    meta.pop(key, None)
            meta[TIMELINE_KEY] = {**anchor, "fragment": True}
            fragment = {
                **turn,
                "id": str(
                    uuid.uuid5(
                        _NAMESPACE,
                        f"{session_id}:{anchor.get('span_seq', anchor['seq'])}:"
                        f"after-input:{boundaries[slot - 1] if slot else 0}",
                    )
                ),
                "parts": group,
                "content": "\n\n".join(
                    p["text"] for p in group if p.get("type") == "text" and p.get("text")
                ),
                "created_at": anchor["observed_at"],
                "metadata": meta,
            }
            if slot != last_slot:
                fragment.pop("in_progress", None)
            positioned.append((anchor["seq"], fragment))
    return [turn for _, turn in sorted(positioned, key=lambda item: item[0])]


def project_timeline(turns: list[dict], session_id: str) -> list[dict]:
    """Stable row IDs across polling, completion and replay; never duplicate items.

    Do not cross an unknown/legacy row or split partially timed content. This is
    a derived read/cache projection; native content and the durable event ledger
    are never rewritten.
    """
    result: list[dict] = []
    run: list[dict] = []
    for turn in turns:
        parts = turn.get("parts")
        eligible = _user_stamp(turn) is not None or (
            turn.get("role") == "assistant"
            and isinstance(parts, list)
            and bool(parts)
            and all(isinstance(p, dict) and timeline(p) is not None for p in parts)
        )
        if eligible:
            run.append(turn)
            continue
        result.extend(_project_run(run, session_id))
        run = []
        result.append(turn)
    result.extend(_project_run(run, session_id))
    return result
