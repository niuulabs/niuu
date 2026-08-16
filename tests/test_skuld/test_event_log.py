"""Tests for the broker's durable full-fidelity event log producer."""

import asyncio
from contextlib import suppress
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from skuld import event_log as event_log_mod
from skuld.broker import Broker, ConversationTurn
from skuld.config import SkuldSettings
from skuld.event_log import EventLogRejectedError


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

    def test_sustained_overflow_sentinel_reports_true_hole_size(self, tmp_path):
        # M-4: under SUSTAINED overflow a prior sentinel is itself re-dropped. If it were
        # counted as one frame the reported `dropped` would under-report the true hole — only
        # the latest slice — so the gap silently shrinks. Folding its accumulated count back in
        # keeps `dropped` == the full cumulative hole (== last_seq - first_seq + 1).
        b = _broker(tmp_path, event_log_max_buffer=3)
        n = 50
        for i in range(n):
            b._enqueue_event_log({"type": "content_block_delta", "i": i})

        gaps = [e for e in b._event_log_buffer if e["kind"] == "log_gap"]
        # exactly ONE sentinel survives (the prior one is always folded into the new one)
        assert len(gaps) == 1
        gap = gaps[0]
        survivors = [e for e in b._event_log_buffer if e["kind"] != "log_gap"]
        # the newest 3 real frames survive; everything older is one cumulative hole
        assert [e["seq"] for e in survivors] == [n - 2, n - 1, n]
        # the TRUE hole is the whole covered range, not just the most recent slice
        assert gap["payload"]["first_seq"] == 1
        assert gap["payload"]["last_seq"] == n - 3
        true_hole = gap["payload"]["last_seq"] - gap["payload"]["first_seq"] + 1
        assert gap["payload"]["dropped"] == true_hole
        assert gap["payload"]["dropped"] == n - 3
        assert gap["seq"] == gap["payload"]["first_seq"]

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


class TestPermanentRejection:
    """A configured durable log that Volundr permanently rejects is fatal.

    Retrying a 4xx forever while logging at DEBUG is the silent-fallback
    pattern the no-fallbacks rule forbids: the broker keeps chatting while
    its transcript records nothing.
    """

    async def test_flush_raises_on_permanent_rejection(self, tmp_path):
        b = _broker(tmp_path)
        b._enqueue_event_log({"type": "assistant"})

        client = AsyncMock()
        rejection = _resp(422)
        rejection.text = "value is not a valid uuid"
        client.post.return_value = rejection
        with patch.object(b, "_get_http_client", AsyncMock(return_value=client)):
            with pytest.raises(EventLogRejectedError, match="session UUID"):
                await b._flush_event_log()

    @pytest.mark.parametrize("status", [408, 429, 500, 503])
    async def test_flush_retries_transient_statuses(self, tmp_path, status):
        b = _broker(tmp_path)
        b._enqueue_event_log({"type": "assistant"})

        client = AsyncMock()
        client.post.return_value = _resp(status)
        with patch.object(b, "_get_http_client", AsyncMock(return_value=client)):
            await b._flush_event_log()

        assert len(b._event_log_buffer) == 1  # retained for retry, no raise

    async def test_flush_loop_propagates_permanent_rejection(self, tmp_path):
        b = _broker(tmp_path, event_log_flush_interval_ms=1)

        with patch.object(
            b, "_flush_event_log", AsyncMock(side_effect=EventLogRejectedError("rejected"))
        ):
            with pytest.raises(EventLogRejectedError):
                await b._event_log_flush_loop()

    async def test_flush_loop_swallows_transient_errors(self, tmp_path):
        b = _broker(tmp_path, event_log_flush_interval_ms=1)
        calls = 0

        async def _flaky():
            nonlocal calls
            calls += 1
            if calls >= 3:
                b._event_log_stopping = True
                return
            raise RuntimeError("network blip")

        with patch.object(b, "_flush_event_log", _flaky):
            await b._event_log_flush_loop()

        assert calls == 3

    async def test_init_raises_when_head_fetch_is_rejected(self, tmp_path):
        b = _broker(tmp_path)
        client = AsyncMock()
        rejection = _resp(422)
        rejection.text = "value is not a valid uuid"
        client.get.return_value = rejection

        with patch.object(b, "_get_http_client", AsyncMock(return_value=client)):
            with pytest.raises(EventLogRejectedError, match="session UUID"):
                await b._init_event_log()
        assert b._event_log_task is None  # no worker started against a dead log

    async def test_init_raises_when_volundr_is_unreachable(self, tmp_path):
        b = _broker(tmp_path)
        client = AsyncMock()
        client.get.side_effect = httpx.ConnectError("connection refused")

        with patch.object(b, "_get_http_client", AsyncMock(return_value=client)):
            with pytest.raises(EventLogRejectedError, match="unreachable"):
                await b._init_event_log()
        assert b._event_log_task is None

    async def test_worker_death_terminates_the_broker(self, tmp_path):
        b = _broker(tmp_path)

        async def _doomed():
            raise EventLogRejectedError("rejected")

        task = asyncio.get_running_loop().create_task(_doomed())
        with suppress(EventLogRejectedError):
            await task

        with patch.object(event_log_mod.signal, "raise_signal") as raise_signal:
            b._on_event_log_worker_done(task)
        raise_signal.assert_called_once_with(event_log_mod.signal.SIGTERM)

    async def test_cancelled_worker_does_not_terminate_the_broker(self, tmp_path):
        b = _broker(tmp_path)

        task = asyncio.get_running_loop().create_task(asyncio.sleep(60))
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        with patch.object(event_log_mod.signal, "raise_signal") as raise_signal:
            b._on_event_log_worker_done(task)
        raise_signal.assert_not_called()

    async def test_shutdown_drain_reports_rejection_instead_of_raising(self, tmp_path):
        b = _broker(tmp_path)
        b._enqueue_event_log({"type": "assistant"})

        with patch.object(
            b, "_flush_event_log", AsyncMock(side_effect=EventLogRejectedError("rejected"))
        ):
            await b._stop_event_log()  # must not raise mid-teardown

        assert len(b._event_log_buffer) == 1  # loss is reported, not hidden
