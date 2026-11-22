"""The shallow durable transcript is folded, not rebuilt (forge-hosts OOM, 2026-11-22).

Every poll of a session without a reachable broker rebuilt up to 50k frames, and after a
Forge restart every session polled at once. The fold must return exactly what the whole
rebuild would, elided, while reading the raw frames of the open turn only.
"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest

from niuu.cap_telemetry import cap_hit_counts, reset_cap_hits
from niuu.domain.conversation_timeline import TIMELINE_KEY, observation
from niuu.domain.text_projection import REPAIR_KIND, repair_legacy_turn, repair_marker
from niuu.domain.transcript_rebuild import rebuild_turns
from niuu.domain.transcript_reducer import assistant_turn_id, reduce_frames
from skuld.conversation_shallow import elide_turn, elide_turns
from tests.test_domain.test_session_archive_service import InMemorySessionEventLog
from tests.test_volundr.test_conversation_db_fallback import (
    _CONV_PATH,
    _build_client,
    _running_session,
)
from volundr.domain.models import Session, SessionLogEntry, SessionStatus
from volundr.domain.services import SessionArchiveService
from volundr.domain.services import session_archive as archive_mod
from volundr.domain.services.shallow_transcript import REBUILD_KINDS

_NOW = datetime(2026, 11, 22, 18, 22, tzinfo=UTC)
_KNOB = "conversation_durable_fold_max_pending"
_HEAVY = "x" * 5000


@pytest.fixture(autouse=True)
def _clean_counters():
    reset_cap_hits()
    yield
    reset_cap_hits()


class _NoArchive:
    def load_transcript(self, **_kwargs):
        return None


class _Service(SessionArchiveService):
    async def _workspace_dir_candidates(self, session_id):
        return None, []


class _CountingLog(InMemorySessionEventLog):
    """Counts the rows handed out, by the query that asked for them."""

    def __init__(self, entries=None):
        super().__init__(entries)
        self.read: list[SessionLogEntry] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def read_after(self, session_id, after_seq=0, limit=1000):
        page = await super().read_after(session_id, after_seq=after_seq, limit=limit)
        self.read += page
        return page

    async def read_after_kinds(self, session_id, after_seq=0, limit=1000, kinds=frozenset()):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0)
        page = [
            row
            for row in self._entries
            if row.session_id == session_id and row.seq > after_seq and row.kind in kinds
        ][:limit]
        self.in_flight -= 1
        self.read += page
        return page


def _service(event_log, **kwargs) -> SessionArchiveService:
    return _Service(None, None, _NoArchive(), event_log_repository=event_log, **kwargs)


class _Frames:
    """Builds one session's log."""

    def __init__(self, sid=None):
        self.sid = sid or uuid4()
        self.rows: list[SessionLogEntry] = []

    @property
    def seq(self) -> int:
        return len(self.rows)

    def add(self, kind: str, payload: dict, **extra) -> SessionLogEntry:
        row = SessionLogEntry(
            session_id=self.sid, seq=self.seq + 1, kind=kind, payload=payload, ts=_NOW, **extra
        )
        self.rows.append(row)
        return row

    def turn(self, turn: dict, **extra) -> None:
        self.add("conversation.turn", {"type": "conversation.turn", "turn": turn}, **extra)

    def user(self, uid: str, *, timed: bool = False) -> None:
        stamp = {TIMELINE_KEY: self._stamp()} if timed else {}
        self.add("user", {"uuid": uid, "message": {"content": f"ask {uid}"}, **stamp})
        self.turn(
            {
                "id": uid,
                "role": "user",
                "content": f"ask {uid}",
                "parts": [],
                "metadata": dict(stamp),
            }
        )

    def work(self, call: str, *, timed: bool = False) -> None:
        stamp = {TIMELINE_KEY: self._stamp()} if timed else {}
        self.add(
            "assistant",
            {
                "message": {
                    "content": [
                        {"type": "text", "text": f"step {call}"},
                        {
                            "type": "tool_use",
                            "id": call,
                            "name": "Bash",
                            "input": {"command": _HEAVY},
                        },
                    ]
                },
                **stamp,
            },
        )
        self.add("claude_hook", {"hook_event_name": "PostToolUse", "payload": {"x": _HEAVY}})
        self.add(
            "user",
            {
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": call, "content": _HEAVY}]
                },
                **({TIMELINE_KEY: self._stamp()} if timed else {}),
            },
        )

    def settle(self, start: int) -> None:
        """What the broker does when a turn ends: the result frame, then its turn row."""
        self.add("result", {"subtype": "success", "result": ""})
        turns = reduce_frames(self.rows[start:]).turns
        for turn in [t for t in turns if t["role"] == "assistant"]:
            self.turn(turn)

    def exchange(self, n: int, *, timed: bool = False, calls: int = 2) -> None:
        start = self.seq
        self.user(f"U{n}", timed=timed)
        for i in range(calls):
            self.work(f"call-{n}-{i}", timed=timed)
        self.settle(start)

    def _stamp(self) -> dict:
        seq = self.seq + 1
        return observation(seq, f"2026-11-22T18:{seq // 60 % 60:02d}:{seq % 60:02d}+00:00")


