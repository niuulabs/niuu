"""Verified repair of old aggregate-only prose using retained message boundaries.

The event ledger is immutable. A repair changes only a derived turn's text
projection after proving exact prose bytes and every nontext part agree. IDs,
timestamps, tools, results, accounting, and human turns remain authoritative.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any

from niuu.domain.conversation_timeline import timeline
from niuu.domain.transcript_reducer import Frame, reduce_frames

REPAIR_KIND = "conversation.projection"
REPAIR_KEY = "text_projection_repair"
SCHEMA = 1


def _metadata(turn: dict) -> dict:
    value = turn.get("metadata")
    return value if isinstance(value, dict) else {}


def projection_digest(turn: dict) -> str:
    """Bind only the projection; unrelated delivery/accounting metadata may evolve."""
    data = {key: turn.get(key) for key in ("id", "role", "content", "parts")}
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def projection_revision(turns: list[dict]) -> str:
    """Stable across ordinary appends; change when an existing prefix is repaired."""
    repairs = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        marker = _metadata(turn).get(REPAIR_KEY)
        if isinstance(marker, dict) and marker.get("schema") == SCHEMA:
            repairs.append((turn.get("id"), marker.get("digest")))
    suffix = "0"
    if repairs:
        suffix = hashlib.sha256(
            json.dumps(repairs, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
    timed = any(
        timeline(turn.get("metadata")) is not None
        or any(timeline(part) is not None for part in turn.get("parts", []))
        for turn in turns
        if isinstance(turn, dict)
    )
    return f"text-items-{SCHEMA}:{suffix}" + (";timeline-1" if timed else "")


def _legacy(turn: dict) -> bool:
    parts = turn.get("parts", [])
    return (
        turn.get("role") == "assistant"
        and isinstance(turn.get("id"), str)
        and bool(turn["id"])
        and isinstance(turn.get("content"), str)
        and bool(turn["content"])
        and isinstance(parts, list)
        and all(isinstance(part, dict) and part.get("type") != "text" for part in parts)
    )


def repair_legacy_turn(
    original: dict,
    candidate: dict,
    *,
    source_head: int | None = None,
    source: str = "normalized_log",
    native_prefix_sha256: str | None = None,
) -> dict | None:
    """Return a verified replacement, or None when boundaries cannot be proved.

    Matching aggregate text alone is insufficient: the turn identity and exact
    ordered nontext dictionaries must also match. Every text item must have a
    captured boundary and explicit completion. Missing evidence stays legacy.
    """
    if not _legacy(original) or candidate.get("id") != original.get("id"):
        return None
    if candidate.get("role") != "assistant":
        return None
    parts = candidate.get("parts")
    if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
        return None
    texts = [part for part in parts if part.get("type") == "text"]
    if not texts or any(
        not isinstance(part.get("text"), str)
        or not isinstance(part.get("id"), str)
        or not part["id"]
        or part.get("complete") is not True
        or part.get("phase") not in (None, "commentary", "final_answer")
        or any(
            key in part and (not isinstance(part[key], str) or not part[key])
            for key in ("turn_id", "thread_id")
        )
        for part in texts
    ):
        return None
    identities = [(part.get("thread_id"), part.get("turn_id"), part["id"]) for part in texts]
    if len(set(identities)) != len(identities):
        return None
    if "".join(part["text"] for part in texts) != original["content"]:
        return None
    if [part for part in parts if part.get("type") != "text"] != original.get("parts", []):
        return None
    replacement = {
        **original,
        "content": "\n\n".join(part["text"] for part in texts if part["text"]),
        "parts": [dict(part) for part in parts],
    }
    proof: dict[str, Any] = {
        "schema": SCHEMA,
        "source": source,
        "before_digest": projection_digest(original),
        "digest": projection_digest(replacement),
    }
    if source_head is not None:
        proof["source_head"] = source_head
    if native_prefix_sha256 is not None:
        proof["native_prefix_sha256"] = native_prefix_sha256
    replacement["metadata"] = {**_metadata(original), REPAIR_KEY: proof}
    return replacement


def repair_legacy_turns(turns: list[dict], frames: list[Frame]) -> list[dict]:
    """Repair only identified native spans, ignoring queued human interruptions.

    A human message may be queued while the prior native turn still runs.
    Grouping by both native thread and turn avoids treating that queue arrival
    as an assistant boundary. Untagged history has insufficient evidence here.
    """
    eligible = {turn["id"] for turn in turns if _legacy(turn)}
    if not eligible or any(frame.kind in ("log_gap", "log_conflict") for frame in frames):
        return turns
    groups: dict[tuple[str, str], list[Frame]] = defaultdict(list)
    for frame in frames:
        payload = frame.payload if isinstance(frame.payload, dict) else {}
        thread, turn = payload.get("thread_id"), payload.get("turn_id")
        if not isinstance(thread, str) or not thread or not isinstance(turn, str) or not turn:
            continue
        if frame.kind in ("user", "user_confirmed"):
            message = payload.get("message")
            blocks = message.get("content") if isinstance(message, dict) else None
            if (
                not isinstance(blocks, list)
                or not blocks
                or any(
                    not isinstance(block, dict) or block.get("type") != "tool_result"
                    for block in blocks
                )
            ):
                continue
        groups[(thread, turn)].append(frame)
    candidates = {}
    for group in groups.values():
        if any(frame.kind in ("log_gap", "log_conflict") for frame in group):
            continue
        # A terminal boundary is required; interrupted tails are never repaired.
        if not any(frame.kind == "result" for frame in group):
            continue
        head = max(frame.seq for frame in group)
        for candidate in reduce_frames(group).turns:
            if candidate["id"] in eligible:
                candidates[candidate["id"]] = (candidate, head)
    repaired = []
    for original in turns:
        match = candidates.get(original["id"])
        replacement = (
            repair_legacy_turn(original, match[0], source_head=match[1]) if match else None
        )
        repaired.append(replacement or original)
    return repaired


def repair_marker(replacements: list[dict]) -> dict:
    """Create an append-only derived marker for already verified replacements."""
    repairs = []
    for turn in replacements:
        proof = _metadata(turn).get(REPAIR_KEY)
        if (
            not isinstance(proof, dict)
            or proof.get("schema") != SCHEMA
            or proof.get("digest") != projection_digest(turn)
        ):
            raise ValueError("Repair lacks a valid projection proof")
        repairs.append({"turn_id": turn["id"], "parts": turn["parts"], "proof": proof})
    return {"type": REPAIR_KIND, "schema": SCHEMA, "repairs": repairs}


def apply_repair_markers(turns: list[dict], frames: list[Frame]) -> list[dict]:
    """Consume derived markers only when their original projection still matches."""
    result = list(turns)
    positions = {turn["id"]: index for index, turn in enumerate(result)}
    for frame in sorted(frames, key=lambda row: row.seq):
        payload = frame.payload if isinstance(frame.payload, dict) else {}
        if frame.kind != REPAIR_KIND or payload.get("schema") != SCHEMA:
            continue
        repairs = payload.get("repairs")
        if not isinstance(repairs, list):
            continue
        for repair in repairs:
            if (
                not isinstance(repair, dict)
                or not isinstance(repair.get("turn_id"), str)
                or repair["turn_id"] not in positions
            ):
                continue
            index = positions[repair["turn_id"]]
            original = result[index]
            proof = repair.get("proof")
            if (
                not isinstance(proof, dict)
                or proof.get("schema") != SCHEMA
                or proof.get("before_digest") != projection_digest(original)
            ):
                continue
            head = proof.get("source_head")
            if type(head) is not int or not 0 <= head < frame.seq:
                continue
            if proof.get("source") not in ("normalized_log", "native_log_verified"):
                continue
            candidate = {**original, "parts": repair.get("parts")}
            replacement = repair_legacy_turn(
                original,
                candidate,
                source_head=head,
                source=proof["source"],
                native_prefix_sha256=proof.get("native_prefix_sha256"),
            )
            if replacement and projection_digest(replacement) == proof.get("digest"):
                result[index] = replacement
    return result
