"""Fault injection at the broker -> durable log boundary, with real buffer mutation."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tests.test_skuld.test_event_log import _broker, _resp


@pytest.mark.parametrize("arrivals", [1, 3, 10])
async def test_overflow_during_post_never_acknowledges_unsent_frames(tmp_path, arrivals):
    broker = _broker(tmp_path, event_log_max_buffer=3, event_log_batch_size=2)
    for i in range(3):
        broker._enqueue_event_log({"type": "assistant", "i": i})

    async def post(*args, **kwargs):
        assert [e["seq"] for e in kwargs["json"]["entries"]] == [1, 2]
        for i in range(arrivals):
            broker._enqueue_event_log({"type": "assistant", "i": i + 3})
        return _resp()

    client = AsyncMock()
    client.post.side_effect = post
    with patch.object(broker, "_get_http_client", AsyncMock(return_value=client)):
        await broker._flush_event_log()

    # Every unacknowledged seq is either retained or explicitly covered by a gap.
    represented = set()
    for entry in broker._event_log_buffer:
        if entry["kind"] == "log_gap":
            gap = entry["payload"]
            assert gap["first_seq"] == entry["seq"] > 2
            assert gap["dropped"] == gap["last_seq"] - gap["first_seq"] + 1
            represented.update(range(gap["first_seq"], gap["last_seq"] + 1))
        else:
            represented.add(entry["seq"])
    assert represented == set(range(3, 4 + arrivals))


async def test_concurrent_flushes_do_not_delete_each_others_unsent_batches(tmp_path):
    broker = _broker(tmp_path, event_log_batch_size=1)
    for i in range(3):
        broker._enqueue_event_log({"type": "assistant", "i": i})
    entered = asyncio.Event()
    release = asyncio.Event()
    batches = []

    async def post(*args, **kwargs):
        batches.append([e["seq"] for e in kwargs["json"]["entries"]])
        entered.set()
        await release.wait()
        return _resp()

    client = AsyncMock()
    client.post.side_effect = post
    with patch.object(broker, "_get_http_client", AsyncMock(return_value=client)):
        first = asyncio.create_task(broker._flush_event_log())
        await asyncio.wait_for(entered.wait(), 1)
        second = asyncio.create_task(broker._flush_event_log())
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), 1)

    assert batches == [[1], [2]]
    assert [e["seq"] for e in broker._event_log_buffer] == [3]


@pytest.mark.parametrize(
    "failure", [503, ConnectionError("disconnected"), asyncio.CancelledError()]
)
async def test_failed_or_cancelled_post_retains_identical_retry_batch(tmp_path, failure):
    broker = _broker(tmp_path)
    broker._enqueue_event_log({"type": "assistant", "message": "keep me"})
    original = list(broker._event_log_buffer)
    client = AsyncMock()
    if isinstance(failure, int):
        client.post.return_value = _resp(failure)
    else:
        client.post.side_effect = failure
    with patch.object(broker, "_get_http_client", AsyncMock(return_value=client)):
        if isinstance(failure, asyncio.CancelledError):
            with pytest.raises(asyncio.CancelledError):
                await broker._flush_event_log()
        else:
            await broker._flush_event_log()
        assert broker._event_log_buffer == original
        client.post.side_effect = None
        client.post.return_value = _resp()
        await broker._flush_event_log()
    assert client.post.call_args_list[0].kwargs == client.post.call_args_list[1].kwargs
    assert broker._event_log_buffer == []


async def test_resume_rebases_overflow_gap_range_alongside_sequence(tmp_path):
    broker = _broker(tmp_path, event_log_max_buffer=3)
    for i in range(8):
        broker._enqueue_event_log({"type": "assistant", "i": i})
    await broker._resume_seq_from_head(100)
    gap = broker._event_log_buffer[0]
    assert gap["seq"] == gap["payload"]["first_seq"] == 101
    assert gap["payload"]["last_seq"] == 105
    assert gap["payload"]["dropped"] == 5
    assert broker._event_log_seq == 108


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
async def test_unknown_durable_head_prevents_starting_at_sequence_zero(tmp_path, status):
    broker = _broker(tmp_path)
    client = AsyncMock()
    client.get.return_value = _resp(status)
    with patch.object(broker, "_get_http_client", AsyncMock(return_value=client)):
        with pytest.raises(RuntimeError, match="durable event log"):
            await broker._init_event_log()
    assert broker._event_log_task is None


@pytest.mark.parametrize(
    "head", [{}, {"latest_seq": -1}, {"latest_seq": "bad"}, {"latest_seq": True}]
)
async def test_invalid_durable_head_never_reuses_existing_sequence_space(tmp_path, head):
    broker = _broker(tmp_path)
    client = AsyncMock()
    client.get.return_value = _resp(200)
    client.get.return_value.json.return_value = head
    with patch.object(broker, "_get_http_client", AsyncMock(return_value=client)):
        with pytest.raises(RuntimeError, match="durable event log"):
            await broker._init_event_log()
    assert broker._event_log_task is None
