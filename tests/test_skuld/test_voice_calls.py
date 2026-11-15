"""The session's voice calls as durable ``voice_call`` frames (``skuld.voice.calls``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from skuld.voice import calls as calls_module
from skuld.voice.calls import (
    VOICE_CALL_FRAME,
    CallRecordError,
    UnknownCallError,
    VoiceCallBook,
    iso,
)


class Clock:
    """A settable ``now_ms`` so recorded times are exact."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, start: int = 1_000) -> None:
        self.now = start
        monkeypatch.setattr(calls_module, "now_ms", lambda: self.now)


@pytest.fixture
def clock(monkeypatch):
    return Clock(monkeypatch)


@pytest.fixture
def records() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def book(records, clock) -> VoiceCallBook:
    return VoiceCallBook(records.append)


def _start(book: VoiceCallBook, call_id: str = "C1", **metadata: Any):
    return book.start(call_id, persona="sidekick", provider="xai", metadata=metadata)


def _frames(records: list[dict[str, Any]]) -> list[SimpleNamespace]:
    return [SimpleNamespace(kind=VOICE_CALL_FRAME, payload=frame) for frame in records]


class TestStartAndReopen:
    def test_start_opens_an_active_call_and_records_the_frame(self, book, records, clock):
        call = _start(book, client="ios")

        assert call.state == "active"
        assert call.created_at_ms == 1_000
        assert (call.persona, call.provider) == ("sidekick", "xai")
        assert call.metadata == {"client": "ios"}
        assert records == [
            {
                "type": "voice_call",
                "at_ms": 1_000,
                "event": "started",
                "call_id": "C1",
                "persona": "sidekick",
                "provider": "xai",
                "metadata": {"client": "ios"},
            }
        ]
        assert book.active_count() == 1

    def test_starting_an_id_again_reopens_it_and_merges_metadata(self, book, clock):
        _start(book, client="ios", a=1)
        book.end("C1", reason="hangup", duration_ms=5, summary="s", metadata={})
        clock.now = 9_000

        reopened = _start(book, a=2, b=3)

        assert reopened.state == "active"
        assert reopened.ended_at_ms is None
        assert reopened.end_reason is None
        assert reopened.created_at_ms == 1_000
        assert reopened.metadata == {"client": "ios", "a": 2, "b": 3}
        assert len(book.calls()) == 1


class TestLog:
    def test_an_entry_is_recorded_then_folded(self, book, records, clock):
        _start(book)
        clock.now = 2_000

        entry = book.log("C1", "user_speech", {"text": "hi"})

        assert (entry.call_id, entry.type, entry.created_at_ms) == ("C1", "user_speech", 2_000)
        assert records[-1] == {
            "type": "voice_call",
            "at_ms": 2_000,
            "event": "entry",
            "call_id": "C1",
            "entry_type": "user_speech",
            "payload": {"text": "hi"},
        }
        assert book.get("C1").entry_count == 1

    def test_logging_to_an_unknown_call_raises(self, book, records):
        with pytest.raises(UnknownCallError, match="Unknown voice call: NOPE"):
            book.log("NOPE", "user_speech", {})

        assert records == []

    def test_an_unknown_entry_type_raises_and_records_nothing(self, book, records):
        _start(book)
        before = len(records)

        with pytest.raises(ValueError, match="Unknown log entry type: blob"):
            book.log("C1", "blob", {})

        assert len(records) == before

    def test_an_interim_speech_entry_is_acknowledged_not_recorded(self, book, records):
        _start(book)
        before = len(records)

        entry = book.log("C1", "user_speech", {"text": "hel", "isLive": True})

        assert entry.payload == {"text": "hel", "isLive": True}
        assert len(records) == before
        assert book.get("C1").entry_count == 0

    def test_is_live_false_is_recorded(self, book):
        _start(book)

        book.log("C1", "user_speech", {"text": "hello", "isLive": False})

        assert book.get("C1").entry_count == 1


