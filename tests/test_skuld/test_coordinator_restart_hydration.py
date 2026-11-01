"""A ticket coordinator restarts from its durable log.

The rows mirror the log a real coordinator wrote for NIU-1172 on 2026-11-01. Its
Ravn replies reach Skuld over the room, so they have no raw ``assistant`` frame:
each one is stored only as a ``conversation.turn`` seed. They are served here by
the real Forge log route, so the test fails if that route and hydration disagree
about which rows a restart gets to see.
"""

from unittest.mock import MagicMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from niuu.ports.cli.transport import TransportCapabilities
from skuld.broker import Broker, ConversationTurn
from skuld.config import SkuldSettings
from tests.test_adapters.test_rest_session_log import InMemoryLog, allow_log_access
from volundr.adapters.inbound.rest_session_log import create_session_log_router

_SID = "468b74c4-a05d-57c5-afd8-f27b1a6e2191"
_TS = "2026-11-01T15:25:19.540845+00:00"
_LOG = f"/api/v1/forge/sessions/{_SID}/log"
_RAVN = "flock-ticket-coordinator"
_RAVN_META = {"peer_id": _RAVN, "persona": "ticket-coordinator", "participant_type": "ravn"}
_REQUEST_ID = "6cd649e4-ad63-4f89-9a91-e89993b8feb5"

_BRIEF_ID = "cc4d1080-33f0-48fa-9b4d-87907ca1db4a"
_LAUNCH_ID = "5e8a40b5-f7f7-4af0-9a7f-4a29c25666a2"
_GATE_ID = "445194da-8ebe-4eba-8597-4beef136c251"
_REPLY_ID = "6f2a4445-fef2-4e26-8ff6-c06a681c7d21"
_BRIEF = "Ticket NIU-1172: write three opening lines. Start the workflow 'Ticket Work Flow'."
_LAUNCH = "---outcome---\nselected_next_action: await the workflow"
_GATE = "@ticket-coordinator Workflow gate opened: Review the result is waiting for a person."
_REPLY = "The result is awaiting your decision on the ticket's **Review** tab."


def _turn(turn_id, role, content, **fields):
    return {
        "id": turn_id,
        "role": role,
        "content": content,
        "parts": [],
        "created_at": _TS,
        "metadata": {},
        "participant_id": None,
        "participant_meta": None,
        "thread_id": None,
        "visibility": "public",
        **fields,
    }


def _coordinator_turns():
    room = {"participant_id": _RAVN, "participant_meta": _RAVN_META}
    return [
        _turn(_BRIEF_ID, "user", _BRIEF),
        _turn(_LAUNCH_ID, "assistant", _LAUNCH, **room),
        _turn(_GATE_ID, "user", _GATE, participant_id="", thread_id=""),
        _turn(_REPLY_ID, "assistant", _REPLY, **room),
    ]


def _handshake(seq):
    return [
        {
            "seq": seq,
            "kind": "system",
            "payload": {
                "type": "system",
                "content": f"Connected to session {_SID}",
                "_per_connect_handshake": True,
            },
        },
        {
            "seq": seq + 1,
            "kind": "capabilities",
            "payload": {"type": "capabilities", "steering_mode": "live", "history_protocol": 2},
        },
    ]


def _seed(seq, turn):
    return {
        "seq": seq,
        "kind": "conversation.turn",
        "payload": {"type": "conversation.turn", "turn": turn},
    }


def _slash_commands(seq):
    return {
        "seq": seq,
        "kind": "slash_commands",
        "payload": {
            "type": "slash_commands",
            "count": 1,
            "commands": [{"kind": "command", "name": "/effort", "source": "forge"}],
        },
    }


