"""INV-8c — capture-window seq collision on durable-log resume (broker tier).

This is the genuine gap that ``test_event_log.py::TestInitResume`` (INV-8a,
resume-from-head) does NOT cover. INV-8a proves that AFTER ``_init_event_log``
seeds the seq counter from the backend head, the NEXT enqueued frame continues
at head+1. It says nothing about frames that were already enqueued into the
broker's buffer DURING the pre-head-fetch *capture window* — the interval while
``_init_event_log`` is awaiting ``GET .../log/head`` and the event loop is free
to run other tasks that push agent output into ``_event_log_buffer``.

The durable log's contract (``pg_session_event_log.append`` /
``InMemoryLog.append``) is ``ON CONFLICT (session_id, seq) DO NOTHING``: a frame
whose ``(session_id, seq)`` already exists in the backend is SILENTLY swallowed.
So INV-8c is: no frame the broker ever emits for THIS run may carry a seq that
collides with the backend's already-stored ``1..K``, and the post-resume stream
must be strictly increasing from ``K+1`` with no duplicate and no gap.

Verification strategy (non-tautological):

  * The expectation is derived INDEPENDENTLY of the broker — a fresh
    ``InMemoryLog`` is pre-seeded with the prior session's ``1..K`` (mirroring the
    real pg contract that motivated resume in the first place), then the broker's
    ENTIRE accumulated buffer is appended through that same idempotent log. We
    assert on what the LOG actually stored (``read_after(0)`` / ``latest_seq``),
    not on the broker's own counter — so a collision shows up as a frame that
    silently never landed, exactly as it would in production.
  * The head fetch uses the ``_resp`` / ``AsyncMock`` style from
    ``test_event_log.py::TestInitResume``, but its ``.get`` blocks on an
    ``asyncio.Event`` so the test can interleave real ``_enqueue_event_log`` calls
    INSIDE the capture window, driving the actual broker code path.

This box leaks SKULD__*/VOLUNDR* env into ``SkuldSettings``; the broker tier's
autouse strip lives in ``tests/test_skuld/conftest.py``, so this file (under
``tests/test_skuld/``) inherits it and need not strip again.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from skuld.broker import Broker
from skuld.config import SkuldSettings
from volundr.domain.models import SessionLogEntry

# Prior session already durably stored seq 1..PRIOR_HEAD; the restarted broker
# must never re-emit anything inside that range.
PRIOR_HEAD = 5
# Frames pushed by the agent DURING the in-flight head fetch (the capture window).
WINDOW_FRAMES = 4
# Frames pushed AFTER _init_event_log has seeded the counter from the head.
POST_RESUME_FRAMES = 3


def _broker(tmp_path) -> Broker:
    settings = SkuldSettings(
        session={"id": "s-capture", "workspace_dir": str(tmp_path)},
        volundr_api_url="http://volundr.test",
    )
    return Broker(settings=settings)


class _InMemoryLog:
    """Idempotent (session_id, seq) log — mirrors pg ON CONFLICT DO NOTHING.

    Identical contract to ``InMemoryLog`` in
    ``tests/test_adapters/test_rest_session_log.py`` and the
    ``InMemory*Repository`` fakes in ``tests/conftest.py``; inlined here because
    the task forbids editing those files, and we need a hand to pre-seed.
    """

    def __init__(self):
        self._rows: dict[tuple, SessionLogEntry] = {}

    async def append(self, entries: list[SessionLogEntry]) -> int:
        for e in entries:
            self._rows.setdefault((e.session_id, e.seq), e)
        return len(entries)

    async def read_after(self, session_id, after_seq=0, limit=1000) -> list[SessionLogEntry]:
        rows = [e for (sid, seq), e in self._rows.items() if sid == session_id and seq > after_seq]
        rows.sort(key=lambda e: e.seq)
        return rows[:limit]

    async def latest_seq(self, session_id) -> int:
        seqs = [seq for (sid, seq) in self._rows if sid == session_id]
        return max(seqs) if seqs else 0


def _buffer_to_entries(broker: Broker, session_id) -> list[SessionLogEntry]:
    """Map the broker's raw buffer to durable-log entries (broker → repo seam)."""
    from datetime import UTC, datetime

    entries: list[SessionLogEntry] = []
    for raw in broker._event_log_buffer:
        entries.append(
            SessionLogEntry(
                session_id=session_id,
                seq=int(raw["seq"]),
                kind=str(raw.get("kind", "unknown")),
                payload=raw.get("payload", {}),
                ts=datetime.now(UTC),
                role=raw.get("role"),
                request_id=raw.get("request_id"),
            )
        )
    return entries


async def _seed_prior_session(log: _InMemoryLog, session_id) -> None:
    """Durably store the prior session's 1..PRIOR_HEAD before the restart."""
    from datetime import UTC, datetime

    await log.append(
        [
            SessionLogEntry(
                session_id=session_id,
                seq=seq,
                kind="assistant",
                payload={"type": "assistant", "prior": seq},
                ts=datetime.now(UTC),
            )
            for seq in range(1, PRIOR_HEAD + 1)
        ]
    )


