"""Tests for the broker's durable full-fidelity event log producer."""

from unittest.mock import AsyncMock, MagicMock, patch

from skuld.broker import Broker, ConversationTurn
from skuld.config import SkuldSettings


def _broker(tmp_path, **overrides) -> Broker:
    settings = SkuldSettings(
        session={"id": "s1", "workspace_dir": str(tmp_path)},
        volundr_api_url=overrides.pop("volundr_api_url", "http://volundr.test"),
        **overrides,
    )
    return Broker(settings=settings)


def _resp(status: int = 201) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = ""
    return r


class TestEnqueue:
    def test_enqueue_assigns_monotonic_seq_and_captures_frame(self, tmp_path):
        b = _broker(tmp_path)
        b._enqueue_event_log({"type": "assistant", "role": "assistant", "message": {"x": 1}})
        b._enqueue_event_log({"type": "content_block_delta", "delta": {"text": "hi"}})

        assert [e["seq"] for e in b._event_log_buffer] == [1, 2]
        assert b._event_log_buffer[0]["kind"] == "assistant"
        assert b._event_log_buffer[0]["role"] == "assistant"
        # full frame preserved verbatim (no truncation)
        assert b._event_log_buffer[1]["payload"] == {
            "type": "content_block_delta",
            "delta": {"text": "hi"},
        }

    def test_enqueue_extracts_request_id(self, tmp_path):
        b = _broker(tmp_path)
        b._enqueue_event_log({"type": "result", "request_id": "forge-web-7"})
        b._enqueue_event_log({"type": "assistant", "message": {"request_id": "forge-web-8"}})

        assert b._event_log_buffer[0]["request_id"] == "forge-web-7"
        assert b._event_log_buffer[1]["request_id"] == "forge-web-8"

    def test_enqueue_noop_when_disabled(self, tmp_path):
        b = _broker(tmp_path, event_log_enabled=False)
        b._enqueue_event_log({"type": "assistant"})
        assert b._event_log_buffer == []

    def test_enqueue_noop_without_api_url(self, tmp_path):
        b = _broker(tmp_path, volundr_api_url="")
        b._enqueue_event_log({"type": "assistant"})
        assert b._event_log_buffer == []

    def test_overflow_drops_oldest_and_inserts_gap_sentinel(self, tmp_path):
        b = _broker(tmp_path, event_log_max_buffer=3)
        for i in range(5):
            b._enqueue_event_log({"type": "content_block_delta", "i": i})

        # INV-2: a dropped-oldest window leaves a DETECTABLE sentinel, never a
        # silent hole. The newest 3 survive; seq keeps climbing (never reused);
        # a log_gap sentinel rides at the front recording what was dropped.
        kinds = [e["kind"] for e in b._event_log_buffer]
        assert kinds[0] == "log_gap"
        gap = b._event_log_buffer[0]
        assert gap["payload"]["dropped"] >= 1
        assert gap["payload"]["reason"] == "buffer_overflow"
        assert gap["payload"]["first_seq"] == gap["seq"]
        # surviving real frames keep their monotonic seq (the sentinel + 3 real)
        survivors = [e for e in b._event_log_buffer if e["kind"] != "log_gap"]
        assert [e["seq"] for e in survivors] == [3, 4, 5]
        assert gap["payload"]["first_seq"] == 1

    def test_append_turn_mirrors_conversation_turn_to_event_log(self, tmp_path):
        b = _broker(tmp_path)
        b._append_turn(
            ConversationTurn(
                id="turn-1",
                role="assistant",
                content="hello from flock",
                participant_id="flock-researcher",
            )
        )

        assert b._event_log_buffer[-1]["kind"] == "conversation.turn"
        assert b._event_log_buffer[-1]["payload"]["turn"]["content"] == "hello from flock"
        assert b._event_log_buffer[-1]["payload"]["turn"]["participant_id"] == "flock-researcher"


class TestFlush:
    async def test_flush_posts_batch_and_removes_on_success(self, tmp_path):
        b = _broker(tmp_path, event_log_batch_size=10)
        for i in range(3):
            b._enqueue_event_log({"type": "assistant", "i": i})

        client = AsyncMock()
        client.post.return_value = _resp(201)
        with patch.object(b, "_get_http_client", AsyncMock(return_value=client)):
            await b._flush_event_log()

        client.post.assert_awaited_once()
        path, kwargs = client.post.call_args[0][0], client.post.call_args[1]
        assert path == "/api/v1/forge/sessions/s1/log"
        assert len(kwargs["json"]["entries"]) == 3
        assert b._event_log_buffer == []  # removed after success

    async def test_flush_keeps_buffer_on_http_error(self, tmp_path):
        b = _broker(tmp_path)
        b._enqueue_event_log({"type": "assistant"})

        client = AsyncMock()
        client.post.return_value = _resp(503)
        with patch.object(b, "_get_http_client", AsyncMock(return_value=client)):
            await b._flush_event_log()

        assert len(b._event_log_buffer) == 1  # retained for retry

    async def test_flush_keeps_buffer_on_exception(self, tmp_path):
        b = _broker(tmp_path)
        b._enqueue_event_log({"type": "assistant"})

        client = AsyncMock()
        client.post.side_effect = RuntimeError("network down")
        with patch.object(b, "_get_http_client", AsyncMock(return_value=client)):
            await b._flush_event_log()

        assert len(b._event_log_buffer) == 1

    async def test_flush_removes_only_sent_count_when_appended_during_post(self, tmp_path):
        b = _broker(tmp_path, event_log_batch_size=2)
        b._enqueue_event_log({"type": "a"})
        b._enqueue_event_log({"type": "b"})

        async def _post(*_args, **_kwargs):
            # a frame arrives mid-flight
            b._enqueue_event_log({"type": "c"})
            return _resp(201)

        client = AsyncMock()
        client.post.side_effect = _post
        with patch.object(b, "_get_http_client", AsyncMock(return_value=client)):
            await b._flush_event_log()

        # only the 2 sent were removed; the late 'c' remains
        assert [e["kind"] for e in b._event_log_buffer] == ["c"]

    async def test_flush_empty_buffer_is_noop(self, tmp_path):
        b = _broker(tmp_path)
        client = AsyncMock()
        with patch.object(b, "_get_http_client", AsyncMock(return_value=client)):
            await b._flush_event_log()
        client.post.assert_not_called()


class TestInitResume:
    async def test_init_resumes_seq_from_backend_head(self, tmp_path):
        b = _broker(tmp_path)
        client = AsyncMock()
        head = MagicMock()
        head.status_code = 200
        head.json.return_value = {"latest_seq": 41}
        client.get.return_value = head

        with patch.object(b, "_get_http_client", AsyncMock(return_value=client)):
            await b._init_event_log()
        # cancel the worker the init spun up
        await b._stop_event_log()

        assert b._event_log_seq == 41
        # next captured frame continues after the stored head (no PK collision)
        b._enqueue_event_log({"type": "assistant"})
        assert b._event_log_buffer[-1]["seq"] == 42

    async def test_init_noop_when_disabled(self, tmp_path):
        b = _broker(tmp_path, event_log_enabled=False)
        await b._init_event_log()
        assert b._event_log_task is None
