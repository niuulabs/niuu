"""The voice call store and its projections into a voice client.s shapes."""

from __future__ import annotations

from pathlib import Path

import pytest

from ravn.adapters.channels.voice.store import UnknownCallError, VoiceCallStore, iso


@pytest.fixture
def store(tmp_path: Path) -> VoiceCallStore:
    store = VoiceCallStore(str(tmp_path / "voice" / "calls.db"))
    yield store
    store.close()


def _call(store: VoiceCallStore, call_id: str = "CALL-1") -> None:
    store.start(
        call_id,
        persona="sidekick",
        provider="xai",
        metadata={"client": "ios", "agentId": "sidekick"},
    )


def test_start_log_end_projects_a_call_history_row(store: VoiceCallStore) -> None:
    _call(store)
    store.log(
        "CALL-1", "user_speech", {"text": "What's on today?", "timestamp": 1000, "entryId": "u1"}
    )
    store.log("CALL-1", "user_speech", {"text": "partial", "isLive": True})
    store.log(
        "CALL-1",
        "tool_call",
        {"callId": "t1", "name": "tracker_issue", "args": {"q": "x"}, "timestamp": 1100},
    )
    store.log(
        "CALL-1",
        "tool_result",
        {
            "callId": "t1",
            "name": "tracker_issue",
            "result": "2 issues",
            "durationMs": 40,
            "timestamp": 1140,
        },
    )
    store.log("CALL-1", "tool_result", {"callId": "t2", "name": "web_search", "error": "down"})
    store.log("CALL-1", "agent_speech", {"text": "Two open issues.", "timestamp": 1200})
    store.log("CALL-1", "system_event", {"text": "call_summary"})
    call = store.end(
        "CALL-1",
        reason="user_hangup",
        duration_ms=5000,
        summary="Checked issues",
        metadata={"x": 1},
    )

    row = store.history_json(call)

    assert row["provider"] == "grok-voice"
    assert row["client"] == "ios"
    assert row["state"] == "ended"
    assert row["durationMs"] == 5000
    assert row["summary"] == "Checked issues"
    assert [(m["role"], m["text"]) for m in row["messages"]] == [
        ("user", "What's on today?"),
        ("assistant", "Two open issues."),
    ]
    assert row["messages"][0] == {
        "id": "u1",
        "role": "user",
        "text": "What's on today?",
        "timestamp": 1000,
        "isFinal": True,
    }
    first, second = row["toolCalls"]
    assert first == {
        "id": "t1",
        "name": "tracker_issue",
        "status": "completed",
        "startedAt": 1100,
        "args": {"q": "x"},
        "completedAt": 1140,
        "result": "2 issues",
        "durationMs": 40,
    }
    assert second["status"] == "error"


def test_session_and_entry_json_use_iso_times(store: VoiceCallStore) -> None:
    _call(store)
    store.log("CALL-1", "user_speech", {"text": "hi"})
    call = store.update("CALL-1", title="Morning", pinned=True)

    session = store.session_json(call)
    entry = store.entry_json(store.entries("CALL-1")[0])

    assert session["id"] == "CALL-1"
    assert session["state"] == "active"
    assert session["createdAt"].endswith("Z")
    assert session["endedAt"] is None
    assert session["entryCount"] == 1
    assert session["pinned"] is True
    assert session["metadata"]["title"] == "Morning"
    assert entry["sessionId"] == "CALL-1"
    assert entry["timestamp"].endswith("Z")
    assert entry["payload"] == {"text": "hi"}


def test_starting_an_ended_call_reopens_it_and_merges_metadata(store: VoiceCallStore) -> None:
    _call(store)
    store.end("CALL-1", reason="done", duration_ms=None, summary=None, metadata={})

    call = store.start(
        "CALL-1", persona="sidekick", provider="xai", metadata={"continuedFrom": "OLD"}
    )

    assert call.state == "active"
    assert call.ended_at_ms is None
    assert call.metadata == {"client": "ios", "agentId": "sidekick", "continuedFrom": "OLD"}
    assert store.history_json(call)["continuedFrom"] == "OLD"


def test_unknown_calls_and_entry_types_raise(store: VoiceCallStore) -> None:
    with pytest.raises(UnknownCallError):
        store.log("NOPE", "user_speech", {})
    with pytest.raises(UnknownCallError):
        store.end("NOPE", reason="x", duration_ms=None, summary=None, metadata={})
    with pytest.raises(UnknownCallError):
        store.update("NOPE", title="x")
    with pytest.raises(UnknownCallError):
        store.delete("NOPE")
    with pytest.raises(UnknownCallError):
        store.get("NOPE")
    _call(store)
    with pytest.raises(ValueError, match="Unknown log entry type"):
        store.log("CALL-1", "transcript_blob", {})


def test_calls_are_newest_first_and_search_finds_words_and_titles(store: VoiceCallStore) -> None:
    _call(store, "A")
    _call(store, "B")
    store.log("A", "user_speech", {"text": "Ship the release notes"})
    store.update("B", title="Garden plans")

    assert {c.call_id for c in store.calls(10)} == {"A", "B"}
    assert [c.call_id for c in store.search("release", 10)] == ["A"]
    assert [c.call_id for c in store.search("garden", 10)] == ["B"]


def test_delete_removes_the_call_and_its_entries(store: VoiceCallStore) -> None:
    _call(store)
    store.log("CALL-1", "user_speech", {"text": "hi"})

    store.delete("CALL-1")

    assert store.calls(10) == []
    assert store.entries("CALL-1") == []


def test_live_call_duration_runs_from_its_start(store: VoiceCallStore) -> None:
    _call(store)

    row = store.history_json(store.get("CALL-1"))

    assert row["endedAt"] is None
    assert row["durationMs"] >= 0


def test_iso_of_none_is_none() -> None:
    assert iso(None) is None
