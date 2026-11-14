"""The voice channel wired into the broker: startup, restore from the durable log, failure."""

from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from niuu.cap_telemetry import degraded_counts, reset_cap_hits
from niuu.domain.transcript_reducer import NON_BROADCAST_KINDS, is_read_path_excluded
from niuu.ports.cli.transport import TransportCapabilities
from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.voice.calls import VOICE_CALL_FRAME, CallRecordError
from skuld.voice.channel import SessionVoiceChannel
from tests.test_adapters.test_rest_session_log import InMemoryLog, allow_log_access
from tests.test_skuld.voice_support import OWNER, persona_document
from volundr.adapters.inbound.rest_session_log import create_session_log_router

_SID = "5d6a1e0a-9c0a-4d5e-8a50-6a7d0bb3c9a1"
_LOG = f"/api/v1/forge/sessions/{_SID}/log"


@pytest.fixture(autouse=True)
def _fresh_counts():
    reset_cap_hits()
    yield
    reset_cap_hits()


def _broker(tmp_path, *, voice: bool = True, **overrides) -> Broker:
    settings = SkuldSettings(
        session={
            "id": _SID,
            "name": "Refactor billing",
            "workspace_dir": str(tmp_path),
            "owner_id": OWNER,
        },
        volundr_api_url="http://forge.test",
        forge_mcp={"token": "forge-session-token"},
        voice={"enabled": voice, "default_persona": persona_document()},
        transport="subprocess",
        # Peer inbox (on by default) also needs the durable history; off, only voice asks for it.
        peer_inbox={"enabled": False},
        **overrides,
    )
    broker = Broker(settings=settings)
    broker._transport = MagicMock()
    broker._transport.is_alive = True
    broker._transport.capabilities = TransportCapabilities()
    return broker


def _frame(event: str, call_id: str = "C1", at_ms: int = 1_000, **fields):
    return {"type": "voice_call", "at_ms": at_ms, "event": event, "call_id": call_id, **fields}


def _history_frames(*payloads: dict, kind: str = VOICE_CALL_FRAME) -> list[SimpleNamespace]:
    return [SimpleNamespace(kind=kind, payload=payload) for payload in payloads]


class TestStartVoiceChannel:
    def test_off_builds_nothing(self, tmp_path):
        broker = _broker(tmp_path, voice=False)

        broker._start_voice_channel()

        assert broker._voice_channel is None

    def test_a_fresh_broker_has_no_channel_until_started(self, tmp_path):
        assert _broker(tmp_path)._voice_channel is None

    def test_on_builds_the_channel_for_this_session_and_owner(self, tmp_path):
        broker = _broker(tmp_path)

        broker._start_voice_channel()

        channel = broker._voice_channel
        assert isinstance(channel, SessionVoiceChannel)
        assert channel.caller({"x-auth-user-id": OWNER}) is not None
        assert channel.caller({"x-auth-user-id": "intruder"}) is None
        assert channel.history_unavailable is None

    def test_enabled_without_a_voice_persona_is_fatal(self, tmp_path):
        broker = _broker(tmp_path)
        broker._settings.voice.default_persona = None

        with pytest.raises(ValueError, match="voice.default_persona"):
            broker._start_voice_channel()

        assert broker._voice_channel is None


class TestRestoreDurableVoiceCalls:
    def test_folds_voice_call_frames_into_the_channel(self, tmp_path):
        broker = _broker(tmp_path)
        broker._start_voice_channel()
        frames = _history_frames(
            _frame("started", persona="sidekick", provider="xai", metadata={"client": "ios"}),
            _frame("entry", at_ms=2_000, entry_type="user_speech", payload={"text": "hi"}),
        ) + _history_frames({"type": "assistant"}, kind="assistant")

        broker._restore_durable_voice_calls(frames)

        call = broker._voice_channel.calls.get("C1")
        assert call.entry_count == 1
        assert call.metadata == {"client": "ios"}

    def test_without_a_channel_it_does_nothing(self, tmp_path):
        broker = _broker(tmp_path, voice=False)
        broker._start_voice_channel()

        broker._restore_durable_voice_calls(_history_frames(_frame("started")))

        assert broker._voice_channel is None

    def test_a_malformed_call_frame_raises_instead_of_being_skipped(self, tmp_path):
        broker = _broker(tmp_path)
        broker._start_voice_channel()

        with pytest.raises(CallRecordError):
            broker._restore_durable_voice_calls(_history_frames(_frame("entry", call_id="GONE")))

    def test_is_part_of_the_durable_runtime_state_restore(self, tmp_path):
        broker = _broker(tmp_path)
        broker._start_voice_channel()

        broker._restore_durable_runtime_state(
            _history_frames(_frame("started", persona="sidekick", provider="xai", metadata={}))
        )

        assert [c.call_id for c in broker._voice_channel.calls.calls()] == ["C1"]

    def test_logs_how_many_frames_it_restored(self, tmp_path, caplog):
        broker = _broker(tmp_path)
        broker._start_voice_channel()

        with caplog.at_level(logging.INFO, logger="skuld.broker"):
            broker._restore_durable_voice_calls(
                _history_frames(_frame("started", persona="p", provider="x", metadata={}))
            )

        assert "Restored 1 voice call record frame(s)" in caplog.text