class TestCaptureWindowResume:
    async def test_window_frames_do_not_collide_with_backend_head(self, tmp_path):
        """INV-8c: a fresh broker resuming at head=K re-sequences capture-window
        frames so NO emitted seq lands in 1..K and the stream is gapless from K+1.
        """
        session_id = uuid4()
        log = _InMemoryLog()
        await _seed_prior_session(log, session_id)
        assert await log.latest_seq(session_id) == PRIOR_HEAD

        broker = _broker(tmp_path)

        # The head fetch blocks until we release it — this is the capture window.
        head_released = asyncio.Event()

        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.json.return_value = {"latest_seq": PRIOR_HEAD}

        async def _slow_get(*_args, **_kwargs):
            await head_released.wait()
            return head_resp

        client = AsyncMock()
        client.get.side_effect = _slow_get

        with patch.object(broker, "_get_http_client", AsyncMock(return_value=client)):
            init_task = asyncio.create_task(broker._init_event_log())

            # --- inside the capture window: agent output arrives before the head
            #     fetch returns. Drive the REAL enqueue path.
            await asyncio.sleep(0)  # let _init_event_log reach the awaiting GET
            for i in range(WINDOW_FRAMES):
                broker._enqueue_event_log({"type": "assistant", "message": {"window": i}})

            # --- close the window: head fetch returns K, counter resumes.
            head_released.set()
            await init_task

            # --- post-resume frames continue the stream.
            for i in range(POST_RESUME_FRAMES):
                broker._enqueue_event_log({"type": "content_block_delta", "delta": {"post": i}})

        await broker._stop_event_log()

        # Independently verify against the durable log: append the broker's WHOLE
        # buffer through the same idempotent (session_id, seq) contract that the
        # backend uses, then read back what actually landed.
        entries = _buffer_to_entries(broker, session_id)
        broker_seqs = [e.seq for e in entries]
        await log.append(entries)

        stored = await log.read_after(session_id, after_seq=0)
        stored_seqs = [e.seq for e in stored]

        # INV-8c core: not one broker-emitted seq may fall inside the backend's
        # already-stored 1..PRIOR_HEAD — otherwise ON CONFLICT silently eats it.
        prior_range = set(range(1, PRIOR_HEAD + 1))
        collisions = [s for s in broker_seqs if s in prior_range]
        assert collisions == [], (
            "capture-window frames collided with the backend head "
            f"{prior_range}; colliding seqs={collisions}. The window frames are "
            "silently swallowed by ON CONFLICT DO NOTHING."
        )

        # Every frame the broker produced for THIS run actually landed (none lost
        # to a conflict): stored count == prior + broker count, all distinct.
        assert len(broker_seqs) == len(set(broker_seqs))  # no internal dup
        assert len(stored_seqs) == PRIOR_HEAD + len(broker_seqs)

        # The post-resume slice (everything the broker emitted) is strictly
        # increasing, starts at PRIOR_HEAD+1, and is gapless with no duplicate.
        new_seqs = sorted(s for s in stored_seqs if s > PRIOR_HEAD)
        assert new_seqs == broker_seqs == sorted(broker_seqs)
        assert new_seqs[0] == PRIOR_HEAD + 1
        assert new_seqs == list(range(PRIOR_HEAD + 1, PRIOR_HEAD + 1 + len(new_seqs)))

        # Full durable stream is gapless 1..N — the prior session AND the resumed
        # run form one unbroken sequence with no overlap and no hole.
        total = PRIOR_HEAD + WINDOW_FRAMES + POST_RESUME_FRAMES
        assert stored_seqs == list(range(1, total + 1))
        assert await log.latest_seq(session_id) == total

    async def test_capture_window_frames_survive_and_are_ordered(self, tmp_path):
        """The window frames themselves must survive the resume (not be dropped)
        and stay ahead of the post-resume frames in emission order."""
        session_id = uuid4()
        log = _InMemoryLog()
        await _seed_prior_session(log, session_id)

        broker = _broker(tmp_path)
        head_released = asyncio.Event()
        head_resp = MagicMock()
        head_resp.status_code = 200
        head_resp.json.return_value = {"latest_seq": PRIOR_HEAD}

        async def _slow_get(*_args, **_kwargs):
            await head_released.wait()
            return head_resp

        client = AsyncMock()
        client.get.side_effect = _slow_get

        with patch.object(broker, "_get_http_client", AsyncMock(return_value=client)):
            init_task = asyncio.create_task(broker._init_event_log())
            await asyncio.sleep(0)
            for i in range(WINDOW_FRAMES):
                broker._enqueue_event_log({"type": "assistant", "message": {"window": i}})
            head_released.set()
            await init_task
            for i in range(POST_RESUME_FRAMES):
                broker._enqueue_event_log({"type": "content_block_delta", "delta": {"post": i}})

        await broker._stop_event_log()

        await log.append(_buffer_to_entries(broker, session_id))
        stored = await log.read_after(session_id, after_seq=PRIOR_HEAD)

        # All window + post-resume frames are present and in emission order: the
        # WINDOW_FRAMES assistant frames precede the POST_RESUME content deltas.
        kinds = [e.kind for e in stored]
        assert kinds == ["assistant"] * WINDOW_FRAMES + ["content_block_delta"] * POST_RESUME_FRAMES

        # The very first window payload is preserved verbatim (no truncation), and
        # it sits at PRIOR_HEAD+1 — directly after the resumed head.
        assert stored[0].seq == PRIOR_HEAD + 1
        assert stored[0].payload == {"type": "assistant", "message": {"window": 0}}
