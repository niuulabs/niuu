"""Bounded durable-history reads and conservative local-cache reconciliation."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime

import httpx

from niuu.cap_telemetry import record_cap_hit
from niuu.domain.text_projection import REPAIR_KEY, projection_digest, repair_legacy_turn
from niuu.domain.transcript_rebuild import rebuild_turns


@dataclass(frozen=True)
class _HistoryFrame:
    session_id: str
    seq: int
    kind: str
    payload: dict
    ts: datetime
    request_id: str | None = None


#: The settings that bound a durable-history read, named when a read reaches them.
MAX_BYTES_KNOB = "history_hydration_max_bytes"
MAX_FRAMES_KNOB = "history_hydration_max_frames"

#: Sentinels Forge writes where frames were lost or a sequence was reused.
_GAP_KINDS = frozenset({"log_gap", "log_conflict"})


class HistoryBudgetExceededError(ValueError):
    """A durable-history read reached the configured byte or frame budget."""

    def __init__(self, knob: str, what: str) -> None:
        self.knob = knob
        message = f"{what} exceeds the configured {knob}; raise {knob} to read it"
        record_cap_hit(knob, message)
        super().__init__(message)


async def _read_json(
    client: httpx.AsyncClient, path: str, *, remaining: int, params=None, what: str
):
    chunks = []
    size = 0
    async with client.stream("GET", path, params=params) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > remaining:
                raise HistoryBudgetExceededError(MAX_BYTES_KNOB, what)
            chunks.append(chunk)
    return json.loads(b"".join(chunks)), size


async def read_durable_head(client: httpx.AsyncClient, *, path: str, max_bytes: int) -> int:
    """The highest stored seq: the frozen horizon a restart reads up to."""
    head_data, _ = await _read_json(
        client, path + "/head", remaining=max_bytes, what="Durable history head"
    )
    head = head_data.get("latest_seq") if isinstance(head_data, dict) else None
    if type(head) is not int or head < 0:
        raise ValueError("Durable history returned an invalid head")
    return head


def _row_seq(row, *, session_id: str, previous: int) -> int:
    if not isinstance(row, dict):
        raise ValueError("Durable history frame must be an object")
    seq = row.get("seq")
    if type(seq) is not int or seq <= previous:
        raise ValueError("Durable history cursor must increase monotonically")
    if str(row.get("session_id")) != session_id:
        raise ValueError("Durable history contains a foreign session frame")
    return seq


def _history_frame(row: dict, *, session_id: str) -> _HistoryFrame:
    kind, payload = row.get("kind"), row.get("payload")
    if not isinstance(kind, str) or not isinstance(payload, dict):
        raise ValueError("Durable history frame has an invalid kind or payload")
    if kind in _GAP_KINDS:
        raise ValueError("Durable history contains a capture gap or sequence conflict")
    return _HistoryFrame(
        session_id=session_id,
        seq=row["seq"],
        kind=kind,
        payload=payload,
        ts=datetime.fromisoformat(row["ts"]),
        request_id=row.get("request_id"),
    )


async def fetch_durable_state_frames(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    path: str,
    kinds: frozenset[str],
    head: int,
    timeout_seconds: float,
    page_size: int,
    max_frames: int,
    max_bytes: int,
) -> list[_HistoryFrame]:
    """Every frame of *kinds* up to *head*, read with Forge's kind filter.

    The state a broker owns (its peer inbox, controls, workflow steps, follows, seats,
    voice calls) lives in a few frame kinds, while an interactive session's log is
    mostly terminal frames. Asking for only these kinds keeps a restart's state read
    the size of that state, not of the whole conversation. Gap and conflict sentinels
    are read too: a lost frame of one of these kinds must not look like none.
    """
    wanted = kinds | _GAP_KINDS
    params = [("show_internal", "true"), *(("kind", k) for k in sorted(wanted))]
    async with asyncio.timeout(timeout_seconds):
        frames: list[_HistoryFrame] = []
        consumed = 0
        cursor = 0
        while cursor < head:
            page, size = await _read_json(
                client,
                path,
                remaining=max_bytes - consumed,
                params=[*params, ("after", cursor), ("limit", page_size)],
                what="Durable session state",
            )
            consumed += size
            if not isinstance(page, list):
                raise ValueError("Durable history page must be an array")
            if not page:
                return frames
            for row in page:
                cursor = _row_seq(row, session_id=session_id, previous=cursor)
                if cursor > head:
                    return frames
                if row.get("kind") not in wanted:
                    raise ValueError(
                        f"Forge answered a kind-filtered log read with a {row.get('kind')!r} "
                        "frame; it predates the kind filter. Upgrade Forge to this broker's "
                        "release"
                    )
                frames.append(_history_frame(row, session_id=session_id))
                if len(frames) > max_frames:
                    raise HistoryBudgetExceededError(MAX_FRAMES_KNOB, "Durable session state")
        return frames


async def fetch_durable_history(
    client: httpx.AsyncClient,
    *,
    session_id: str,
    path: str,
    head: int,
    timeout_seconds: float,
    page_size: int,
    max_frames: int,
    max_bytes: int,
) -> list[dict]:
    """Fold a frozen log horizon; filtered pages never imply end-of-log.

    Reads the reducer seeds too and folds with Forge's own ``rebuild_turns``:
    a room participant's reply is stored only as a ``conversation.turn`` seed,
    so the public wire view alone loses every Ravn answer a session gave.

    Raw rows are paged before the server removes per-connect frames.
    An empty page can safely advance by the requested raw page size: its rows
    were all excluded and strictly increasing integer seqs bound their end.
    A short visible page must likewise be followed until the frozen head.
    """
    async with asyncio.timeout(timeout_seconds):
        frames = []
        consumed = 0
        cursor = 0
        while cursor < head:
            page, size = await _read_json(
                client,
                path,
                remaining=max_bytes - consumed,
                params={
                    "after": cursor,
                    "limit": page_size,
                    "show_internal": "true",
                    "include_seeds": "true",
                },
                what="Durable conversation history",
            )
            consumed += size
            if not isinstance(page, list):
                raise ValueError("Durable history page must be an array")
            if not page:
                cursor += page_size
                continue
            previous = cursor
            for row in page:
                previous = _row_seq(row, session_id=session_id, previous=previous)
                if previous > head:
                    continue
                frames.append(_history_frame(row, session_id=session_id))
                if len(frames) > max_frames:
                    raise HistoryBudgetExceededError(
                        MAX_FRAMES_KNOB, "Durable conversation history"
                    )
            cursor = previous
        return rebuild_turns(frames).turns


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
