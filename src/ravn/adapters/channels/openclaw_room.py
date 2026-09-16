"""Rooms as OpenClaw sessions — the bridge that puts a collaboration room on a phone.

A ``ravn room`` is a Skuld broker bound to loopback, with several Ravn members and one or more
humans in it.  Nothing about it is reachable from a device: the broker binds ``127.0.0.1`` and
speaks its own participation API, not the OpenClaw protocol.

This module makes a room look like one more OpenClaw session, so it arrives in the same channel
list, the same transcript and the same composer as everything else — with one addition the 1:1
protocol has no need for: **who said it**.  Every room turn carries its speaker, and the session
carries the roster, so a client can attribute a message rather than assuming the channel's agent.

Discovery is the rooms directory itself.  ``ravn room create`` already writes ``room.yaml`` with
the broker's host and port, so a room that exists on this host is a room that shows up on the
phone — no second registration step, and nothing to keep in sync.

What the broker gives us, and what it costs:

- ``GET  /api/room/participants``   → the roster
- ``GET  /api/conversation/history``→ turns, each with ``participant_id`` and ``participant_meta``
- ``POST /api/room/message``        → post as a participant

There is no server-push here, so new turns are discovered by polling the history endpoint while at
least one client is subscribed.  That is a deliberate v1 choice: the alternative is a second
long-lived socket per room speaking a protocol the broker does not yet stabilise, and polling a
loopback endpoint every couple of seconds is cheap and cannot wedge.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)

#: Session-key grammar for a room. The room name sits in the agent slot: every existing client
#: rail derives identity from ``agent:<id>:`` — the channel list, the slug router, the title and
#: the history fetch — and a room owns its channel exactly the way an agent owns one. The roster
#: carries the truth about who is inside.
KEY_PREFIX = "agent:"
KEY_SUFFIX = ":main"

#: How often the poller re-reads a subscribed room's history.
POLL_INTERVAL_S = 2.0

#: Turns fetched per poll. A room is a conversation between people, not a firehose.
HISTORY_LIMIT = 50

_TIMEOUT_S = 10.0


def room_session_key(name: str) -> str:
    return f"{KEY_PREFIX}{name}{KEY_SUFFIX}"


def room_name_from_key(key: str) -> str | None:
    """Inverse of :func:`room_session_key`; None when *key* is not a room key shape."""
    if not isinstance(key, str) or not key.startswith(KEY_PREFIX) or not key.endswith(KEY_SUFFIX):
        return None
    name = key[len(KEY_PREFIX) : -len(KEY_SUFFIX)]
    return name or None


@dataclass(frozen=True)
class RoomRef:
    """A room this host knows about, as ``room.yaml`` describes it."""

    name: str
    host: str
    port: int

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def session_key(self) -> str:
        return room_session_key(self.name)


def discover_rooms(rooms_dir: Path) -> list[RoomRef]:
    """Every room defined under *rooms_dir*, whether or not its broker is running.

    A stopped room still belongs in the list: it is a real conversation with real history, and
    hiding it would make a restart look like data loss. Liveness is reported per room instead.
    """
    if not rooms_dir.is_dir():
        return []
    out: list[RoomRef] = []
    for entry in sorted(rooms_dir.iterdir()):
        definition = entry / "room.yaml"
        if not definition.is_file():
            continue
        try:
            data = yaml.safe_load(definition.read_text(encoding="utf-8")) or {}
            out.append(
                RoomRef(
                    name=str(data["name"]),
                    host=str(data.get("host", "127.0.0.1")),
                    port=int(data["port"]),
                )
            )
        except (OSError, KeyError, ValueError, TypeError) as exc:
            # One unreadable room must not hide the others.
            logger.warning("room %s: unreadable definition (%s)", entry.name, exc)
    return out


def participant_from_turn(turn: dict[str, Any]) -> dict[str, Any] | None:
    """The wire ``sender`` for a room turn, from what the broker already stamps on it.

    ``participant_meta`` carries peer id, display name, persona and the room's own colour slot.
    The colour is passed through for fidelity, but a client should not tint from it: the room
    assigns slots in join order, so they move when a member restarts.
    """
    peer_id = turn.get("participant_id")
    if not isinstance(peer_id, str) or not peer_id:
        return None
    meta = turn.get("participant_meta") or {}
    sender: dict[str, Any] = {"id": peer_id}
    if meta.get("display_name"):
        sender["displayName"] = str(meta["display_name"])
    if meta.get("participant_type"):
        sender["kind"] = "human" if meta["participant_type"] == "human" else "agent"
    if meta.get("color"):
        sender["color"] = str(meta["color"])
    if meta.get("persona"):
        sender["persona"] = str(meta["persona"])
    return sender


def turn_to_message(turn: dict[str, Any]) -> dict[str, Any] | None:
    """One room turn in the OpenClaw ``chat.history`` message shape, or None if unusable."""
    body = turn.get("content")
    if not isinstance(body, str) or not body.strip():
        return None
    role = "user" if turn.get("role") == "user" else "assistant"
    message: dict[str, Any] = {
        "id": str(turn.get("id") or ""),
        "role": role,
        "blocks": [{"type": "text", "text": body}],
        "content": body,
    }
    sender = participant_from_turn(turn)
    if sender is not None:
        message["sender"] = sender
    if turn.get("created_at"):
        message["createdAt"] = str(turn["created_at"])
    return message


class RoomClient:
    """Talks to one room's broker. Every call reports failure rather than inventing an answer."""

    def __init__(self, ref: RoomRef, *, client: httpx.AsyncClient | None = None) -> None:
        self._ref = ref
        self._client = client
        self._owns_client = client is None

    @property
    def ref(self) -> RoomRef:
        return self._ref

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_TIMEOUT_S)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def participants(self) -> list[dict[str, Any]]:
        """The roster in the wire shape, or empty when the broker is down."""
        try:
            http = await self._http()
            response = await http.get(f"{self._ref.base_url}/api/room/participants")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.debug("room %s: participants unavailable (%s)", self._ref.name, exc)
            return []
        payload = response.json()
        raw = payload.get("participants") if isinstance(payload, dict) else payload
        if not isinstance(raw, list):
            return []
        out: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            peer_id = entry.get("peer_id") or entry.get("id")
            if not peer_id:
                continue
            participant: dict[str, Any] = {"id": str(peer_id)}
            if entry.get("display_name"):
                participant["displayName"] = str(entry["display_name"])
            kind = entry.get("participant_type")
            if kind:
                participant["kind"] = "human" if kind == "human" else "agent"
            if entry.get("color"):
                participant["color"] = str(entry["color"])
            if entry.get("persona"):
                participant["persona"] = str(entry["persona"])
            out.append(participant)
        return out

    async def history(self, *, limit: int = HISTORY_LIMIT) -> list[dict[str, Any]]:
        """Turns oldest-first, in the OpenClaw message shape.

        Oldest-first matters: the client renders the order verbatim, so a reversed array reads
        the conversation backwards.
        """
        try:
            http = await self._http()
            response = await http.get(
                f"{self._ref.base_url}/api/conversation/history", params={"limit": limit}
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.debug("room %s: history unavailable (%s)", self._ref.name, exc)
            return []
        turns = _turns_of(response.json())
        return [m for m in (turn_to_message(t) for t in turns) if m is not None]

    async def post(self, text: str, *, participant_id: str) -> bool:
        """Post into the room as *participant_id*, addressed the way the room expects.

        Mirrors ``ravn room post`` rather than the lower-level ``room message``: an ``@handle``
        resolves to a directed delivery, and an unaddressed message falls to whoever spoke last.
        Without that, a message typed on a phone would land in the transcript and invoke nobody —
        visible, and answered by no one, which reads as the room being broken.
        """
        roster = await self.participants()
        peer_ids = {str(p["id"]) for p in roster}
        recipients = [peer for peer in _resolve_mentions(text, peer_ids) if peer != participant_id]
        if not recipients:
            last = await self._last_speaker(exclude=participant_id)
            recipients = [last] if last else []

        try:
            http = await self._http()
            if not recipients:
                # Addressed to nobody: commentary. It belongs in the transcript, but must not be
                # pushed at a transport as if someone had been asked something.
                response = await http.post(
                    f"{self._ref.base_url}/api/room/message",
                    json={
                        "participant_id": participant_id,
                        "content": text,
                        "deliver_to_transport": False,
                    },
                )
                response.raise_for_status()
                return True
            for recipient in recipients:
                response = await http.post(
                    f"{self._ref.base_url}/api/room/direct",
                    json={
                        "target_peer_id": recipient,
                        "content": text,
                        "participant_id": participant_id,
                        "metadata": {"room": self._ref.name, "recipients": recipients},
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("room %s: post failed (%s)", self._ref.name, exc)
            return False
        return True

    async def _last_speaker(self, *, exclude: str) -> str | None:
        """Who spoke most recently, so an unaddressed message lands somewhere sensible."""
        try:
            http = await self._http()
            response = await http.get(
                f"{self._ref.base_url}/api/conversation/history", params={"limit": HISTORY_LIMIT}
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        for turn in reversed(_turns_of(response.json())):
            speaker = turn.get("participant_id")
            if isinstance(speaker, str) and speaker and speaker != exclude:
                return speaker
        return None

    async def join(self, *, participant_id: str, role: str = "owner") -> bool:
        """Seat a human in the room, so a phone can speak without a shell.

        Idempotent at the broker; a rejoin refreshes presence rather than duplicating a seat.
        """
        try:
            http = await self._http()
            response = await http.post(
                f"{self._ref.base_url}/api/room/join",
                json={
                    "participant_id": participant_id,
                    "display_name": participant_id,
                    "environment_id": self._ref.name,
                    "role": role,
                    "room_id": "",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("room %s: join failed (%s)", self._ref.name, exc)
            return False
        return True

    async def is_live(self) -> bool:
        try:
            http = await self._http()
            response = await http.get(f"{self._ref.base_url}/api/room/participants")
            return response.status_code < 400
        except httpx.HTTPError:
            return False


#: Same grammar the room CLI parses, so an address typed on a phone resolves the way the same
#: address typed in a terminal does.
_MENTION_RE = re.compile(r"@([A-Za-z0-9][A-Za-z0-9_:-]*)")
_FENCED_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def _resolve_mentions(body: str, peer_ids: set[str]) -> list[str]:
    """Peers addressed in *body*, in order, ignoring code spans.

    A human answers to their bare name too, so ``@damien`` reaches ``human:damien``. An unknown
    handle is simply not a recipient — it stays plain text rather than becoming a guess.
    """
    masked = _FENCED_RE.sub(lambda m: " " * len(m.group(0)), body)
    masked = _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), masked)

    lookup: dict[str, str] = {}
    for peer_id in peer_ids:
        lookup[peer_id.lower()] = peer_id
        if ":" in peer_id:
            lookup.setdefault(peer_id.split(":", 1)[1].lower(), peer_id)

    out: list[str] = []
    for match in _MENTION_RE.finditer(masked):
        peer = lookup.get(match.group(1).lower())
        if peer is not None and peer not in out:
            out.append(peer)
    return out


def _turns_of(payload: Any) -> list[dict[str, Any]]:
    """Normalise the history endpoint's envelope, which varies by broker version."""
    if isinstance(payload, list):
        return [t for t in payload if isinstance(t, dict)]
    if isinstance(payload, dict):
        for key in ("turns", "messages", "history"):
            value = payload.get(key)
            if isinstance(value, list):
                return [t for t in value if isinstance(t, dict)]
    return []


def session_row(
    ref: RoomRef,
    *,
    participants: list[dict[str, Any]],
    last_message: str | None,
    updated_at_ms: int | None,
    live: bool,
) -> dict[str, Any]:
    """The ``sessions.list`` row for a room.

    ``agentId`` is the room name, matching the key: the client filters channel visibility by
    agent id, so a room needs a real one or it would not be listed at all.
    """
    row: dict[str, Any] = {
        "key": ref.session_key,
        "sessionId": ref.session_key,
        "kind": "room",
        "agentId": ref.name,
        "displayName": ref.name,
        "status": "idle" if live else "offline",
        "hidden": False,
        "participants": participants,
    }
    if last_message:
        row["lastMessagePreview"] = last_message
    if updated_at_ms is not None:
        row["updatedAt"] = updated_at_ms
    return row