class TestEndUpdateDelete:
    def test_end_closes_the_call(self, book, clock):
        _start(book, a=1)
        clock.now = 4_000

        call = book.end(
            "C1", reason="user_hangup", duration_ms=3_000, summary="Said hi", metadata={"b": 2}
        )

        assert call.state == "ended"
        assert call.ended_at_ms == 4_000
        assert call.end_reason == "user_hangup"
        assert call.duration_ms == 3_000
        assert call.summary == "Said hi"
        assert call.metadata == {"a": 1, "b": 2}
        assert book.active_count() == 0

    def test_end_of_an_unknown_call_raises_without_recording(self, book, records):
        with pytest.raises(UnknownCallError):
            book.end("NOPE", reason="x", duration_ms=None, summary=None, metadata={})

        assert records == []

    def test_update_sets_title_and_pin(self, book, records):
        _start(book)

        call = book.update("C1", title="Morning", pinned=True)

        assert (call.title, call.pinned) == ("Morning", True)
        assert records[-1]["event"] == "updated"
        assert records[-1]["title"] == "Morning"

    def test_update_only_changes_what_is_given(self, book, records):
        _start(book)
        book.update("C1", title="Morning", pinned=True)

        call = book.update("C1", pinned=False)

        assert (call.title, call.pinned) == ("Morning", False)
        assert "title" not in records[-1]

    def test_update_of_an_unknown_call_raises(self, book):
        with pytest.raises(UnknownCallError):
            book.update("NOPE", title="x")

    def test_delete_hides_the_call_but_the_frames_stay(self, book, records):
        _start(book)
        book.log("C1", "user_speech", {"text": "hi"})

        book.delete("C1")

        assert book.calls() == []
        with pytest.raises(UnknownCallError):
            book.get("C1")
        assert [frame["event"] for frame in records] == ["started", "entry", "deleted"]

    def test_delete_of_an_unknown_call_raises(self, book):
        with pytest.raises(UnknownCallError):
            book.delete("NOPE")


class TestRecordBeforeFold:
    def test_a_record_callback_that_raises_leaves_the_book_unchanged(self, clock):
        failures = {"on": False}
        recorded: list[dict] = []

        def record(frame: dict) -> None:
            if failures["on"]:
                raise RuntimeError("the event log is gone")
            recorded.append(frame)

        book = VoiceCallBook(record)
        failures["on"] = True

        with pytest.raises(RuntimeError, match="event log is gone"):
            _start(book)

        assert book.calls() == []
        failures["on"] = False
        _start(book)
        failures["on"] = True
        with pytest.raises(RuntimeError):
            book.log("C1", "user_speech", {"text": "hi"})
        with pytest.raises(RuntimeError):
            book.update("C1", title="t")
        with pytest.raises(RuntimeError):
            book.end("C1", reason="r", duration_ms=1, summary=None, metadata={})
        with pytest.raises(RuntimeError):
            book.delete("C1")
        call = book.get("C1")
        assert call.entry_count == 0
        assert call.title is None
        assert call.state == "active"
        assert len(recorded) == 1

    def test_the_frame_is_recorded_before_it_is_folded(self, clock):
        seen: dict[str, Any] = {}

        def record(frame: dict) -> None:
            seen["calls_at_record"] = len(book.calls())

        book = VoiceCallBook(record)

        _start(book)

        assert seen["calls_at_record"] == 0
        assert len(book.calls()) == 1