class TestHistoryUnrestored:
    def test_marks_the_channel_and_counts_the_degradation(self, tmp_path, caplog):
        broker = _broker(tmp_path)
        broker._start_voice_channel()

        broker._voice_history_unrestored(RuntimeError("forge down"))

        assert broker._voice_channel.history_unavailable == "RuntimeError: forge down"
        assert degraded_counts() == {"skuld.voice.history": 1}
        assert "call history answers 503" in caplog.text

    def test_without_a_channel_nothing_is_marked_or_counted(self, tmp_path):
        broker = _broker(tmp_path, voice=False)

        broker._voice_history_unrestored(RuntimeError("forge down"))

        assert degraded_counts() == {}


class TestHydration:
    async def test_runs_when_only_voice_is_enabled(self, tmp_path, monkeypatch):
        broker = _broker(tmp_path)
        broker._start_voice_channel()
        calls = {"n": 0}

        async def fetch(client, *, on_frames, **kwargs):
            calls["n"] += 1
            on_frames(
                _history_frames(_frame("started", persona="sidekick", provider="xai", metadata={}))
            )
            return []

        module = importlib.import_module("skuld.broker")
        monkeypatch.setattr(module, "fetch_durable_history", fetch)
        monkeypatch.setattr(broker, "_get_session_http_client", AsyncMock())

        await broker._hydrate_conversation_history()

        assert calls["n"] == 1
        assert [c.call_id for c in broker._voice_channel.calls.calls()] == ["C1"]
        assert broker._voice_channel.history_unavailable is None

    async def test_does_not_run_when_nothing_needs_it(self, tmp_path, monkeypatch):
        broker = _broker(tmp_path, voice=False)
        fetch = AsyncMock(return_value=[])
        monkeypatch.setattr(importlib.import_module("skuld.broker"), "fetch_durable_history", fetch)

        await broker._hydrate_conversation_history()

        fetch.assert_not_awaited()

    async def test_a_failed_read_marks_the_channel_and_counts_it(
        self, tmp_path, monkeypatch, caplog
    ):
        broker = _broker(tmp_path)
        broker._start_voice_channel()
        fetch = AsyncMock(side_effect=RuntimeError("forge down"))
        monkeypatch.setattr(importlib.import_module("skuld.broker"), "fetch_durable_history", fetch)
        monkeypatch.setattr(broker, "_get_session_http_client", AsyncMock())

        await broker._hydrate_conversation_history()

        assert broker._voice_channel.history_unavailable == "RuntimeError: forge down"
        assert degraded_counts() == {"skuld.voice.history": 1}
        assert "Durable history hydration skipped" in caplog.text

    async def test_a_malformed_call_frame_in_the_log_marks_the_channel_too(
        self, tmp_path, monkeypatch
    ):
        broker = _broker(tmp_path)
        broker._start_voice_channel()

        async def fetch(client, *, on_frames, **kwargs):
            on_frames(_history_frames(_frame("ended", call_id="UNSEEN")))
            return []

        monkeypatch.setattr(importlib.import_module("skuld.broker"), "fetch_durable_history", fetch)
        monkeypatch.setattr(broker, "_get_session_http_client", AsyncMock())

        await broker._hydrate_conversation_history()

        assert "names an unknown call UNSEEN" in broker._voice_channel.history_unavailable
        assert broker._voice_channel.calls.calls() == []
        assert degraded_counts() == {"skuld.voice.history": 1}


async def _forge_log_client() -> tuple[httpx.AsyncClient, InMemoryLog]:
    app = FastAPI()
    repo = InMemoryLog()
    app.include_router(create_session_log_router(repo, session_service=allow_log_access(app)))
    client = httpx.AsyncClient(base_url="http://forge.test", transport=httpx.ASGITransport(app=app))
    return client, repo