def _expected(rows) -> list[dict]:
    return elide_turns(rebuild_turns(rows).turns)


async def _shallow(service, sid) -> list[dict]:
    payload = await service._load_shallow_transcript(sid, elide_turn)
    return payload["turns"] if payload else []


def _plain() -> _Frames:
    log = _Frames()
    for n in range(4):
        log.exchange(n)
    return log


def _open_turn() -> _Frames:
    log = _plain()
    log.user("U-open")
    log.work("call-open")
    log.add("content_block_delta", {"delta": {"type": "text_delta", "text": "still going"}})
    return log


def _timed_steering() -> _Frames:
    """Same-turn input splits the assistant rows around it; later ACKs stamp delivery."""
    log = _Frames()
    log.exchange(0, timed=True)
    start = log.seq
    log.user("U1", timed=True)
    log.work("call-a", timed=True)
    log.add("user_confirmed", {"id": "steer", "content": _HEAVY, "steering_state": "pending"})
    log.user("steer", timed=True)
    log.work("call-b", timed=True)
    log.settle(start)
    log.exchange(2, timed=True)
    log.add("user_active", {"id": "steer", "accepted_at": "2026-11-22T18:30:00+00:00"})
    log.add("user_delivery_failed", {"id": "U2"})
    return log


def _raw_only() -> _Frames:
    log = _Frames()
    log.add("user", {"uuid": "U1", "message": {"content": "go"}})
    log.work("call-1")
    log.add("result", {"subtype": "success", "result": "done"})
    log.add("user", {"uuid": "U2", "message": {"content": "again"}})
    log.work("call-2")
    return log


def _imported() -> _Frames:
    log = _Frames()
    log.add("system", {"subtype": "init"})
    log.add(
        "assistant",
        {
            "content": [{"type": "text", "text": "Recovered"}],
            "message": {"content": [{"type": "text", "text": "Recovered"}]},
            "metadata": {"native_import": {"source": "codex"}},
        },
    )
    log.work("call-native")
    log.add("result", {"type": "result", "metadata": {"native_import": {"source": "codex"}}})
    log.add("history_import", {"type": "history_import", "source_id": "s"})
    log.exchange(1)
    log.work("call-tail")
    return log


def _duplicates_and_gaps() -> _Frames:
    log = _plain()
    log.add("log_gap", {"missing": 3})
    log.turn(dict(log.rows[-2].payload["turn"]))  # a retried row with the same turn id
    log.turn({"id": "note", "role": "system", "content": "joined", "parts": []})
    log.add("conversation.turn", {"type": "conversation.turn"})  # no turn at all
    log.exchange(9)
    log.add("error", {"message": "boom"})
    return log


_LOGS = [_plain, _open_turn, _timed_steering, _raw_only, _imported, _duplicates_and_gaps]


@pytest.mark.parametrize("build", _LOGS)
@pytest.mark.parametrize("page", [1, 3, 50])
async def test_the_fold_is_the_whole_rebuild_elided(build, page):
    log = build()
    service = _service(InMemorySessionEventLog(log.rows), turn_page_size=page)

    assert await _shallow(service, log.sid) == _expected(log.rows)


@pytest.mark.parametrize("build", _LOGS)
async def test_every_refresh_is_the_whole_rebuild_elided(build):
    """The log grows frame by frame under a poller, as a running session's does."""
    log = build()
    event_log = InMemorySessionEventLog([])
    service = _service(event_log, turn_page_size=2)

    for upto, row in enumerate(log.rows, start=1):
        await event_log.append([row])
        assert await _shallow(service, log.sid) == _expected(log.rows[:upto]), upto


