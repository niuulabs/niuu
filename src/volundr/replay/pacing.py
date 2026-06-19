"""Pacing for replay-as-live: turn recorded ts deltas into sleep intervals.

``frame_delay`` is a PURE function (no I/O, no clock) so it is exhaustively
unit-testable. ``drive_replay`` is the small async driver that paces emission;
its ``sleep`` and ``emit`` callables are injected so tests can drive it with a
controllable clock and capture what was written.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from volundr.domain.models import SessionLogEntry


@dataclass(frozen=True)
class PacingConfig:
    """How recorded ts deltas are translated into wall-clock sleeps.

    ``speed`` is a timeline divisor (``2.0`` => twice as fast; ``0.5`` => slow
    motion). ``max_gap_seconds`` caps the *recorded wall gap* and is applied
    BEFORE the speed division, so a long idle stretch never stalls the stream
    regardless of ``speed``.
    """

    speed: float = 1.0
    max_gap_seconds: float = 2.0


def frame_delay(
    prev_ts: datetime | None,
    cur_ts: datetime | None,
    cfg: PacingConfig,
) -> float:
    """Seconds to sleep BEFORE emitting the frame recorded at ``cur_ts``.

    PURE — no I/O, no clock. Rules, applied in order:

    * ``prev_ts is None`` (first frame) OR ``cur_ts is None`` -> ``0.0``
    * ``raw_gap = (cur_ts - prev_ts).total_seconds()``
    * ``raw_gap <= 0`` (equal or out-of-order ts) -> ``0.0`` (``seq`` is the
      true order; never sleep on a non-positive gap)
    * clamp ``raw_gap`` to ``max_gap_seconds``, THEN divide by ``speed``
      (defensively floored to ``1.0`` if ``speed <= 0``)
    """
    if prev_ts is None or cur_ts is None:
        return 0.0
    raw = (cur_ts - prev_ts).total_seconds()
    if raw <= 0.0:
        return 0.0
    capped = min(raw, cfg.max_gap_seconds)
    speed = cfg.speed if cfg.speed > 0 else 1.0
    return capped / speed


async def drive_replay(
    entries: AsyncIterator[SessionLogEntry],
    *,
    cfg: PacingConfig,
    emit: Callable[[SessionLogEntry], Awaitable[bool]],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    """Pace and emit recorded frames; return the number of frames written.

    ``emit`` does the visibility gating + wire send and reports whether it
    actually wrote a frame (a hidden frame returns ``False``). ``sleep`` is
    injected so tests can pass a zeroed/recording clock.

    ``prev_ts`` advances on EVERY source frame — even ones ``emit`` drops — so
    the spacing *between visible frames* matches the recorded wall-clock gap
    (pacing follows the recorded timeline, not the emitted one).

    ``asyncio.CancelledError`` (client disconnect cancels the driving task)
    propagates out so the caller can tear down; the ``async for`` closes the
    source iterator (and its DB paging) on the way out.
    """
    prev_ts: datetime | None = None
    count = 0
    async for entry in entries:
        delay = frame_delay(prev_ts, entry.ts, cfg)
        prev_ts = entry.ts
        if delay > 0:
            await sleep(delay)
        if await emit(entry):
            count += 1
    return count


def encode_frame(payload: dict) -> str:
    """Encode one frame for the wire: compact JSON, no trailing newline.

    Parity with skuld's ``WebSocketChannel.send_event`` (one JSON object per
    WebSocket message, no NDJSON newline). The client tolerates both forms.
    """
    return json.dumps(payload, separators=(",", ":"))