async def test_a_call_survives_a_restart_through_forges_real_log_route(tmp_path):
    """Frames the broker records are what a restarted broker restores, over the real route."""
    first = _broker(tmp_path / "first")
    first._start_voice_channel()
    calls = first._voice_channel.calls
    calls.start("CALL-1", persona="sidekick", provider="xai", metadata={"client": "ios"})
    calls.log("CALL-1", "user_speech", {"text": "run the tests", "timestamp": 1_700_000_000_000})
    calls.log("CALL-1", "agent_speech", {"text": "running them now"})
    calls.end("CALL-1", reason="user_hangup", duration_ms=4_000, summary="Ran tests", metadata={})
    calls.update("CALL-1", title="Test run", pinned=True)
    calls.start("CALL-2", persona="sidekick", provider="openai", metadata={})
    calls.delete("CALL-2")
    expected = calls.history_json(calls.get("CALL-1"))
    assert [e["kind"] for e in first._event_log_buffer] == ["voice_call"] * 7

    client, repo = await _forge_log_client()
    async with client:
        stored = await client.post(_LOG, json={"entries": first._event_log_buffer})
        assert stored.status_code == 201
        assert await repo.latest_seq(UUID(_SID)) == 7

        second = _broker(tmp_path / "second")
        second._start_voice_channel()
        second._http_client = client
        second._session_http_client = client
        await second._hydrate_conversation_history()

        # The ordinary wire read never carries them: they are the call record, not conversation.
        wire = await client.get(_LOG, params={"after": 0, "show_internal": "true"})
        assert wire.status_code == 200 and wire.json() == []

    restored = second._voice_channel
    assert restored.history_unavailable is None
    assert [c.call_id for c in restored.calls.calls()] == ["CALL-1"]
    assert restored.calls.history_json(restored.calls.get("CALL-1")) == expected
    assert expected["title"] == "Test run" and expected["pinned"] is True
    assert [m["text"] for m in expected["messages"]] == ["run the tests", "running them now"]


async def test_startup_starts_the_channel_before_it_hydrates(tmp_path, monkeypatch):
    module = importlib.import_module("skuld.broker")
    broker = _broker(tmp_path)
    seen: dict[str, object] = {}

    async def fetch(client, *, on_frames, **kwargs):
        seen["channel_at_hydration"] = broker._voice_channel
        on_frames(
            _history_frames(_frame("started", persona="sidekick", provider="xai", metadata={}))
        )
        return []

    def create_transport():
        seen["calls_at_transport"] = [c.call_id for c in broker._voice_channel.calls.calls()]
        return broker._transport

    monkeypatch.setattr(module, "fetch_durable_history", fetch)
    monkeypatch.setattr(broker, "_get_session_http_client", AsyncMock())
    monkeypatch.setattr(broker, "_create_transport", create_transport)
    monkeypatch.setattr(broker, "_auto_start_transport", AsyncMock())
    for name in ("_ensure_session_trace_started", "_init_telegram_channel", "_init_event_log"):
        monkeypatch.setattr(broker, name, AsyncMock())
    manager = MagicMock()
    manager.init = AsyncMock()
    monkeypatch.setattr(module, "ServiceManager", MagicMock(return_value=manager))

    await broker.startup()

    assert isinstance(seen["channel_at_hydration"], SessionVoiceChannel)
    assert seen["calls_at_transport"] == ["C1"]


async def test_startup_with_voice_off_has_no_channel(tmp_path, monkeypatch):
    module = importlib.import_module("skuld.broker")
    broker = _broker(tmp_path, voice=False)
    monkeypatch.setattr(broker, "_create_transport", lambda: broker._transport)
    monkeypatch.setattr(broker, "_auto_start_transport", AsyncMock())
    for name in ("_ensure_session_trace_started", "_init_telegram_channel", "_init_event_log"):
        monkeypatch.setattr(broker, name, AsyncMock())
    manager = MagicMock()
    manager.init = AsyncMock()
    monkeypatch.setattr(module, "ServiceManager", MagicMock(return_value=manager))

    await broker.startup()

    assert broker._voice_channel is None


class TestRouteMount:
    def test_the_broker_app_serves_the_voice_routes_and_404s_while_off(self):
        from fastapi.testclient import TestClient

        from skuld import broker_api

        previous = broker_api.broker._voice_channel
        broker_api.broker._voice_channel = None
        try:
            client = TestClient(broker_api.app, headers={"x-auth-user-id": OWNER})
            response = client.get("/voice/health")
        finally:
            broker_api.broker._voice_channel = previous

        assert response.status_code == 404
        assert "no voice channel" in response.json()["detail"]


class TestTranscriptReducer:
    def test_voice_call_frames_are_never_on_the_wire_read_path(self):
        assert "voice_call" in NON_BROADCAST_KINDS
        assert is_read_path_excluded("voice_call", {"type": "voice_call", "event": "entry"})
        assert is_read_path_excluded("voice_call", None)

    def test_conversation_frames_are_still_read(self):
        assert not is_read_path_excluded("assistant", {"type": "assistant"})