@pytest.mark.parametrize("seed", range(40))
async def test_random_logs_fold_to_the_whole_rebuild(seed):
    rng = random.Random(seed)
    log = _Frames()
    for n in range(rng.randint(1, 8)):
        step = rng.choice(["exchange", "timed", "work", "user", "ack", "note", "terminal", "error"])
        if step in ("exchange", "timed"):
            log.exchange(n, timed=step == "timed", calls=rng.randint(1, 3))
        elif step == "work":
            log.work(f"loose-{n}", timed=rng.random() < 0.5)
        elif step == "user":
            log.user(f"loose-user-{n}", timed=rng.random() < 0.5)
        elif step == "ack":
            log.add("user_active", {"id": f"U{rng.randint(0, 8)}"})
        elif step == "note":
            log.turn({"id": f"note-{n}", "role": "assistant", "content": "room post", "parts": []})
        elif step == "terminal":
            log.add("terminal_frame", {"rows": [f"pane {n}"]})
        else:
            log.add("error", {"message": "boom"})
    event_log = InMemorySessionEventLog([])
    service = _service(event_log, turn_page_size=rng.randint(1, 4))

    cut = rng.randint(1, len(log.rows))
    await event_log.append(log.rows[:cut])
    assert await _shallow(service, log.sid) == _expected(log.rows[:cut])
    await event_log.append(log.rows[cut:])
    assert await _shallow(service, log.sid) == _expected(log.rows)


@pytest.mark.parametrize("build", _LOGS)
def test_frames_of_other_kinds_never_change_the_rebuild(build):
    rows = build().rows

    kept = [row for row in rows if row.kind in REBUILD_KINDS]

    assert len(kept) < len(rows)
    assert rebuild_turns(kept).turns == rebuild_turns(rows).turns


async def test_the_fold_decodes_turn_rows_and_the_open_turn_only():
    log = _open_turn()
    event_log = _CountingLog(log.rows)
    service = _service(event_log)

    await _shallow(service, log.sid)

    boundary = max(
        row.seq
        for row in log.rows
        if row.kind == "conversation.turn" and row.payload["turn"]["role"] == "assistant"
    )
    settled = {row.kind for row in event_log.read if row.seq <= boundary}
    assert settled == {"conversation.turn", "result"}


async def test_a_refresh_reads_only_the_frames_after_the_last_settled_turn():
    log = _open_turn()
    event_log = _CountingLog(log.rows)
    service = _service(event_log)
    await _shallow(service, log.sid)

    event_log.read.clear()
    await event_log.append(
        [log.add("content_block_delta", {"delta": {"type": "text_delta", "text": " more"}})]
    )
    assert await _shallow(service, log.sid) == _expected(log.rows)

    open_turn = [row for row in log.rows if row.seq > log.seq - 9]
    assert len(event_log.read) <= len(open_turn)
    assert all(row.seq > log.seq - 9 for row in event_log.read)


async def test_a_boundaryless_log_is_scanned_for_settled_rows_once():
    log = _raw_only()
    event_log = _CountingLog(log.rows)
    service = _service(event_log)
    await _shallow(service, log.sid)

    event_log.read.clear()
    await event_log.append([log.add("user_active", {"id": "U2"})])
    assert await _shallow(service, log.sid) == _expected(log.rows)

    # One settled-row read (the new ACK) and the open tail; no second scan from seq 0.
    assert [row.seq for row in event_log.read].count(log.seq) == 2


async def test_no_heavy_content_is_kept_between_reads():
    log = _open_turn()
    service = _service(InMemorySessionEventLog(log.rows))
    await _shallow(service, log.sid)

    fold = service._shallow_cache[str(log.sid)]
    assert _HEAVY not in repr(fold._rows)
    assert _HEAVY not in repr(fold._pending)
    assert all(row.kind != "result" for row in fold._rows)


async def test_a_log_past_the_frame_ceiling_still_folds_whole():
    """The whole rebuild keeps the newest 50k frames; the fold needs no such cut."""
    log = _plain()
    service = _service(InMemorySessionEventLog(log.rows), turn_tail_max_pending=5)

    assert await _shallow(service, log.sid) == _expected(log.rows)
    assert cap_hit_counts() == {}


async def test_an_open_turn_past_the_pending_limit_is_loud_and_counted_once():
    log = _raw_only()
    service = _service(InMemorySessionEventLog(log.rows), turn_tail_max_pending=4)

    turns = await _shallow(service, log.sid)
    await _shallow(service, log.sid)

    assert turns == _expected(log.rows[-4:])
    assert cap_hit_counts() == {_KNOB: 1}


async def test_rows_appended_during_a_read_belong_to_the_next_one():
    log = _plain()
    grown = len(log.rows)
    log.exchange(9)
    event_log = InMemorySessionEventLog(log.rows)
    service = _service(event_log)

    async def early_head(_sid):
        return grown

    event_log.latest_seq = early_head
    assert await _shallow(service, log.sid) == _expected(log.rows[:grown])

    del event_log.latest_seq
    assert await _shallow(service, log.sid) == _expected(log.rows)