def _coordinator_log():
    brief, launch, gate, reply = _coordinator_turns()
    return [
        _seed(1, brief),
        {
            "seq": 2,
            "kind": "user",
            "payload": {
                "role": "user",
                "type": "user",
                "uuid": _BRIEF_ID,
                "message": {"role": "user", "content": _BRIEF},
            },
        },
        {
            "seq": 3,
            "kind": "user_confirmed",
            "payload": {"id": _BRIEF_ID, "type": "user_confirmed", "content": _BRIEF},
        },
        *_handshake(4),
        _slash_commands(6),
        {
            "seq": 7,
            "kind": "room_outcome",
            "payload": {
                "type": "room_outcome",
                "valid": True,
                "fields": {"event_type": "run.requested", "session_id": _SID},
            },
        },
        _seed(8, launch),
        *_handshake(9),
        _seed(11, gate),
        {
            "seq": 12,
            "kind": "user_confirmed",
            "request_id": _REQUEST_ID,
            "payload": {
                "id": _GATE_ID,
                "type": "user_confirmed",
                "source": "browser",
                "content": _GATE,
                "request_id": _REQUEST_ID,
            },
        },
        _seed(13, reply),
        *_handshake(14),
        _slash_commands(16),
    ]


def _coordinator_broker(tmp_path):
    broker = Broker(
        SkuldSettings(
            session={
                "id": _SID,
                "workspace_dir": str(tmp_path),
                "initial_prompt": _BRIEF,
            },
            volundr_api_url="http://forge.test",
            transport="subprocess",
            room={"enabled": True},
            workflow_trigger={"enabled": True, "event_type": "run.requested"},
        )
    )
    broker._transport = MagicMock()
    broker._transport.is_alive = True
    broker._transport.capabilities = TransportCapabilities()
    return broker


def _cache_turns(broker):
    broker._conversation_turns = [ConversationTurn(**turn) for turn in _coordinator_turns()]
    broker._save_conversation_history()


async def _restart(tmp_path, entries):
    """Boot a fresh broker over the workspace, hydrating from the real log route."""
    app = FastAPI()
    repo = InMemoryLog()
    app.include_router(create_session_log_router(repo, session_service=allow_log_access(app)))
    async with httpx.AsyncClient(
        base_url="http://forge.test", transport=httpx.ASGITransport(app=app)
    ) as client:
        stored = await client.post(_LOG, json={"entries": entries})
        assert stored.status_code == 201
        assert await repo.latest_seq(UUID(_SID)) == entries[-1]["seq"]
        broker = _coordinator_broker(tmp_path)
        broker._http_client = client
        broker._load_conversation_history()
        await broker._hydrate_conversation_history()
    return broker


async def test_coordinator_restarts_from_its_cache_and_log(tmp_path):
    _cache_turns(_coordinator_broker(tmp_path))

    broker = await _restart(tmp_path, _coordinator_log())

    assert [turn.id for turn in broker._conversation_turns] == [
        _BRIEF_ID,
        _LAUNCH_ID,
        _GATE_ID,
        _REPLY_ID,
    ]
    assert broker._conversation_turns[3].content == _REPLY
    assert broker._conversation_turns[3].participant_id == _RAVN
    assert broker._conversation_turns[3].participant_meta == _RAVN_META


async def test_coordinator_recovers_ravn_replies_with_no_cache(tmp_path):
    broker = await _restart(tmp_path, _coordinator_log())

    assert [(turn.role, turn.content) for turn in broker._conversation_turns] == [
        ("user", _BRIEF),
        ("assistant", _LAUNCH),
        ("user", _GATE),
        ("assistant", _REPLY),
    ]
    assert broker._conversation_turns[1].participant_id == _RAVN


async def test_coordinator_still_refuses_a_cached_turn_the_log_lacks(tmp_path):
    _cache_turns(_coordinator_broker(tmp_path))
    log = [entry for entry in _coordinator_log() if entry["seq"] != 8]

    with pytest.raises(RuntimeError, match="complete review evidence") as refused:
        await _restart(tmp_path, log)

    assert "unmatched turns within the durable span" in str(refused.value.__cause__)
