"""Durable transcript rebuilds: single flight per session, shallow results cached per seq."""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from volundr.adapters.inbound.durable_transcripts import DurableTranscripts


def _transcript(text: str = "x" * 50_000) -> dict:
    return {
        "turns": [
            {"id": "t1", "role": "user", "content": "hi", "parts": []},
            {
                "id": "t2",
                "role": "assistant",
                "parts": [
                    {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}},
                    {"type": "tool_result", "tool_use_id": "toolu_1", "content": text},
                ],
            },
        ]
    }


class FakeSource:
    """Counts rebuilds; each one can be held open to test overlap."""

    def __init__(self, seq: int = 7) -> None:
        self.seq = seq
        self.rebuilds = 0
        self.running = 0
        self.max_running = 0
        self.gate: asyncio.Event | None = None

    async def durable_latest_seq(self, session_id: UUID) -> int:
        return self.seq

    async def get_transcript(self, session_id: UUID) -> dict:
        self.rebuilds += 1
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            if self.gate is not None:
                await self.gate.wait()
            return _transcript()
        finally:
            self.running -= 1


async def test_shallow_is_elided_and_rebuilt_once_per_seq():
    source, cache, sid = FakeSource(), DurableTranscripts(), uuid4()

    first = await cache.shallow(source, sid)
    again = await cache.shallow(source, sid)

    assert source.rebuilds == 1
    assert again is first
    result = first["turns"][1]["parts"][1]
    assert "x" * 50_000 not in str(result), "the cached copy must be the elided one"


async def test_a_new_event_rebuilds():
    source, cache, sid = FakeSource(), DurableTranscripts(), uuid4()
    await cache.shallow(source, sid)

    source.seq = 8
    await cache.shallow(source, sid)

    assert source.rebuilds == 2


async def test_no_event_log_is_never_cached():
    source, cache, sid = FakeSource(seq=0), DurableTranscripts(), uuid4()

    await cache.shallow(source, sid)
    await cache.shallow(source, sid)

    assert source.rebuilds == 2


async def test_concurrent_shallow_reads_share_one_rebuild():
    source, cache, sid = FakeSource(), DurableTranscripts(), uuid4()
    source.gate = asyncio.Event()

    reads = [asyncio.create_task(cache.shallow(source, sid)) for _ in range(4)]
    await asyncio.sleep(0)
    source.gate.set()
    results = await asyncio.gather(*reads)

    assert source.rebuilds == 1
    assert all(r is results[0] for r in results)


async def test_full_reads_never_overlap_and_are_not_cached():
    source, cache, sid = FakeSource(), DurableTranscripts(), uuid4()
    source.gate = asyncio.Event()

    reads = [asyncio.create_task(cache.full(source, sid)) for _ in range(3)]
    await asyncio.sleep(0)
    source.gate.set()
    results = await asyncio.gather(*reads)

    assert source.rebuilds == 3
    assert source.max_running == 1
    assert "x" * 50_000 in str(results[0]), "full reads keep the tool_result content"


async def test_different_sessions_rebuild_in_parallel():
    source, cache = FakeSource(), DurableTranscripts()
    source.gate = asyncio.Event()

    reads = [asyncio.create_task(cache.full(source, uuid4())) for _ in range(2)]
    await asyncio.sleep(0)
    source.gate.set()
    await asyncio.gather(*reads)

    assert source.max_running == 2


async def test_least_recently_used_sessions_are_dropped():
    source, cache = FakeSource(), DurableTranscripts(max_sessions=2)
    a, b, c = uuid4(), uuid4(), uuid4()

    await cache.shallow(source, a)
    await cache.shallow(source, b)
    await cache.shallow(source, a)  # a is now the most recent
    await cache.shallow(source, c)  # drops b
    rebuilds = source.rebuilds
    await cache.shallow(source, a)
    await cache.shallow(source, b)

    assert source.rebuilds == rebuilds + 1
