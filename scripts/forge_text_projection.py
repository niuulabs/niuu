"""Preview a verified text-history repair from immutable exported evidence.

python -m scripts.forge_text_projection --help

This command performs no network/database writes and never modifies source
history. It emits a corrected cache and append-only ledger marker for a guarded
idle-session maintenance operation. Native private analysis is never exported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from niuu.domain.text_projection import (
    projection_digest,
    projection_revision,
    repair_legacy_turn,
    repair_legacy_turns,
    repair_marker,
)
from volundr.adapters.outbound.external_sessions.codex_transcript import parse_codex_transcript
from volundr.adapters.outbound.external_sessions.transcript_common import (
    DEFAULT_MAX_TRANSCRIPT_BYTES,
    read_native_jsonl,
)

MAX_EVIDENCE_BYTES = 128 * 1024 * 1024


def read_evidence(path: Path) -> tuple[object, str]:
    with path.open("rb") as handle:
        data = handle.read(MAX_EVIDENCE_BYTES + 1)
    if len(data) > MAX_EVIDENCE_BYTES:
        raise ValueError("Evidence exceeds the bounded input budget")
    return json.loads(data), hashlib.sha256(data).hexdigest()


def preview(rows: list[dict], history: dict, native_frames: list, native_sha256: str) -> dict:
    if not isinstance(rows, list) or not rows:
        raise ValueError("A complete nonempty raw ledger export is required")
    session_ids = {str(row.get("session_id")) for row in rows}
    if len(session_ids) != 1:
        raise ValueError("Ledger contains multiple session identities")
    if [row.get("seq") for row in rows] != list(range(1, len(rows) + 1)):
        raise ValueError("Ledger must be a complete contiguous frozen prefix")
    # Exporters may spell a timestamptz with a space or a T. Restore its typed
    # database value before folding; never alter literal timestamps in payloads.
    frames = [
        SimpleNamespace(
            **{
                **row,
                "request_id": row.get("request_id"),
                "ts": datetime.fromisoformat(row["ts"])
                if isinstance(row.get("ts"), str)
                else row.get("ts"),
            }
        )
        for row in rows
    ]
    turns = history.get("turns")
    if not isinstance(turns, list):
        raise ValueError("History must contain an array of turns")
    seeds = {
        row["payload"]["turn"]["id"]: row["payload"]["turn"]
        for row in rows
        if row["kind"] == "conversation.turn"
        and isinstance(row.get("payload", {}).get("turn"), dict)
    }
    native: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for frame in native_frames:
        for block in frame.payload.get("message", {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                native[(block.get("thread_id"), block.get("turn_id"))].append(block)
    repaired = repair_legacy_turns(turns, frames)
    replacements = []
    checks = []
    for index, (original, candidate) in enumerate(zip(turns, repaired, strict=True)):
        if candidate is original:
            continue
        seed = seeds.get(original["id"])
        if seed is None or projection_digest(seed) != projection_digest(original):
            raise ValueError("Cache projection differs from its authoritative ledger seed")
        texts = [part for part in candidate["parts"] if part["type"] == "text"]
        contexts = {(part.get("thread_id"), part.get("turn_id")) for part in texts}
        native_match = False
        if len(contexts) == 1:
            messages = native.get(next(iter(contexts)), [])
            if [part["text"] for part in texts] == [part["text"] for part in messages]:
                # Match by turn + occurrence order, never global text deduplication.
                parts = [dict(part) for part in candidate["parts"]]
                messages_iter = iter(messages)
                for part in parts:
                    if part["type"] != "text":
                        continue
                    message = next(messages_iter)
                    for key in ("id", "id_source", "phase", "thread_id", "turn_id"):
                        part.pop(key, None)
                        if key in message:
                            part[key] = message[key]
                enriched = repair_legacy_turn(
                    original,
                    {**candidate, "parts": parts},
                    source_head=rows[-1]["seq"],
                    source="native_log_verified",
                    native_prefix_sha256=native_sha256,
                )
                if enriched is not None:
                    candidate = enriched
                    native_match = True
        repaired[index] = candidate
        replacements.append(candidate)
        checks.append(
            {
                "turn_id": original["id"],
                "text_items": len(texts),
                "native_identity_and_phase_verified": native_match,
                "exact_original_text_bytes": True,
                "exact_nontext_parts": True,
                "before_digest": projection_digest(original),
                "after_digest": projection_digest(candidate),
            }
        )
    return {
        "history": {
            **history,
            "turns": repaired,
            "projection_revision": projection_revision(repaired),
        },
        "marker": repair_marker(replacements),
        "summary": {
            "session_id": next(iter(session_ids)),
            "source_head": rows[-1]["seq"],
            "turns": len(turns),
            "repaired_turns": len(replacements),
            "checks": checks,
            "projection_revision": projection_revision(repaired),
            "raw_ledger_unchanged": True,
            "native_private_analysis_exported": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--native-rollout", type=Path, required=True)
    parser.add_argument("--native-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows, ledger_sha = read_evidence(args.ledger)
    history, history_sha = read_evidence(args.history)
    source = read_native_jsonl(
        args.native_rollout, args.native_rollout.parent, DEFAULT_MAX_TRANSCRIPT_BYTES
    )
    native = parse_codex_transcript(source, args.native_id, UUID(rows[0]["session_id"]))
    result = preview(rows, history, native, source.sha256)
    result["summary"]["inputs"] = {
        "ledger_sha256": ledger_sha,
        "history_sha256": history_sha,
        "native_prefix_sha256": source.sha256,
    }
    os.umask(0o077)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, value in result.items():
        (args.output / f"{name}.json").write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(json.dumps(result["summary"], sort_keys=True))


if __name__ == "__main__":
    main()