class TestRestore:
    def _busy_book(self, records, clock) -> VoiceCallBook:
        book = VoiceCallBook(records.append)
        _start(book, "A", client="ios")
        clock.now = 1_500
        book.log("A", "user_speech", {"text": "hello", "timestamp": 1_200})
        book.log("A", "tool_call", {"name": "web_search", "callId": "t1", "args": {"q": "x"}})
        book.log("A", "tool_result", {"callId": "t1", "result": "sunny", "durationMs": 20})
        clock.now = 2_000
        book.end("A", reason="done", duration_ms=1_000, summary="chat", metadata={"x": 1})
        book.update("A", title="Hello", pinned=True)
        clock.now = 3_000
        _start(book, "B")
        _start(book, "GONE")
        book.delete("GONE")
        return book

    def test_a_restored_book_is_the_one_that_wrote_the_frames(self, records, clock):
        original = self._busy_book(records, clock)

        restored = VoiceCallBook(lambda frame: None)
        folded = restored.restore(_frames(records))

        assert folded == len(records)
        assert [c.call_id for c in restored.calls()] == ["B", "A"]
        for call in original.calls():
            assert restored.history_json(restored.get(call.call_id)) == original.history_json(call)
            assert restored.session_json(restored.get(call.call_id)) == original.session_json(call)

    def test_restore_ignores_other_kinds_and_records_nothing(self, records, clock):
        book = VoiceCallBook(records.append)
        frames = [
            SimpleNamespace(kind="assistant", payload={"type": "assistant"}),
            SimpleNamespace(kind="user_confirmed", payload={}),
            SimpleNamespace(
                kind=VOICE_CALL_FRAME,
                payload={
                    "type": "voice_call",
                    "at_ms": 5,
                    "event": "started",
                    "call_id": "C",
                    "persona": "p",
                    "provider": "x",
                    "metadata": {},
                },
            ),
            object(),
        ]

        assert book.restore(frames) == 1

        assert [c.call_id for c in book.calls()] == ["C"]
        assert records == []

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ("not a frame", "must be an object"),
            ({"event": "nope", "call_id": "C", "at_ms": 1}, "no known event and call id"),
            ({"event": "started", "call_id": "", "at_ms": 1}, "no known event and call id"),
            ({"event": "started", "call_id": 5, "at_ms": 1}, "no known event and call id"),
            ({"event": "started", "call_id": "C"}, "has no at_ms"),
            ({"event": "started", "call_id": "C", "at_ms": "1"}, "has no at_ms"),
            ({"event": "entry", "call_id": "C", "at_ms": 1}, "names an unknown call C"),
            ({"event": "ended", "call_id": "C", "at_ms": 1}, "names an unknown call C"),
        ],
    )
    def test_a_malformed_frame_raises_instead_of_being_skipped(self, payload, message):
        book = VoiceCallBook(lambda frame: None)

        with pytest.raises(CallRecordError, match=message):
            book.restore([SimpleNamespace(kind=VOICE_CALL_FRAME, payload=payload)])

    def test_odd_field_shapes_fold_to_safe_values(self):
        book = VoiceCallBook(lambda frame: None)
        frames = [
            {"event": "started", "call_id": "C", "at_ms": 10, "metadata": "oops"},
            {"event": "entry", "call_id": "C", "at_ms": 11, "payload": "oops"},
            {"event": "updated", "call_id": "C", "at_ms": 12, "title": 7, "pinned": "yes"},
            {
                "event": "ended",
                "call_id": "C",
                "at_ms": 13,
                "duration_ms": "long",
                "summary": 5,
                "metadata": [],
            },
        ]

        book.restore([SimpleNamespace(kind=VOICE_CALL_FRAME, payload=f) for f in frames])

        call = book.get("C")
        assert call.metadata == {}
        assert call.entries[0].payload == {}
        assert call.entries[0].type == ""
        assert call.title is None and call.pinned is False
        assert call.duration_ms is None
        assert call.summary is None
        assert call.end_reason == "ended"
        assert call.state == "ended"

    def test_a_float_duration_folds_to_an_int(self):
        book = VoiceCallBook(lambda frame: None)
        frames = [
            {"event": "started", "call_id": "C", "at_ms": 10},
            {"event": "ended", "call_id": "C", "at_ms": 13, "duration_ms": 1234.9},
        ]

        book.restore([SimpleNamespace(kind=VOICE_CALL_FRAME, payload=f) for f in frames])

        assert book.get("C").duration_ms == 1234


class TestReads:
    def test_calls_are_newest_first_and_limited(self, book, clock):
        for number, call_id in enumerate(("A", "B", "C")):
            clock.now = 1_000 + number
            _start(book, call_id)

        assert [c.call_id for c in book.calls()] == ["C", "B", "A"]
        assert [c.call_id for c in book.calls(2)] == ["C", "B"]

    def test_session_json_shape(self, book, clock):
        _start(book, client="ios")
        book.log("C1", "user_speech", {"text": "x"})
        book.update("C1", title="Morning", pinned=True)

        row = book.session_json(book.get("C1"))

        assert row == {
            "id": "C1",
            "state": "active",
            "createdAt": "1970-01-01T00:00:01Z",
            "endedAt": None,
            "entryCount": 1,
            "pinned": True,
            "metadata": {"client": "ios", "title": "Morning", "pinned": True},
        }

    def test_session_json_of_an_untitled_call_has_no_title(self, book):
        _start(book)

        assert "title" not in book.session_json(book.get("C1"))["metadata"]

    def test_entry_json_shape(self, book, clock):
        _start(book)
        clock.now = 2_000
        entry = book.log("C1", "agent_speech", {"text": "hello"})

        assert VoiceCallBook.entry_json(entry) == {
            "sessionId": "C1",
            "timestamp": "1970-01-01T00:00:02Z",
            "type": "agent_speech",
            "payload": {"text": "hello"},
        }

    def test_iso_of_none_is_none(self):
        assert iso(None) is None