def _legacy_with_native_frames() -> _Frames:
    """An aggregate-only turn whose native frames prove its text boundaries."""
    log = _Frames()
    native = {"thread_id": "thread", "turn_id": "native"}
    log.add("content_block_start", {"index": 0, "content_block": {"type": "text"}, **native})
    log.add("content_block_delta", {"delta": {"text": "Checking.\n"}, **native})
    log.add("content_block_stop", {"index": 0, **native})
    log.add(
        "assistant",
        {
            "message": {
                "content": [{"type": "tool_use", "id": "c", "name": "exec", "input": {"x": _HEAVY}}]
            },
            **native,
        },
    )
    log.add("content_block_start", {"index": 0, "content_block": {"type": "text"}, **native})
    log.add("content_block_delta", {"delta": {"text": "Done."}, **native})
    log.add("content_block_stop", {"index": 0, **native})
    log.add("result", {"stop_reason": "end_turn", **native})
    [candidate] = reduce_frames(log.rows).turns
    assert candidate["id"] == assistant_turn_id(str(log.sid), log.seq)
    log.turn(
        {
            **candidate,
            "content": "Checking.\nDone.",
            "parts": [part for part in candidate["parts"] if part["type"] != "text"],
            "metadata": {},
        }
    )
    return log


async def test_turns_repaired_from_native_frames_use_the_whole_log():
    log = _legacy_with_native_frames()
    service = _service(InMemorySessionEventLog(log.rows))

    turns = elide_turns(await _shallow(service, log.sid))

    assert [part["type"] for part in turns[0]["parts"]].count("text") == 2
    assert turns == _expected(log.rows)


async def test_turns_with_a_stored_repair_use_the_whole_log():
    log = _legacy_with_native_frames()
    original = log.rows[-1].payload["turn"]
    [candidate] = reduce_frames(log.rows[:-1]).turns
    repaired = repair_legacy_turn(original, candidate, source_head=log.seq)
    stored = _Frames(log.sid)
    for _ in range(log.seq - 1):
        stored.add("system", {})
    stored.turn(original)
    stored.add(REPAIR_KIND, repair_marker([repaired]))
    service = _service(InMemorySessionEventLog(stored.rows))

    turns = elide_turns(await _shallow(service, log.sid))

    assert [part["type"] for part in turns[0]["parts"]].count("text") == 2
    assert turns == _expected(stored.rows)


async def test_legacy_turns_without_native_frames_stay_folded(monkeypatch):
    log = _Frames()
    log.turn({"id": "post", "role": "assistant", "content": "room post", "parts": []})
    log.exchange(1)
    service = _service(InMemorySessionEventLog(log.rows))

    async def no_rebuild(*_args, **_kwargs):
        raise AssertionError("nothing to repair: the whole log must not be read")

    monkeypatch.setattr(service, "_load_event_log_transcript", no_rebuild)
    assert await _shallow(service, log.sid) == _expected(log.rows)


async def test_sessions_that_ask_together_decode_their_logs_one_at_a_time():
    logs = [_open_turn() for _ in range(6)]
    event_log = _CountingLog([row for log in logs for row in log.rows])
    service = _service(event_log)

    results = await asyncio.gather(*(_shallow(service, log.sid) for log in logs))

    assert event_log.max_in_flight == 1
    assert results == [_expected(log.rows) for log in logs]


async def test_the_concurrency_is_configurable():
    logs = [_open_turn() for _ in range(6)]
    event_log = _CountingLog([row for log in logs for row in log.rows])
    service = _service(event_log, rebuild_concurrency=3)

    await asyncio.gather(*(_shallow(service, log.sid) for log in logs))

    assert event_log.max_in_flight == 3


async def test_least_recently_read_sessions_are_folded_again():
    logs = [_plain() for _ in range(3)]
    event_log = InMemorySessionEventLog([row for log in logs for row in log.rows])
    service = _service(event_log, shallow_sessions=2)

    for log in logs:
        await _shallow(service, log.sid)

    assert list(service._shallow_cache) == [str(log.sid) for log in logs[1:]]
    assert await _shallow(service, logs[0].sid) == _expected(logs[0].rows)


async def test_a_read_that_fails_midway_does_not_leave_its_fold_behind(monkeypatch):
    log = _plain()
    event_log = InMemorySessionEventLog(log.rows)
    service = _service(event_log)
    await _shallow(service, log.sid)
    await event_log.append([log.add("user_active", {"id": "U1"})])

    def broken(*_args, **_kwargs):
        raise RuntimeError("fold failed")

    with monkeypatch.context() as patch:
        patch.setattr(archive_mod.ShallowTranscriptFold, "turns", broken)
        with pytest.raises(RuntimeError, match="fold failed"):
            await _shallow(service, log.sid)
    assert str(log.sid) not in service._shallow_cache

    assert await _shallow(service, log.sid) == _expected(log.rows)


