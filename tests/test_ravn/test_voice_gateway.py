"""The voice channel end to end, in the shapes the shapes voice clients decode."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ravn.adapters.channels.voice.gateway import VoiceGateway
from ravn.adapters.channels.voice.prompt import VoicePromptComposer
from ravn.adapters.channels.voice.room import RoomCopyError
from ravn.adapters.channels.voice.store import VoiceCallStore
from ravn.adapters.channels.voice.tools import VoiceConversationTools, VoiceToolBridge
from ravn.adapters.permission.allow_deny import AllowAllPermission
from ravn.config import VoiceChannelConfig
from ravn.ports.realtime_voice import (
    RealtimeCredential,
    RealtimeCredentialError,
    RealtimeCredentialMinterPort,
)
from tests.test_ravn.test_voice_prompt_tools_room import MODELS, FakeTool, persona


class FakeMinter(RealtimeCredentialMinterPort):
    def __init__(self, *, fail: bool = False, bound: bool = False) -> None:
        self.fail = fail
        self.bound = bound
        self.requests: list[dict] = []

    async def mint(
        self, *, model: str, voice: str, instructions: str, reasoning: str = ""
    ) -> RealtimeCredential:
        self.requests.append(
            {"model": model, "voice": voice, "instructions": instructions, "reasoning": reasoning}
        )
        if self.fail:
            raise RealtimeCredentialError("xai realtime mint returned HTTP 401: bad key")
        return RealtimeCredential(
            token="secret", expires_at_ms=1_800_000_000_000, model=model, session_bound=self.bound
        )


class FakeRoom:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.copies: list[dict] = []
        self.closed = False

    async def copy(self, *, call_id: str, speaker: str, text: str) -> None:
        if self.fail:
            raise RoomCopyError("Room broker refused voice copy (HTTP 503)")
        self.copies.append({"call_id": call_id, "speaker": speaker, "text": text})

    async def aclose(self) -> None:
        self.closed = True


OWNER = "owner-1"
_OPEN: list[VoiceGateway] = []


@pytest.fixture(autouse=True)
def _close_gateways():
    yield
    while _OPEN:
        asyncio.run(_OPEN.pop().aclose())


def build(
    tmp_path: Path, *, minters=None, room=None, tools=None
) -> tuple[VoiceGateway, TestClient]:
    config = VoiceChannelConfig(
        enabled=True, history_default_limit=2, history_max_limit=3, owner_id=OWNER
    )
    store = VoiceCallStore(str(tmp_path / "calls.db"))
    bridge = VoiceToolBridge(
        tools=tools if tools is not None else [FakeTool("web_search", result="sunny")],
        permission=AllowAllPermission(),
        conversation=VoiceConversationTools(store, search_limit=5),
        agent_name="sidekick",
        timeout_seconds=5,
    )
    the_persona = persona()
    gateway = _gateway(config, the_persona, bridge, store, minters, room)
    _OPEN.append(gateway)
    return gateway, TestClient(gateway.app, headers={"x-auth-user-id": OWNER})


def _gateway(config, the_persona, bridge, store, minters, room) -> VoiceGateway:
    try:
        return _new_gateway(config, the_persona, bridge, store, minters, room)
    except ValueError:
        store.close()
        raise


def _new_gateway(config, the_persona, bridge, store, minters, room) -> VoiceGateway:
    return VoiceGateway(
        config,
        persona=the_persona,
        composer=VoicePromptComposer(
            the_persona, models=MODELS, time_zone="UTC", tool_names=bridge.names
        ),
        tools=bridge,
        store=store,
        minters={"xai": FakeMinter()} if minters is None else minters,
        room=room,
    )


def ask(ws, message_type: str, payload: dict | None = None, message_id: str = "m1") -> dict:
    ws.send_json({"type": message_type, "id": message_id, "payload": payload or {}})
    return ws.receive_json()


def test_handshake_frames(tmp_path: Path) -> None:
    _, client = build(tmp_path)
    with client.websocket_connect("/") as ws:
        assert ask(ws, "auth", {"token": "x"}) == {"type": "auth.ok", "id": "m1", "payload": {}}
        assert ask(ws, "ping", message_id="p") == {"type": "pong", "id": "p", "payload": {}}
        assert ask(ws, "persona.list")["payload"] == {
            "defaultPersona": "sidekick",
            "personas": ["sidekick"],
        }
        context = ask(ws, "persona.get", {"name": "sidekick", "connector": "sidekick-ios"})[
            "payload"
        ]["context"]
        assert context["name"] == "sidekick" and context["displayName"] == "Sidekick"
        assert context["voices"] == {"grok-voice": "ara", "openai-realtime": "marin"}
        assert context["voice"] == "marin"
        assert context["sources"] == ["persona:sidekick"] and context["errors"] == []
        assert context["contextPrompt"].startswith("You are Sidekick.")
        assert ask(ws, "gateway.credentials")["payload"]["gateways"] == []


def test_error_frames_match_the_reference_service(tmp_path: Path) -> None:
    _, client = build(tmp_path)
    with client.websocket_connect("/") as ws:
        assert ask(ws, "voice.ephemeral", {}, "fixture-voice-ephemeral-missing-provider") == {
            "type": "error",
            "id": "fixture-voice-ephemeral-missing-provider",
            "payload": {"error": "provider is required", "code": "INVALID_PARAMS"},
        }
        unknown = ask(ws, "tools.bogus", {}, "fixture-unknown-type")
        assert unknown["type"] == "error" and unknown["payload"]["code"] == "UNKNOWN_TYPE"
        ws.send_text("not json")
        assert ws.receive_json()["payload"]["code"] == "INVALID_MESSAGE"
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["payload"]["code"] == "INVALID_MESSAGE"
        assert ask(ws, "dictation.credential")["payload"]["code"] == "NOT_CONFIGURED"
        assert (
            ask(ws, "tools.group.toggle", {"group": "web", "enabled": False})["payload"]["code"]
            == "INVALID_PARAMS"
        )
        assert (
            ask(ws, "voice.ephemeral", {"provider": "gemini"})["payload"]["code"]
            == "UNSUPPORTED_PROVIDER"
        )


def test_mint_returns_a_credential_and_the_persona(tmp_path: Path) -> None:
    minter = FakeMinter()
    _, client = build(tmp_path, minters={"xai": minter})
    with client.websocket_connect("/") as ws:
        result = ask(
            ws, "voice.ephemeral", {"provider": "xai", "persona": "sidekick", "reasoning": "high"}
        )

    assert result["type"] == "voice.ephemeral.result"
    payload = result["payload"]
    assert payload["success"] is True
    assert payload["provider"] == "xai"
    assert payload["token"] == "secret"
    assert payload["expiresAt"] == 1_800_000_000_000
    assert payload["model"] == "grok-voice-3.0"
    assert payload["voice"] == "ara"
    assert payload["personaApplied"] is False
    assert payload["instructions"].startswith("You are Sidekick.")
    assert minter.requests[0]["reasoning"] == "high"


def test_failed_mint_is_a_provider_error(tmp_path: Path) -> None:
    _, client = build(tmp_path, minters={"xai": FakeMinter(fail=True)})
    with client.websocket_connect("/") as ws:
        error = ask(ws, "voice.ephemeral", {"provider": "xai"})

    assert error["payload"]["code"] == "PROVIDER_ERROR"
    assert "HTTP 401" in error["payload"]["error"]


def test_minters_for_unknown_providers_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no supported provider"):
        build(tmp_path, minters={"gemini": FakeMinter()})


def test_tools_over_the_socket(tmp_path: Path) -> None:
    _, client = build(tmp_path)
    with client.websocket_connect("/") as ws:
        listed = ask(ws, "tools.list", {"profile": "voice"})["payload"]
        assert listed["groups"] == [] and listed["tools"][0]["name"] == "web_search"
        enabled = ask(
            ws, "tools.enable", {"tools": ["web_search", "nope"], "allowlist": ["web_search"]}
        )
        assert enabled["payload"] == {"enabled": ["web_search"]}
        assert [t["name"] for t in ask(ws, "tools.list")["payload"]["tools"]] == ["web_search"]
        assert (
            ask(ws, "tools.enable", {"tools": "web_search"})["payload"]["code"] == "INVALID_PARAMS"
        )
        called = ask(
            ws,
            "tools.call",
            {"name": "web_search", "args": {"q": "x"}, "sessionId": "C", "callId": "t"},
        )
        assert called == {
            "type": "tools.call.result",
            "id": "m1",
            "payload": {"name": "web_search", "result": "sunny"},
        }
        missing = ask(ws, "tools.call", {"name": "nope", "args": {}})
        assert (
            missing["type"] == "tools.call.error"
            and missing["payload"]["error"] == "Unknown tool: nope"
        )
        assert ask(ws, "tools.call", {"args": {}})["payload"]["code"] == "INVALID_PARAMS"
        assert (
            ask(ws, "tools.call", {"name": "web_search", "args": [1]})["payload"]["code"]
            == "INVALID_PARAMS"
        )


def test_a_failing_tool_answers_with_its_error(tmp_path: Path) -> None:
    _, client = build(tmp_path, tools=[FakeTool("broken", raises=True)])
    with client.websocket_connect("/") as ws:
        failed = ask(ws, "tools.call", {"name": "broken", "args": {}})

    assert failed["payload"]["result"] == {"error": "Tool error: boom", "toolName": "broken"}


def test_call_lifecycle_lands_in_history_logs_and_the_room(tmp_path: Path) -> None:
    room = FakeRoom()
    _, client = build(tmp_path, room=room)
    with client.websocket_connect("/") as ws:
        started = ask(
            ws,
            "session.start",
            {
                "sessionId": "CALL-1",
                "persona": "sidekick",
                "provider": "xai",
                "metadata": {"client": "ios"},
            },
        )
        assert (
            started["type"] == "session.started"
            and started["payload"]["session"]["state"] == "active"
        )
        ack = ask(
            ws,
            "session.log",
            {
                "sessionId": "CALL-1",
                "type": "user_speech",
                "payload": {"text": "Hi Sidekick", "timestamp": 1000},
            },
        )
        assert (
            ack["type"] == "session.log.ack"
            and ack["payload"]["entry"]["payload"]["text"] == "Hi Sidekick"
        )
        ask(
            ws,
            "session.log",
            {
                "sessionId": "CALL-1",
                "type": "user_speech",
                "payload": {"text": "Hi", "isLive": True},
            },
        )
        ask(
            ws,
            "session.log",
            {
                "sessionId": "CALL-1",
                "type": "agent_speech",
                "payload": {"text": "Hello!", "timestamp": 2000},
            },
        )
        ask(
            ws,
            "session.log",
            {
                "sessionId": "CALL-1",
                "type": "tool_call",
                "payload": {"name": "web_search", "callId": "t"},
            },
        )
        ended = ask(
            ws,
            "session.end",
            {
                "sessionId": "CALL-1",
                "reason": "user_hangup",
                "durationMs": 9000,
                "summary": "Said hi",
            },
        )
        assert ended["type"] == "session.ended" and ended["payload"]["summary"]["state"] == "ended"
        assert (
            ask(ws, "session.log", {"sessionId": "NOPE", "type": "user_speech", "payload": {}})[
                "payload"
            ]["code"]
            == "SESSION_ERROR"
        )
        assert (
            ask(ws, "session.log", {"sessionId": "CALL-1", "type": "blob", "payload": {}})[
                "payload"
            ]["code"]
            == "INVALID_PARAMS"
        )
        assert ask(ws, "session.end", {"sessionId": "NOPE"})["payload"]["code"] == "SESSION_ERROR"
        assert ask(ws, "session.start", {})["payload"]["code"] == "INVALID_PARAMS"

    assert room.copies == [
        {"call_id": "CALL-1", "speaker": "user", "text": "Hi Sidekick"},
        {"call_id": "CALL-1", "speaker": "agent", "text": "Hello!"},
    ]
    history = client.get("/api/call-history").json()
    assert history["count"] == 1
    row = history["calls"][0]
    assert row["provider"] == "grok-voice" and row["client"] == "ios" and row["durationMs"] == 9000
    assert [m["text"] for m in row["messages"]] == ["Hi Sidekick", "Hello!"]
    sessions = client.get("/api/logs").json()["sessions"]
    assert sessions[0]["id"] == "CALL-1" and sessions[0]["entryCount"] == 4
    detail = client.get("/api/logs/CALL-1").json()
    assert detail["session"]["id"] == "CALL-1" and len(detail["entries"]) == 4
    assert client.get("/api/logs/NOPE").status_code == 404


def test_a_failed_room_copy_is_reported_on_the_log(tmp_path: Path) -> None:
    _, client = build(tmp_path, room=FakeRoom(fail=True))
    with client.websocket_connect("/") as ws:
        ask(ws, "session.start", {"sessionId": "C"})
        error = ask(
            ws, "session.log", {"sessionId": "C", "type": "agent_speech", "payload": {"text": "hi"}}
        )

    assert error["payload"]["code"] == "INTERNAL_ERROR"
    assert "HTTP 503" in error["payload"]["error"]


def test_rest_tools_prompt_and_health(tmp_path: Path) -> None:
    _, client = build(tmp_path)

    plain = client.get("/api/tools").json()
    voice = client.get("/api/tools", params={"profile": "voice"}).json()
    assert plain["success"] and "profile" not in plain
    assert voice["profile"] == "voice" and len(voice["sha"]) == 12
    assert client.get("/api/tools", params={"profile": "full"}).status_code == 400

    called = client.post(
        "/api/tools/web_search/call",
        json={"args": {"q": "x"}, "callId": "t", "conversationId": "C"},
    )
    assert called.json() == {"success": True, "result": "sunny"}
    unknown = client.post("/api/tools/nope/call", json={"args": {}})
    assert unknown.status_code == 400 and unknown.json()["success"] is False

    prompt = client.get(
        "/api/voice/prompt",
        params={"provider": "grok", "surface": "ios", "persona": "sidekick", "conversationId": "C"},
    ).json()
    assert prompt["success"] and prompt["prompt"]["session"]["voice"] == "ara"
    assert (
        client.get(
            "/api/voice/prompt", params={"provider": "gemini", "conversationId": "C"}
        ).status_code
        == 404
    )
    assert client.get("/api/voice/prompt", params={"provider": "grok"}).status_code == 400

    health = client.get("/health").json()
    assert health["status"] == "ok" and health["tools"] == 6


def test_rest_tool_errors(tmp_path: Path) -> None:
    _, client = build(tmp_path, tools=[FakeTool("broken", raises=True)])
    broken = client.post("/api/tools/broken/call", json={"args": {}}).json()
    assert broken == {
        "success": True,
        "result": {"error": "Tool error: boom", "toolName": "broken"},
    }


def test_rest_log_rename_pin_delete_and_history_limits(tmp_path: Path) -> None:
    _, client = build(tmp_path)
    with client.websocket_connect("/") as ws:
        for call_id in ("A", "B", "C", "D"):
            ask(ws, "session.start", {"sessionId": call_id, "provider": "openai"})

    renamed = client.patch("/api/logs/A", json={"title": "Morning", "pinned": True}).json()
    assert renamed == {"success": True, "sessionId": "A", "title": "Morning", "pinned": True}
    bad_title = client.patch("/api/logs/A", json={"title": 3})
    bad_pin = client.patch("/api/logs/A", json={"pinned": "yes"})
    unknown = client.patch("/api/logs/NOPE", json={"title": "x"})
    assert bad_title.status_code == 400
    assert bad_pin.status_code == 400
    assert unknown.status_code == 404
    deleted = client.delete("/api/logs/B")
    deleted_again = client.delete("/api/logs/B")
    assert deleted.json() == {"deleted": True, "sessionId": "B"}
    assert deleted_again.status_code == 404
    assert client.get("/api/call-history").json()["count"] == 2  # history_default_limit
    assert client.get("/api/call-history", params={"limit": 50}).json()["count"] == 3  # max limit


def test_timeouts_are_tool_call_errors(tmp_path: Path) -> None:
    gateway, client = build(tmp_path, tools=[FakeTool("slow", delay=1)])
    gateway._tools._timeout_seconds = 0.01  # noqa: SLF001 — a short timeout for the test
    response = client.post("/api/tools/slow/call", json={"args": {}})
    assert response.status_code == 400 and "ran past" in response.json()["error"]
    with client.websocket_connect("/") as ws:
        error = ask(ws, "tools.call", {"name": "slow", "args": {}})
    assert error["type"] == "tools.call.error" and "ran past" in error["payload"]["error"]


def test_only_the_owner_is_served(tmp_path: Path) -> None:
    gateway, _ = build(tmp_path)
    stranger = TestClient(gateway.app, headers={"x-auth-user-id": "someone-else"})
    anonymous = TestClient(gateway.app)

    assert stranger.get("/health").status_code == 403
    assert anonymous.post("/api/tools/web_search/call", json={"args": {}}).status_code == 403
    for client in (stranger, anonymous):
        with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect("/") as ws:
            ws.receive_json()
        assert closed.value.code == 4403


def test_a_channel_without_an_owner_is_refused(tmp_path: Path) -> None:
    store = VoiceCallStore(str(tmp_path / "calls.db"))
    bridge = VoiceToolBridge(
        tools=[],
        permission=AllowAllPermission(),
        conversation=VoiceConversationTools(store, search_limit=1),
        agent_name="sidekick",
        timeout_seconds=1,
    )
    the_persona = persona()
    try:
        with pytest.raises(ValueError, match="owner_id"):
            VoiceGateway(
                VoiceChannelConfig(enabled=True),
                persona=the_persona,
                composer=VoicePromptComposer(
                    the_persona, models=MODELS, time_zone="UTC", tool_names=[]
                ),
                tools=bridge,
                store=store,
                minters={"xai": FakeMinter()},
                room=None,
            )
    finally:
        store.close()