class TestHistoryJson:
    def test_pairs_tool_calls_with_their_results_and_drops_empty_speech(self, book, clock):
        _start(book, client="ios", agentId="agent-7", continuedFrom="C0")
        clock.now = 1_100
        book.log("C1", "user_speech", {"text": "  hello  ", "entryId": "u1", "timestamp": 1_050})
        book.log("C1", "agent_speech", {"text": "   "})
        book.log("C1", "agent_speech", {"text": ""})
        book.log("C1", "agent_speech", {"text": "hi there"})
        book.log("C1", "tool_call", {"name": "web_search", "callId": "t1", "args": {"q": "x"}})
        clock.now = 1_300
        book.log("C1", "tool_result", {"callId": "t1", "result": "sunny", "durationMs": 40})
        book.log("C1", "tool_call", {"name": "slow", "callId": "t2"})
        book.log("C1", "tool_result", {"callId": "t2", "error": "boom"})
        book.log("C1", "system_event", {"what": "x"})
        book.log("C1", "error", {"what": "y"})
        clock.now = 5_000
        book.end("C1", reason="done", duration_ms=None, summary="chat", metadata={})

        row = book.history_json(book.get("C1"))

        assert [m["text"] for m in row["messages"]] == ["hello", "hi there"]
        assert row["messages"][0] == {
            "id": "u1",
            "role": "user",
            "text": "hello",
            "timestamp": 1_050,
            "isFinal": True,
        }
        assert row["messages"][1]["id"] == "C1-1"
        assert row["messages"][1]["role"] == "assistant"
        first, second = row["toolCalls"]
        assert first == {
            "id": "t1",
            "name": "web_search",
            "status": "completed",
            "startedAt": 1_100,
            "args": {"q": "x"},
            "completedAt": 1_300,
            "result": "sunny",
            "durationMs": 40,
        }
        assert second["status"] == "error"
        assert "durationMs" not in second
        assert row["provider"] == "grok-voice"
        assert row["client"] == "ios"
        assert row["agentId"] == "agent-7"
        assert row["personaId"] == "sidekick"
        assert row["continuedFrom"] == "C0"
        assert row["startedAt"] == 1_000
        assert row["endedAt"] == 5_000
        assert row["durationMs"] == 4_000
        assert row["summary"] == "chat"

    def test_a_tool_result_without_its_call_is_still_listed(self, book):
        _start(book)
        book.log("C1", "tool_result", {"callId": "orphan", "result": 1})

        row = book.history_json(book.get("C1"))

        assert row["toolCalls"][0]["id"] == "orphan"
        assert row["toolCalls"][0]["status"] == "completed"

    def test_a_tool_entry_without_a_call_id_gets_its_own_id(self, book):
        _start(book)
        book.log("C1", "tool_call", {"name": "a"})
        book.log("C1", "tool_call", {"name": "b"})

        ids = {t["id"] for t in book.history_json(book.get("C1"))["toolCalls"]}

        assert len(ids) == 2

    def test_defaults_for_an_unknown_client_provider_and_a_live_call(self, book, clock):
        book.start("C1", persona="p", provider="mystery", metadata={})
        clock.now = 1_700

        row = book.history_json(book.get("C1"))

        assert row["provider"] == "mystery"
        assert row["client"] == "unknown"
        assert row["agentId"] == "p"
        assert row["durationMs"] == 700
        assert row["endedAt"] is None
        assert row["continuedFrom"] is None

    @pytest.mark.parametrize(
        ("raw", "mapped"),
        [
            ("grok", "grok-voice"),
            ("OPENAI", "openai-realtime"),
            ("live", "openai-live"),
            ("gemini", "gemini-live"),
            ("google", "gemini-live"),
        ],
    )
    def test_history_provider_names(self, book, raw, mapped):
        book.start("C1", persona="p", provider=raw, metadata={})

        assert book.history_json(book.get("C1"))["provider"] == mapped