async def test_a_session_without_durable_turns_has_no_shallow_transcript():
    sid = uuid4()
    assert await _service(None)._load_shallow_transcript(sid, elide_turn) is None
    empty = _service(InMemorySessionEventLog([]))
    assert await empty._load_shallow_transcript(sid, elide_turn) is None
    chrome = _Frames(sid)
    chrome.add("system", {"subtype": "init"})
    only_chrome = _service(InMemorySessionEventLog(chrome.rows))
    assert await only_chrome._load_shallow_transcript(sid, elide_turn) is None


async def test_the_public_read_resolves_the_event_log():
    log = _plain()
    service = _service(InMemorySessionEventLog(log.rows))

    payload = await service.get_shallow_transcript(log.sid, elide_turn=elide_turn)

    assert payload["turns"] == _expected(log.rows)
    assert payload["projection_revision"]
    assert payload["is_active"] is False


async def test_the_turn_count_fold_reads_turn_rows_then_the_open_turn():
    log = _open_turn()
    event_log = _CountingLog(log.rows)
    service = _service(event_log)

    summary = await service.durable_turn_tail(log.sid)

    full = rebuild_turns(log.rows).turns
    assert (summary.turns, summary.complete) == (len(full), True)
    boundary = max(
        row.seq
        for row in log.rows
        if row.kind == "conversation.turn" and row.payload["turn"]["role"] == "assistant"
    )
    assert {row.kind for row in event_log.read if row.seq <= boundary} == {"conversation.turn"}


async def test_the_turn_count_fold_settles_an_import_from_its_raw_frames():
    log = _imported()
    marker = next(row.seq for row in log.rows if row.kind == "history_import")
    rows = log.rows[: marker + 1]
    service = _service(_CountingLog(rows), turn_page_size=1)

    summary = await service.durable_turn_tail(log.sid)

    assert summary.turns == len(rebuild_turns(rows).turns)


# ── the conversation route while the broker does not answer ───────────


async def _unreachable_session(event_log, monkeypatch):
    client, repository = _build_client(
        Session(name="x", model="m", status=SessionStatus.STOPPED), event_log
    )
    session = await _running_session(repository)
    broker = AsyncMock()
    broker.get.side_effect = httpx.ConnectError("broker not back yet")
    client_cls = MagicMock()
    client_cls.return_value.__aenter__.return_value = broker
    monkeypatch.setattr("volundr.adapters.inbound.rest.httpx.AsyncClient", client_cls)

    async def no_rebuild(*_args, **_kwargs):
        raise AssertionError("a paged read must not rebuild the whole log")

    monkeypatch.setattr(SessionArchiveService, "_rebuild_event_log", no_rebuild)
    return client, session


async def test_a_shallow_poll_is_paged_from_the_fold(monkeypatch):
    event_log = InMemorySessionEventLog([])
    client, session = await _unreachable_session(event_log, monkeypatch)
    log = _Frames(session.id)
    for n in range(4):
        log.exchange(n)
    await event_log.append(log.rows)
    expected = _expected(log.rows)
    url = _CONV_PATH.format(sid=session.id)

    newest = client.get(url, params={"detail": "shallow", "limit": 1}).json()
    earlier = client.get(url, params={"detail": "shallow", "limit": 2, "before": 1}).json()
    after = client.get(url, params={"detail": "shallow", "after": 5}).json()

    assert newest["turns"] == expected[-1:]
    assert (newest["total_turns"], newest["window_offset"]) == (8, 7)
    assert earlier["turns"] == expected[-3:-1]
    assert after["turns"] == expected[6:]


async def test_a_protocol_2_page_comes_from_the_fold_whatever_its_detail(monkeypatch):
    """The page elides its turns anyway, so it never needs the full transcript."""
    event_log = InMemorySessionEventLog([])
    client, session = await _unreachable_session(event_log, monkeypatch)
    log = _Frames(session.id)
    for n in range(4):
        log.exchange(n)
    await event_log.append(log.rows)
    expected = _expected(log.rows)
    url = _CONV_PATH.format(sid=session.id)

    page = client.get(url, params={"history_protocol": 2, "limit": 3}).json()
    older = client.get(
        url, params={"history_protocol": 2, "limit": 3, "cursor": page["older_cursor"]}
    ).json()

    assert page["turns"] == expected[-3:]
    assert older["turns"] == expected[-6:-3]
