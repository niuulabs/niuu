"""FAULT-C durable turn count: folded incrementally, never a rebuild per poll.

forge-hosts (2026-11-21) was OOMKilled within ~90 s of every start: each conversation
poll of a running session whose event log had grown started a background rebuild of up to
50k frames, and several long sessions rebuilt in parallel. The desync check needs only the
durable turn count and the last settled turn id, which ``TurnTailFold`` keeps by reading
the frames after the last settled boundary.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from niuu.cap_telemetry import cap_hit_counts, degraded_counts, reset_cap_hits
from niuu.domain.transcript_rebuild import rebuild_turns
from tests.test_volundr.test_attachments_index import (
    _NOW,
    InMemorySessionEventLog,
    _frames,
    _NoopStorage,
    _turn_row,
)
from tests.test_volundr.test_conversation_db_fallback import (
    _CONV_PATH,
    _build_client,
    _longer_crash_frames,
    _patch_live_pod,
    _running_session,
    _strip_ambient_env,  # noqa: F401 - autouse fixture, hermetic env
)
from volundr.adapters.inbound import rest as rest_mod
from volundr.domain.models import Session, SessionLogEntry, SessionStatus
from volundr.domain.services import SessionArchiveService
from volundr.domain.services.durable_turn_tail import (
    DurableTurnTail,
    TurnTailFold,
    last_settled_turn_id,
)

_KNOB = "conversation_durable_fold_max_pending"
_WARM_PATH = "volundr.conversation.fault_c_warm"


@pytest.fixture(autouse=True)
def _clean_fault_c_state():
    reset_cap_hits()
    rest_mod._DURABLE_COUNT_CACHE.clear()
    rest_mod._FAULT_C_CLEARED.clear()
    rest_mod._DURABLE_WARMING.clear()
    yield
    reset_cap_hits()
    rest_mod._DURABLE_COUNT_CACHE.clear()
    rest_mod._FAULT_C_CLEARED.clear()
    rest_mod._DURABLE_WARMING.clear()


def _service(event_log, **kwargs) -> SessionArchiveService:
    return SessionArchiveService(
        None, _NoopStorage(), None, event_log_repository=event_log, **kwargs
    )


def _raw_assistant(sid, seq: int) -> SessionLogEntry:
    return SessionLogEntry(
        session_id=sid,
        seq=seq,
        kind="assistant",
        payload={"message": {"content": [{"type": "text", "text": f"step {seq}"}]}},
        ts=_NOW,
        role="assistant",
    )


def _recording(event_log) -> list[int]:
    read_from: list[int] = []
    original = event_log.read_after

    async def recording_read_after(session_id, after_seq=0, limit=1000):
        read_from.append(after_seq)
        return await original(session_id, after_seq=after_seq, limit=limit)

    event_log.read_after = recording_read_after
    return read_from


@pytest.mark.asyncio
@pytest.mark.parametrize("frames", [_frames, _longer_crash_frames])
async def test_fold_matches_the_full_rebuild(frames):
    sid = uuid4()
    rows = frames(sid)
    service = _service(InMemorySessionEventLog(rows), event_log_page_size=1)

    summary = await service.durable_turn_tail(sid)

    full = rebuild_turns(rows).turns
    assert summary == DurableTurnTail(
        seq=rows[-1].seq,
        turns=len(full),
        tail_id=last_settled_turn_id(full),
        complete=True,
    )


@pytest.mark.asyncio
async def test_refresh_reads_only_the_frames_after_the_last_settled_turn():
    sid = uuid4()
    event_log = InMemorySessionEventLog(_frames(sid))
    service = _service(event_log)
    await service.durable_turn_tail(sid)

    read_from = _recording(event_log)
    # Same head: answered from the cache, no read at all.
    await service.durable_turn_tail(sid)
    assert read_from == []

    late = _turn_row(sid, 8, {"id": "t-late", "role": "assistant", "content": "done"})
    await event_log.append([late])
    summary = await service.durable_turn_tail(sid)

    # Seq 5 is the last assistant conversation.turn: nothing before it is read again.
    assert read_from[0] == 5
    full = rebuild_turns(await event_log.read_after(sid, after_seq=0, limit=100)).turns
    assert (summary.seq, summary.turns, summary.tail_id) == (8, len(full), "t-late")


@pytest.mark.asyncio
async def test_a_boundaryless_log_past_the_limit_is_loud_and_rereads_only_the_window():
    sid = uuid4()
    event_log = InMemorySessionEventLog([_raw_assistant(sid, seq) for seq in range(1, 6)])
    service = _service(event_log, event_log_page_size=2, turn_tail_max_pending=3)

    summary = await service.durable_turn_tail(sid)

    assert summary is not None and summary.complete is False
    assert cap_hit_counts() == {_KNOB: 1}

    read_from = _recording(event_log)
    await event_log.append([_raw_assistant(sid, 6)])
    again = await service.durable_turn_tail(sid)

    assert again is not None and again.seq == 6 and again.complete is False
    # Only the newest `max_pending` frames are read again, not the whole log.
    assert read_from[0] == 3
    # The same truncated fold is reported once, not on every poll.
    assert cap_hit_counts() == {_KNOB: 1}


@pytest.mark.asyncio
async def test_no_event_log_or_empty_log_has_no_tail():
    assert await _service(None).durable_turn_tail(uuid4()) is None
    assert await _service(InMemorySessionEventLog([])).durable_turn_tail(uuid4()) is None


@pytest.mark.asyncio
async def test_fold_cache_is_lru_bounded():
    event_log = InMemorySessionEventLog([])
    service = _service(event_log, turn_tail_sessions=2)
    sids = [uuid4() for _ in range(3)]
    for sid in sids:
        await event_log.append(_frames(sid))
        await service.durable_turn_tail(sid)

    assert list(service._turn_tail_cache) == [str(sids[1]), str(sids[2])]
    assert str(sids[0]) not in service._turn_tail_locks


def test_settled_tail_survives_a_window_without_turns():
    fold = TurnTailFold(max_pending=10)
    sid = uuid4()
    fold.settle([_turn_row(sid, 1, {"id": "a1", "role": "assistant", "content": "x"})])

    summary = fold.summary([], seq=1)

    assert (summary.turns, summary.tail_id) == (1, "a1")


@pytest.mark.asyncio
async def test_warm_folds_instead_of_rebuilding():
    forge = MagicMock()

    async def durable_turn_tail(_sid):
        return DurableTurnTail(seq=9, turns=7, tail_id="t7", complete=True)

    async def get_transcript(*_args, **_kwargs):
        raise AssertionError("the warm must not rebuild the transcript")

    forge.durable_turn_tail = durable_turn_tail
    forge.get_transcript = get_transcript
    sid = uuid4()
    rest_mod._DURABLE_WARMING.add(str(sid))

    await rest_mod._warm_durable_count(forge, sid, 9, str(sid))

    assert rest_mod._DURABLE_COUNT_CACHE[str(sid)] == (9, 7, "t7")
    assert str(sid) not in rest_mod._DURABLE_WARMING


@pytest.mark.asyncio
async def test_a_failing_warm_is_counted_not_swallowed():
    forge = MagicMock()

    async def durable_turn_tail(_sid):
        raise RuntimeError("event log unreachable")

    forge.durable_turn_tail = durable_turn_tail
    sid = uuid4()
    rest_mod._DURABLE_WARMING.add(str(sid))

    await rest_mod._warm_durable_count(forge, sid, 9, str(sid))

    assert degraded_counts() == {_WARM_PATH: 1}
    assert str(sid) not in rest_mod._DURABLE_COUNT_CACHE
    assert str(sid) not in rest_mod._DURABLE_WARMING


@pytest.mark.asyncio
async def test_a_tail_pair_the_rebuild_cleared_is_not_rebuilt_again(monkeypatch):
    event_log = InMemorySessionEventLog([])
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)
    await event_log.append(_longer_crash_frames(session.id))
    durable_turns = rebuild_turns(await event_log.read_after(session.id, after_seq=0)).turns
    sid = str(session.id)

    # The broker is caught up: it ends on the rebuild's own last turn, in fewer rows.
    live_body = {
        "turns": [
            {"id": "live-u", "role": "user", "content": "summarize the repo"},
            {"id": durable_turns[-1]["id"], "role": "assistant", "content": "merged"},
        ],
        "is_active": False,
        "last_activity": "",
    }
    rebuilds = 0
    original = SessionArchiveService.get_transcript

    async def counting_get_transcript(self, session_id, **kwargs):
        nonlocal rebuilds
        rebuilds += 1
        return await original(self, session_id, **kwargs)

    monkeypatch.setattr(SessionArchiveService, "get_transcript", counting_get_transcript)

    def poll() -> dict:
        _, mock_client = _patch_live_pod(live_body)
        with monkeypatch.context() as m:
            client_cls = MagicMock()
            client_cls.return_value.__aenter__.return_value = mock_client
            m.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)
            resp = client.get(_CONV_PATH.format(sid=session.id))
        assert resp.status_code == 200
        return resp.json()

    # The fold disagrees with the live tail (more turns, another tail id): the exact rebuild
    # is asked once, finds the same tail as the live body, and the live body is served.
    seq = await event_log.latest_seq(session.id)
    rest_mod._DURABLE_COUNT_CACHE[sid] = (seq, 10, "fold-tail")
    assert poll()["turns"] == live_body["turns"]
    assert rebuilds == 1

    # The log grows (a running session) and the fold reports the same pair again:
    # served live without another rebuild.
    await event_log.append([_raw_assistant(session.id, seq + 1)])
    rest_mod._DURABLE_COUNT_CACHE[sid] = (seq + 1, 10, "fold-tail")
    assert poll()["turns"] == live_body["turns"]
    assert rebuilds == 1
