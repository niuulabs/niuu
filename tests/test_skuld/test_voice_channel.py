"""The session voice channel end to end: WebSocket dispatch and REST (``skuld.voice.channel``)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from niuu.domain.session_voice import SESSION_VOICE_PROTOCOL
from skuld.voice.channel import (
    CLOSE_FORBIDDEN,
    SessionVoiceChannel,
    _Connection,
)
from skuld.voice.minting import VoiceMintError
from skuld.voice.session_port import SendOutcome, SendStatus, SessionTurn
from tests.test_skuld.voice_support import (
    MODELS,
    NAME_HEADER,
    OWNER,
    USER_HEADER,
    FakeMinter,
    FakeSession,
    failing_minter,
    make_app,
    make_channel,
    persona_document,
)

WS = "/voice"


def owner_client(channel: SessionVoiceChannel | None) -> TestClient:
    return TestClient(make_app(channel), headers={USER_HEADER: OWNER})


def ask(ws, message_type: str, payload: dict | None = None, message_id: str = "m1") -> dict:
    ws.send_json({"type": message_type, "id": message_id, "payload": payload or {}})
    return ws.receive_json()


def error_of(frame: dict) -> tuple[str, str]:
    assert frame["type"] == "error"
    return frame["payload"]["code"], frame["payload"]["error"]


@pytest.fixture
def setup():
    channel, session, minter = make_channel()
    return channel, session, minter, owner_client(channel)


class TestOwnerCheck:
    def test_a_channel_without_an_owner_is_refused(self):
        with pytest.raises(ValueError, match="needs the session's owner"):
            make_channel(owner_id="   ")

    def test_no_user_header_is_no_caller(self):
        channel, _, _ = make_channel()

        assert channel.caller({}) is None
        assert channel.caller({USER_HEADER: "   "}) is None

    def test_another_user_is_no_caller(self):
        channel, _, _ = make_channel()

        assert channel.caller({USER_HEADER: "someone-else"}) is None

    def test_the_owner_is_the_author_with_a_percent_decoded_name(self):
        channel, _, _ = make_channel()

        author = channel.caller({USER_HEADER: OWNER, NAME_HEADER: "Ada%20L%C3%B6velace"})

        assert author is not None
        assert author.user_id == OWNER
        assert author.display_name == "Ada Lövelace"

    def test_header_names_are_case_insensitive(self):
        channel, _, _ = make_channel()

        assert channel.caller({"X-Auth-User-Id": OWNER}) is not None


class TestWebSocketAccess:
    @pytest.mark.parametrize("headers", [{}, {USER_HEADER: "someone-else"}])
    def test_a_non_owner_is_closed_with_4403_before_accept(self, headers):
        channel, _, _ = make_channel()
        client = TestClient(make_app(channel), headers=headers)

        with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect(WS):
            pass

        assert closed.value.code == CLOSE_FORBIDDEN == 4403

    def test_no_channel_closes_the_socket(self):
        client = owner_client(None)

        with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect(WS):
            pass

        assert closed.value.code == 4403
        assert "no voice channel" in closed.value.reason

    def test_the_trailing_slash_path_is_served_too(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS + "/") as ws:
            assert ask(ws, "ping")["type"] == "pong"

    def test_an_open_connection_is_counted(self, setup):
        channel, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            ask(ws, "ping")
            assert channel.health()["connections"] == 1


class TestHandshakeFrames:
    def test_auth_ping_and_persona(self, setup):
        _, session, _, client = setup
        session.current_turns = [SessionTurn("user", "hello")]

        with client.websocket_connect(WS) as ws:
            assert ask(ws, "auth", {"token": "x"}) == {"type": "auth.ok", "id": "m1", "payload": {}}
            assert ask(ws, "ping", message_id="p") == {"type": "pong", "id": "p", "payload": {}}
            assert ask(ws, "persona.list")["payload"] == {
                "defaultPersona": "sidekick",
                "personas": ["sidekick"],
            }
            context = ask(ws, "persona.get", {"name": "sidekick"})["payload"]["context"]

        assert context["name"] == "sidekick"
        assert context["displayName"] == "Sidekick"
        assert context["voice"] == "marin"
        assert context["voices"] == {"grok-voice": "ara", "openai-realtime": "marin"}
        assert context["sources"] == ["persona:sidekick", "session:sess-1"]
        assert context["errors"] == []
        assert context["contextPrompt"].startswith("You are Sidekick.")
        assert "## SESSION CARD" in context["contextPrompt"]
        assert "- person: hello" in context["contextPrompt"]

    def test_gateway_credentials_are_empty_with_a_reason(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            result = ask(ws, "gateway.credentials")

        assert result["type"] == "gateway.credentials.result"
        assert result["payload"]["gateways"] == []
        assert "no gateway credentials" in result["payload"]["reason"]

    @pytest.mark.parametrize("message_type", ["dictation.credential", "memory.enrollment"])
    def test_dictation_and_memory_are_not_configured(self, setup, message_type):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            code, message = error_of(ask(ws, message_type))

        assert code == "NOT_CONFIGURED"
        assert message_type in message

    def test_call_presence_and_push_register_are_not_registered(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            presence = ask(ws, "call.presence", {"state": "on"})
            push = ask(ws, "push.register", {"token": "apns-token-123"})

        assert presence["type"] == "call.presence.result"
        assert presence["payload"]["registered"] is False
        assert push["type"] == "push.register.result"
        assert push["payload"]["registered"] is False
        assert "apns-token-123" not in str(push)

    def test_tools_group_toggle_is_invalid(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            code, message = error_of(ask(ws, "tools.group.toggle", {"group": "web"}))

        assert code == "INVALID_PARAMS"
        assert "no groups" in message


class TestMalformedFrames:
    def test_not_json(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            ws.send_text("not json")
            frame = ws.receive_json()

        assert frame == {
            "type": "error",
            "id": "",
            "payload": {"error": "message is not JSON", "code": "INVALID_MESSAGE"},
        }

    def test_json_that_is_not_an_object(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            ws.send_text("[1, 2]")
            frame = ws.receive_json()

        assert error_of(frame)[0] == "INVALID_MESSAGE"
        assert frame["id"] == ""

    def test_missing_id_is_invalid(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            ws.send_json({"type": "ping"})
            frame = ws.receive_json()

        assert error_of(frame) == ("INVALID_MESSAGE", "message.id is required")

    def test_missing_type_echoes_the_id(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            ws.send_json({"id": "abc"})
            frame = ws.receive_json()

        assert frame["id"] == "abc"
        assert error_of(frame) == ("INVALID_MESSAGE", "message.type is required")

    def test_unknown_type(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            frame = ask(ws, "tools.bogus", message_id="u1")

        assert frame["id"] == "u1"
        assert error_of(frame) == ("UNKNOWN_TYPE", "Unknown message: tools.bogus")

    def test_an_unexpected_exception_is_an_internal_error_without_its_text(self, setup, caplog):
        _, session, _, client = setup

        def explode():
            raise RuntimeError("secret-internal-detail")

        session.status = explode  # type: ignore[method-assign]

        with client.websocket_connect(WS) as ws:
            frame = ask(ws, "persona.get", message_id="x1")
            assert ask(ws, "ping")["type"] == "pong"

        code, message = error_of(frame)
        assert (code, frame["id"]) == ("INTERNAL_ERROR", "x1")
        assert "secret-internal-detail" not in message
        assert "see the session's log" in message
        assert "secret-internal-detail" in caplog.text

    def test_a_record_that_cannot_be_written_is_an_internal_error_not_a_silent_call(self, setup):
        channel, session, _, client = setup

        session.record_error = RuntimeError("event log is off")

        with client.websocket_connect(WS) as ws:
            frame = ask(ws, "session.start", {"sessionId": "C1"})

        assert error_of(frame)[0] == "INTERNAL_ERROR"
        assert channel.calls.calls() == []


class TestToolsOverTheSocket:
    def test_list_enable_and_call(self, setup):
        _, session, _, client = setup

        with client.websocket_connect(WS) as ws:
            listed = ask(ws, "tools.list", {"profile": "voice"})["payload"]
            assert listed["groups"] == []
            assert [t["name"] for t in listed["tools"]] == [
                "send_to_this_session",
                "this_session_status",
                "read_this_session_in_full",
            ]
            enabled = ask(ws, "tools.enable", {"tools": ["this_session_status", "nope"]})
            assert enabled == {
                "type": "tools.enable.result",
                "id": "m1",
                "payload": {"enabled": ["this_session_status"]},
            }
            assert [t["name"] for t in ask(ws, "tools.list")["payload"]["tools"]] == [
                "this_session_status"
            ]
            status = ask(ws, "tools.call", {"name": "this_session_status", "sessionId": "C1"})

        assert status["type"] == "tools.call.result"
        assert status["payload"] == {
            "name": "this_session_status",
            "result": "Refactor billing: idle",
        }

    def test_a_tool_that_is_not_enabled_answers_tools_call_error(self, setup):
        _, session, _, client = setup

        with client.websocket_connect(WS) as ws:
            ask(ws, "tools.enable", {"allowlist": ["this_session_status"]})
            refused = ask(
                ws, "tools.call", {"name": "send_to_this_session", "args": {"message": "rm -rf"}}
            )

        assert refused["type"] == "tools.call.error"
        assert refused["payload"] == {
            "name": "send_to_this_session",
            "error": "Tool send_to_this_session is not enabled on this call",
        }
        assert session.sent == []

    def test_enabling_nothing_enables_nothing(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            ask(ws, "tools.enable", {"tools": []})
            refused = ask(ws, "tools.call", {"name": "this_session_status"})

        assert refused["type"] == "tools.call.error"

    @pytest.mark.parametrize("payload", [{}, {"tools": "send"}, {"tools": [1]}])
    def test_enable_with_a_bad_list_is_invalid_params(self, setup, payload):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            assert error_of(ask(ws, "tools.enable", payload))[0] == "INVALID_PARAMS"

    def test_send_goes_to_the_session_as_the_owner_with_the_call_id(self, setup):
        _, session, _, client = setup
        client.headers[NAME_HEADER] = "Ada"

        with client.websocket_connect(WS) as ws:
            result = ask(
                ws,
                "tools.call",
                {
                    "name": "send_to_this_session",
                    "arguments": {"message": "run tests"},
                    "sessionId": "C9",
                },
            )

        assert result["type"] == "tools.call.result"
        assert result["payload"]["result"].startswith("Delivered:")
        text, author, call_id = session.sent[0]
        assert (text, call_id) == ("run tests", "C9")
        assert author is not None and (author.user_id, author.display_name) == (OWNER, "Ada")

    def test_a_tool_error_outcome_is_a_result_carrying_the_error(self, setup):
        _, session, _, client = setup
        session.outcome = SendOutcome(SendStatus.FAILED, "the session did not accept it")

        with client.websocket_connect(WS) as ws:
            result = ask(
                ws, "tools.call", {"name": "send_to_this_session", "args": {"message": "go"}}
            )

        assert result["type"] == "tools.call.result"
        assert result["payload"]["result"] == {
            "error": "Not sent: the session did not accept it. Tell the person plainly; "
            "do not say it was sent.",
            "toolName": "send_to_this_session",
        }

    def test_an_unknown_tool_is_a_tools_call_error(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            frame = ask(ws, "tools.call", {"name": "nope", "args": {}})

        assert frame["type"] == "tools.call.error"
        assert frame["payload"] == {"name": "nope", "error": "Unknown tool: nope"}

    def test_call_without_a_name_or_with_non_object_args_is_invalid_params(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            assert error_of(ask(ws, "tools.call", {"args": {}}))[0] == "INVALID_PARAMS"
            bad_args = ask(ws, "tools.call", {"name": "this_session_status", "args": [1]})
            assert error_of(bad_args) == ("INVALID_PARAMS", "args must be an object")


class TestCallLifecycle:
    def test_start_log_end_round_trip_lands_in_the_session_record_and_the_reads(self, setup):
        channel, session, _, client = setup

        with client.websocket_connect(WS) as ws:
            started = ask(
                ws,
                "session.start",
                {
                    "sessionId": "CALL-1",
                    "provider": "xai",
                    "metadata": {"client": "ios"},
                },
            )
            assert started["type"] == "session.started"
            assert started["payload"]["session"]["state"] == "active"
            ack = ask(
                ws,
                "session.log",
                {
                    "sessionId": "CALL-1",
                    "type": "user_speech",
                    "payload": {"text": "Hi", "timestamp": 1000},
                },
            )
            assert ack["type"] == "session.log.ack"
            assert ack["payload"]["entry"]["payload"]["text"] == "Hi"
            interim = ask(
                ws,
                "session.log",
                {
                    "sessionId": "CALL-1",
                    "type": "user_speech",
                    "payload": {"text": "H", "isLive": True},
                },
            )
            assert interim["type"] == "session.log.ack"
            ask(
                ws,
                "session.log",
                {
                    "sessionId": "CALL-1",
                    "type": "agent_speech",
                    "payload": {"text": "Hello!", "timestamp": 2000},
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
            assert ended["type"] == "session.ended"
            assert ended["payload"]["summary"]["state"] == "ended"

        assert [f["event"] for f in session.records] == ["started", "entry", "entry", "ended"]
        assert all(f["type"] == "voice_call" for f in session.records)
        row = client.get("/voice/api/call-history").json()["calls"][0]
        assert row["provider"] == "grok-voice"
        assert row["personaId"] == "sidekick"
        assert row["client"] == "ios"
        assert row["durationMs"] == 9000
        assert [m["text"] for m in row["messages"]] == ["Hi", "Hello!"]
        assert channel.calls.get("CALL-1").end_reason == "user_hangup"

    def test_end_defaults(self, setup):
        channel, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            ask(ws, "session.start", {"sessionId": "C", "metadata": "oops"})
            ask(ws, "session.end", {"sessionId": "C", "summary": 3, "durationMs": "long"})

        call = channel.calls.get("C")
        assert (call.end_reason, call.summary, call.duration_ms) == ("ended", None, None)

    def test_missing_ids_and_types_are_invalid_params(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            assert error_of(ask(ws, "session.start", {})) == (
                "INVALID_PARAMS",
                "sessionId is required",
            )
            assert error_of(ask(ws, "session.log", {"sessionId": "C"})) == (
                "INVALID_PARAMS",
                "type is required",
            )
            assert error_of(ask(ws, "session.end", {"sessionId": 5}))[0] == "INVALID_PARAMS"

    def test_an_unknown_call_is_a_session_error(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            logged = ask(ws, "session.log", {"sessionId": "NOPE", "type": "user_speech"})
            ended = ask(ws, "session.end", {"sessionId": "NOPE"})

        assert error_of(logged)[0] == "SESSION_ERROR"
        assert error_of(ended)[0] == "SESSION_ERROR"
        assert "NOPE" in error_of(ended)[1]

    def test_an_unknown_entry_type_is_invalid_params_and_not_recorded(self, setup):
        _, session, _, client = setup

        with client.websocket_connect(WS) as ws:
            ask(ws, "session.start", {"sessionId": "C"})
            bad = ask(ws, "session.log", {"sessionId": "C", "type": "blob", "payload": {}})

        assert error_of(bad) == ("INVALID_PARAMS", "Unknown log entry type: blob")
        assert [f["event"] for f in session.records] == ["started"]


class TestVoiceEphemeral:
    def test_success_carries_the_credential_the_voice_and_the_card(self):
        session = FakeSession()
        session.current_turns = [SessionTurn("assistant", "the build is green")]
        channel, _, minter = make_channel(session=session, minter=FakeMinter(session_bound=True))
        client = owner_client(channel)

        with client.websocket_connect(WS) as ws:
            frame = ask(ws, "voice.ephemeral", {"provider": "openai", "reasoning": "high"})

        assert frame["type"] == "voice.ephemeral.result"
        payload = frame["payload"]
        assert payload["success"] is True
        assert payload["provider"] == "openai"
        assert payload["token"] == "secret"
        assert payload["expiresAt"] == 1_800_000_000_000
        assert payload["model"] == MODELS["gpt-realtime"]
        assert payload["persona"] == "sidekick"
        assert payload["voice"] == "marin"
        assert payload["personaApplied"] is True
        assert "the build is green" in payload["instructions"]
        assert minter.requests == [
            {
                "provider": "openai",
                "model": MODELS["gpt-realtime"],
                "voice": "marin",
                "instructions": payload["instructions"],
                "reasoning": "high",
            }
        ]

    def test_the_client_may_name_the_model(self, setup):
        _, _, minter, client = setup

        with client.websocket_connect(WS) as ws:
            frame = ask(ws, "voice.ephemeral", {"provider": "xai", "model": "grok-custom"})

        assert frame["payload"]["model"] == "grok-custom"
        assert frame["payload"]["voice"] == "ara"
        assert frame["payload"]["personaApplied"] is False
        assert minter.requests[0]["model"] == "grok-custom"
        assert minter.requests[0]["reasoning"] == ""

    def test_a_missing_provider_is_invalid_params(self, setup):
        _, _, minter, client = setup

        with client.websocket_connect(WS) as ws:
            frame = ask(ws, "voice.ephemeral", {})

        assert frame["payload"] == {"error": "provider is required", "code": "INVALID_PARAMS"}
        assert minter.requests == []

    def test_an_unsupported_provider_is_refused_without_minting(self, setup):
        _, _, minter, client = setup

        with client.websocket_connect(WS) as ws:
            code, message = error_of(ask(ws, "voice.ephemeral", {"provider": "gemini"}))

        assert code == "UNSUPPORTED_PROVIDER"
        assert "gemini" in message and "['openai', 'xai']" in message
        assert minter.requests == []

    def test_a_persona_without_a_voice_for_the_provider_is_refused_without_minting(self):
        persona = persona_document(voices={"openai-realtime": "marin"})
        channel, _, minter = make_channel(persona=persona)
        client = owner_client(channel)

        with client.websocket_connect(WS) as ws:
            code, message = error_of(ask(ws, "voice.ephemeral", {"provider": "xai"}))

        assert code == "UNSUPPORTED_PROVIDER"
        assert "grok-voice" in message
        assert minter.requests == []

    def test_a_mint_error_is_a_provider_error_and_no_other_provider_is_tried(self, caplog):
        channel, _, minter = make_channel(minter=failing_minter())
        client = owner_client(channel)

        with client.websocket_connect(WS) as ws:
            code, message = error_of(ask(ws, "voice.ephemeral", {"provider": "xai"}))

        assert code == "PROVIDER_ERROR"
        assert "HTTP 401" in message
        assert len(minter.requests) == 1
        assert "Voice credential mint failed" in caplog.text

    def test_a_forge_that_cannot_be_reached_is_a_provider_error(self):
        channel, _, _ = make_channel(minter=FakeMinter(error=VoiceMintError("Forge unreachable")))
        client = owner_client(channel)

        with client.websocket_connect(WS) as ws:
            frame = ask(ws, "voice.ephemeral", {"provider": "openai"})

        assert error_of(frame) == ("PROVIDER_ERROR", "Forge unreachable")


class TestRestAccess:
    def test_no_channel_is_a_404(self):
        client = owner_client(None)

        response = client.get("/voice/health")

        assert response.status_code == 404
        assert "no voice channel" in response.json()["detail"]

    @pytest.mark.parametrize("headers", [{}, {USER_HEADER: "someone-else"}])
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/voice/health"),
            ("get", "/voice/api/tools"),
            ("post", "/voice/api/tools/this_session_status/call"),
            ("get", "/voice/api/voice/prompt?provider=grok&conversationId=c"),
            ("get", "/voice/api/call-history"),
            ("get", "/voice/api/logs"),
            ("get", "/voice/api/logs/C"),
            ("patch", "/voice/api/logs/C"),
            ("delete", "/voice/api/logs/C"),
        ],
    )
    def test_a_non_owner_is_forbidden_on_every_route(self, headers, method, path):
        channel, session, _ = make_channel()
        client = TestClient(make_app(channel), headers=headers)

        response = getattr(client, method)(path)

        assert response.status_code == 403
        assert "owner" in response.json()["detail"]
        assert session.sent == [] and session.records == []


class TestRestTools:
    def test_catalog_without_a_profile(self, setup):
        _, _, _, client = setup

        body = client.get("/voice/api/tools").json()

        assert body["success"] is True
        assert len(body["tools"]) == 3
        assert "profile" not in body and "sha" not in body

    def test_catalog_for_the_voice_profile_has_a_stable_sha(self, setup):
        _, _, _, client = setup

        first = client.get("/voice/api/tools", params={"profile": "voice"}).json()
        second = client.get("/voice/api/tools", params={"profile": "voice"}).json()

        assert first["profile"] == "voice"
        assert len(first["sha"]) == 12 and first["sha"] == second["sha"]

    def test_an_unknown_profile_is_a_400(self, setup):
        _, _, _, client = setup

        response = client.get("/voice/api/tools", params={"profile": "full"})

        assert response.status_code == 400
        assert response.json() == {"success": False, "error": "Unknown tool profile: full"}

    def test_a_tool_call_runs_as_the_owner(self, setup):
        _, session, _, client = setup

        response = client.post(
            "/voice/api/tools/send_to_this_session/call",
            json={"args": {"message": "go"}, "conversationId": "C1"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True and body["result"].startswith("Delivered:")
        assert session.sent[0][0] == "go" and session.sent[0][2] == "C1"
        assert session.sent[0][1].user_id == OWNER

    def test_the_session_id_names_the_call_when_there_is_no_conversation_id(self, setup):
        _, session, _, client = setup

        client.post(
            "/voice/api/tools/send_to_this_session/call",
            json={"args": {"message": "go"}, "sessionId": "S7"},
        )

        assert session.sent[0][2] == "S7"

    def test_a_tool_error_outcome_is_a_success_carrying_the_error(self, setup):
        _, _, _, client = setup

        response = client.post("/voice/api/tools/send_to_this_session/call", json={"args": {}})

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "result": {
                "error": "Nothing to send: the message was empty.",
                "toolName": "send_to_this_session",
            },
        }

    def test_non_object_args_run_with_none(self, setup):
        _, session, _, client = setup

        client.post("/voice/api/tools/send_to_this_session/call", json={"args": "go"})

        assert session.sent == []

    def test_an_unknown_tool_is_a_400(self, setup):
        _, _, _, client = setup

        response = client.post("/voice/api/tools/nope/call", json={"args": {}})

        assert response.status_code == 400
        assert response.json() == {"success": False, "error": "Voice tool 'nope' is not registered"}

    def test_a_body_less_call_is_served(self, setup):
        _, _, _, client = setup

        response = client.post("/voice/api/tools/this_session_status/call")

        assert response.json() == {"success": True, "result": "Refactor billing: idle"}


class TestRestPrompt:
    def test_ok(self, setup):
        _, _, _, client = setup

        response = client.get(
            "/voice/api/voice/prompt",
            params={"provider": "grok", "surface": "mac", "conversationId": "C"},
        )

        body = response.json()
        assert response.status_code == 200 and body["success"] is True
        assert body["prompt"]["session"]["voice"] == "ara"
        assert body["prompt"]["surface"] == "mac"
        assert body["prompt"]["conversationId"] == "C"

    def test_the_surface_defaults_to_ios(self, setup):
        _, _, _, client = setup

        body = client.get(
            "/voice/api/voice/prompt", params={"provider": "gpt", "conversationId": "C"}
        ).json()

        assert body["prompt"]["surface"] == "ios"

    def test_a_missing_conversation_id_is_a_400(self, setup):
        _, _, _, client = setup

        response = client.get("/voice/api/voice/prompt", params={"provider": "grok"})

        assert response.status_code == 400
        assert response.json() == {"success": False, "error": "conversationId is required"}

    def test_an_unsupported_provider_is_a_404(self, setup):
        _, _, _, client = setup

        response = client.get(
            "/voice/api/voice/prompt", params={"provider": "gemini", "conversationId": "C"}
        )

        assert response.status_code == 404
        assert "composes no voice prompt for that provider" in response.json()["error"]
        assert "gemini" not in response.json()["error"]  # no exception text reflected

    def test_a_missing_provider_is_a_422(self, setup):
        _, _, _, client = setup

        assert client.get("/voice/api/voice/prompt").status_code == 422


class TestRestCallRecord:
    def _start_calls(self, client: TestClient, *ids: str) -> None:
        with client.websocket_connect(WS) as ws:
            for call_id in ids:
                ask(ws, "session.start", {"sessionId": call_id, "provider": "openai"})

    def test_history_and_limit(self, setup):
        _, _, _, client = setup
        self._start_calls(client, "A", "B", "C")

        everything = client.get("/voice/api/call-history").json()
        limited = client.get("/voice/api/call-history", params={"limit": 2}).json()

        assert everything["count"] == 3
        assert limited["count"] == 2 and len(limited["calls"]) == 2

    def test_a_non_positive_limit_is_a_422(self, setup):
        _, _, _, client = setup

        assert client.get("/voice/api/call-history", params={"limit": 0}).status_code == 422

    def test_logs_and_one_log(self, setup):
        _, _, _, client = setup
        with client.websocket_connect(WS) as ws:
            ask(ws, "session.start", {"sessionId": "C1"})
            ask(
                ws,
                "session.log",
                {"sessionId": "C1", "type": "user_speech", "payload": {"text": "hi"}},
            )

        sessions = client.get("/voice/api/logs").json()["sessions"]
        detail = client.get("/voice/api/logs/C1").json()

        assert [s["id"] for s in sessions] == ["C1"] and sessions[0]["entryCount"] == 1
        assert detail["session"]["id"] == "C1"
        assert [e["type"] for e in detail["entries"]] == ["user_speech"]

    def test_an_unknown_log_is_a_404(self, setup):
        _, _, _, client = setup

        response = client.get("/voice/api/logs/NOPE")

        assert response.status_code == 404
        assert response.json() == {"error": "Unknown voice call"}

    def test_patch_renames_and_pins(self, setup):
        _, session, _, client = setup
        self._start_calls(client, "A")

        response = client.patch("/voice/api/logs/A", json={"title": "Morning", "pinned": True})

        assert response.json() == {
            "success": True,
            "sessionId": "A",
            "title": "Morning",
            "pinned": True,
        }
        assert session.records[-1]["event"] == "updated"

    @pytest.mark.parametrize(
        ("body", "message"),
        [
            ({"title": 3}, "title must be a string"),
            ({"pinned": "yes"}, "pinned must be a boolean"),
        ],
    )
    def test_patch_with_the_wrong_type_is_a_400_and_records_nothing(self, setup, body, message):
        _, session, _, client = setup
        self._start_calls(client, "A")
        before = len(session.records)

        response = client.patch("/voice/api/logs/A", json=body)

        assert response.status_code == 400
        assert response.json() == {"error": message}
        assert len(session.records) == before

    def test_patching_an_unknown_call_is_a_404(self, setup):
        _, _, _, client = setup

        assert client.patch("/voice/api/logs/NOPE", json={"title": "x"}).status_code == 404

    def test_delete_then_delete_again(self, setup):
        _, _, _, client = setup
        self._start_calls(client, "A")

        first = client.delete("/voice/api/logs/A")
        second = client.delete("/voice/api/logs/A")

        assert first.json() == {"deleted": True, "sessionId": "A"}
        assert second.status_code == 404
        assert client.get("/voice/api/call-history").json()["count"] == 0


class TestHistoryUnavailable:
    @pytest.mark.parametrize(
        "path", ["/voice/api/call-history", "/voice/api/logs", "/voice/api/logs/C"]
    )
    def test_history_reads_are_a_503_naming_the_reason(self, setup, path):
        channel, _, _, client = setup
        channel.history_unavailable = "ConnectError: forge down"

        response = client.get(path)

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "ConnectError: forge down" in detail
        assert "Restart the session" in detail

    def test_new_calls_can_still_be_started_and_recorded(self, setup):
        channel, session, _, client = setup
        channel.history_unavailable = "boom"

        with client.websocket_connect(WS) as ws:
            assert ask(ws, "session.start", {"sessionId": "C"})["type"] == "session.started"

        assert [f["event"] for f in session.records] == ["started"]


class TestHealth:
    def test_ok(self, setup):
        _, _, _, client = setup

        body = client.get("/voice/health").json()

        assert body["status"] == "ok"
        assert body["protocol"] == SESSION_VOICE_PROTOCOL
        assert body["tools"] == 3
        assert body["sessions"] == 0
        assert body["connections"] == 0
        assert isinstance(body["uptime"], int)
        assert "historyUnavailable" not in body

    def test_degraded_names_why(self, setup):
        channel, _, _, client = setup
        channel.history_unavailable = "RuntimeError: no log"

        body = client.get("/voice/health").json()

        assert body["status"] == "degraded"
        assert body["historyUnavailable"] == "RuntimeError: no log"

    def test_counts_active_calls_and_open_connections(self, setup):
        _, _, _, client = setup

        with client.websocket_connect(WS) as ws:
            ask(ws, "session.start", {"sessionId": "C"})
            body = client.get("/voice/health").json()

        assert body["sessions"] == 1
        assert body["connections"] == 1


class _FakeSocket:
    """Just enough of a WebSocket for ``serve``/``_Connection`` unit tests."""

    def __init__(
        self,
        *,
        messages: list[str],
        send_error: Exception | None = None,
        close_error: Exception | None = None,
        headers: dict | None = None,
    ) -> None:
        self.headers = headers if headers is not None else {USER_HEADER: OWNER}
        self._messages = list(messages)
        self._send_error = send_error
        self._close_error = close_error
        self.sent: list[Any] = []
        self.closed = 0

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        return self._messages.pop(0)

    async def send_json(self, message: Any) -> None:
        if self._send_error is not None:
            raise self._send_error
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed += 1
        if self._close_error is not None:
            raise self._close_error


class TestConnectionLoop:
    @pytest.mark.parametrize(
        "error", [RuntimeError("closed"), OSError("broken pipe"), WebSocketDisconnect()]
    )
    async def test_a_gone_socket_is_reported_not_raised(self, error):
        connection = _Connection(
            websocket=_FakeSocket(messages=[], send_error=error),  # type: ignore[arg-type]
            author=None,
        )

        assert await connection.send({"type": "pong"}) is False

    async def test_serve_stops_when_the_socket_cannot_be_written(self):
        channel, _, _ = make_channel()
        socket = _FakeSocket(
            messages=['{"type":"ping","id":"1"}', '{"type":"ping","id":"2"}'],
            send_error=RuntimeError("closed"),
            close_error=RuntimeError("already closed"),
        )

        await channel.serve(socket)  # type: ignore[arg-type]

        assert len(socket._messages) == 1  # it stopped reading after the first failed write
        assert socket.closed == 1
        assert channel.health()["connections"] == 0

    async def test_serve_ends_quietly_when_the_app_disconnects(self):
        channel, _, _ = make_channel()

        class Gone(_FakeSocket):
            async def receive_text(self) -> str:
                raise WebSocketDisconnect()

        socket = Gone(messages=[])

        await channel.serve(socket)  # type: ignore[arg-type]

        assert socket.closed == 1
        assert channel.health()["connections"] == 0
