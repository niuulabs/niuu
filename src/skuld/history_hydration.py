"""Bounded durable-history reads and conservative local-cache reconciliation."""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import httpx

from niuu.domain.text_projection import REPAIR_KEY, projection_digest, repair_legacy_turn
from niuu.domain.transcript_reducer import reduce_frames


@dataclass(frozen=True)
class _HistoryFrame:
    session_id: str
    seq: int
    kind: str
    payload: dict
    ts: datetime
    request_id: str | None = None


async def _read_json(client: httpx.AsyncClient, path: str, *, remaining: int, params=None):
    chunks = []
    size = 0
    async with client.stream("GET", path, params=params) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > remaining:
                raise ValueError("Durable history exceeds the configured byte budget")
            chunks.append(chunk)
    return json.loads(b"".join(chunks)), size


async def fetch_durable_history(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    path: str,
    timeout_seconds: float,
    page_size: int,
    max_frames: int,
    max_bytes: int,
    on_frames: Callable[[list[_HistoryFrame]], None] | None = None,
) -> list[dict]:
    """Fold a frozen public-log horizon; filtered pages never imply end-of-log.

    Raw rows are paged before the server removes synthetic/per-connect frames.
    An empty page can safely advance by the requested raw page size: its rows
    were all excluded and strictly increasing integer seqs bound their end.
    A short visible page must likewise be followed until the frozen head.
    """
    async with asyncio.timeout(timeout_seconds):
        head_data, consumed = await _read_json(client, path + "/head", remaining=max_bytes)
        head = head_data.get("latest_seq") if isinstance(head_data, dict) else None
        if type(head) is not int or head < 0:
            raise ValueError("Durable history returned an invalid head")
        frames = []
        cursor = 0
        while cursor < head:
            page, size = await _read_json(
                client,
                path,
                remaining=max_bytes - consumed,
                params={"after": cursor, "limit": page_size, "show_internal": "true"},
            )
            consumed += size
            if not isinstance(page, list):
                raise ValueError("Durable history page must be an array")
            if not page:
                cursor += page_size
                continue
            previous = cursor
            for row in page:
                if not isinstance(row, dict):
                    raise ValueError("Durable history frame must be an object")
                seq = row.get("seq")
                if type(seq) is not int or seq <= previous:
                    raise ValueError("Durable history cursor must increase monotonically")
                previous = seq
                if str(row.get("session_id")) != session_id:
                    raise ValueError("Durable history contains a foreign session frame")
                if seq > head:
                    continue
                kind, payload = row.get("kind"), row.get("payload")
                if not isinstance(kind, str) or not isinstance(payload, dict):
                    raise ValueError("Durable history frame has an invalid kind or payload")
                if kind in {"log_gap", "log_conflict"}:
                    raise ValueError("Durable history contains a capture gap or sequence conflict")
                frames.append(
                    _HistoryFrame(
                        session_id=session_id,
                        seq=seq,
                        kind=kind,
                        payload=payload,
                        ts=datetime.fromisoformat(row["ts"]),
                        request_id=row.get("request_id"),
                    )
                )
                if len(frames) > max_frames:
                    raise ValueError("Durable history exceeds the configured frame budget")
            cursor = previous
        if on_frames is not None:
            on_frames(frames)
        return reduce_frames(frames).turns


def merge_history_turns(durable: list[dict], local: list[dict]) -> list[dict]:
    """Prefer the durable prefix while preserving richer matches and a local tail.

    Identity anchors must agree in order. An unmatched local turn inside the
    durable span, competing tails, or no overlap is ambiguous: preserve the
    existing cache instead of guessing which conversation a turn belongs to.
    """
    if not local:
        return durable
    if not durable:
        return local
    durable_ids = [turn["id"] for turn in durable]
    local_ids = [turn["id"] for turn in local]
    if len(set(durable_ids)) != len(durable_ids) or len(set(local_ids)) != len(local_ids):
        raise ValueError("History contains duplicate turn identities")
    durable_positions = {turn_id: index for index, turn_id in enumerate(durable_ids)}
    matches = [index for index, turn_id in enumerate(local_ids) if turn_id in durable_positions]
    if not matches:
        raise ValueError("Local and durable histories have no unambiguous identity overlap")
    last_match = matches[-1]
    if matches != list(range(last_match + 1)):
        raise ValueError("Local history contains unmatched turns within the durable span")
    shared_positions = [durable_positions[local_ids[index]] for index in matches]
    if shared_positions != sorted(shared_positions):
        raise ValueError("Local and durable history order disagrees")
    tail = local[last_match + 1 :]
    if tail and shared_positions[-1] != len(durable) - 1:
        raise ValueError("Local and durable histories contain competing tails")
    local_by_id = {turn["id"]: turn for turn in local[: last_match + 1]}
    merged = [
        _merge_matching_turn(turn, local_by_id[turn["id"]]) if turn["id"] in local_by_id else turn
        for turn in durable
    ]
    return [*merged, *tail]


def _merge_matching_turn(durable: dict, local: dict) -> dict:
    if durable.get("role") != local.get("role"):
        raise ValueError("Matching history identities have different roles")
    replacement = repair_legacy_turn(local, durable)
    if replacement is not None:
        return replacement
    # Public raw replay has captured boundaries but may lack native IDs/phases
    # added by a verified repair. Preserve the enriched local projection only
    # when exact ordered prose and all nontext content agree with durable data.
    metadata = local.get("metadata")
    proof = metadata.get(REPAIR_KEY) if isinstance(metadata, dict) else None
    if isinstance(proof, dict) and proof.get("digest") == projection_digest(local):

        def comparable(turn):
            return [
                {"type": "text", "text": part.get("text")} if part.get("type") == "text" else part
                for part in turn.get("parts", [])
            ]

        if comparable(local) == comparable(durable):
            return local
    content = _richer_prefix(durable.get("content", ""), local.get("content", ""))
    parts = _richer_prefix(durable.get("parts", []), local.get("parts", []))
    return {
        **durable,
        **local,
        "content": content,
        "parts": parts,
        "metadata": {**local.get("metadata", {}), **durable.get("metadata", {})},
    }


def _richer_prefix(durable, local):
    if durable == local[: len(durable)]:
        return local
    if local == durable[: len(local)]:
        return durable
    raise ValueError("Matching history identities contain conflicting content")
