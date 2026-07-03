"""FAULT-C durable-count cache: the cheap gate that skips the ~4s durable rebuild on the
conversation read path while the event log hasn't grown (profiled fix).

Locks the new mechanism at the unit level: the seq-keyed count cache helper + the cheap
``latest_event_seq`` freshness signal. The live-path integration (cache hit skips the rebuild)
is covered by the existing FAULT-C fallback tests + measured end-to-end.
"""

from uuid import uuid4

import pytest

from volundr.adapters.inbound import rest as rest_mod
from volundr.domain.services.session_archive import SessionArchiveService


def test_durable_count_cache_put_stores_updates_and_bounds():
    cache = rest_mod._DURABLE_COUNT_CACHE
    cache.clear()
    rest_mod._durable_count_cache_put("s1", 10, 42)
    assert cache["s1"] == (10, 42)
    # Same key updates in place (new seq + count).
    rest_mod._durable_count_cache_put("s1", 11, 43)
    assert cache["s1"] == (11, 43)
    # Bounded: filling well past the cap must never exceed it.
    for i in range(rest_mod._DURABLE_COUNT_CACHE_MAX + 50):
        rest_mod._durable_count_cache_put(f"k{i}", i, i)
    assert len(cache) <= rest_mod._DURABLE_COUNT_CACHE_MAX
    cache.clear()


class _SeqRepo:
    def __init__(self, seq: int) -> None:
        self._seq = seq

    async def latest_seq(self, session_id):  # noqa: ANN001
        return self._seq


@pytest.mark.asyncio
async def test_latest_event_seq_returns_repo_max():
    svc = SessionArchiveService(None, None, None, event_log_repository=_SeqRepo(7))
    assert await svc.latest_event_seq(uuid4()) == 7


@pytest.mark.asyncio
async def test_latest_event_seq_is_zero_without_repo():
    svc = SessionArchiveService(None, None, None, event_log_repository=None)
    assert await svc.latest_event_seq(uuid4()) == 0


@pytest.mark.asyncio
async def test_latest_event_seq_swallows_errors():
    class _Boom:
        async def latest_seq(self, session_id):  # noqa: ANN001
            raise RuntimeError("db down")

    svc = SessionArchiveService(None, None, None, event_log_repository=_Boom())
    # A freshness probe must never fail the conversation read — it degrades to 0 (always-rebuild).
    assert await svc.latest_event_seq(uuid4()) == 0
