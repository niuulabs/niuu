"""CaptureChannel — accumulates all events for a task into a retrievable result.

Used by DriveLoop in place of SilentChannel when cascade is enabled.
Writes to a shared TaskResultStore keyed by task_id so that task_collect
and task_status(include_progress=True) can return real output and event history.

Sleipnir streaming extension path (NIU-545, future work):
    SleipnirChannel (NIU-438) already tags every RavnEvent with task_id in the
    SleipnirEnvelope, so when a subtask runs on a daemon with Sleipnir enabled
    every THOUGHT/TOOL_START/TOOL_RESULT/RESPONSE event is already landing on
    ``ravn.events`` with the task_id in the payload — no publishing changes needed.

    The coordinator's daemon can subscribe to ``ravn.events.#``, filter by task_id,
    and pipe matching events into TaskResultStore directly — giving true streaming
    rather than polling.  This is ~50 lines and does not change the TaskResultStore
    or CaptureChannel design at all.

    By transport:
    - Pi mode / nng only: Sleipnir unavailable — polling via
      task_status(include_progress=True) on 2s intervals is correct (NIU-545).
    - Infra mode / Sleipnir up: events flow through ravn.events.  Coordinator
      subscribes and filters by task_id for streaming.  TaskResultStore still
      stores completed output so task_collect can retrieve it after the event
      stream has been consumed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from ravn.adapters.channels.silent import _SURFACE_PREFIX
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.ports.channel import ChannelPort

logger = logging.getLogger(__name__)

# Default store capacity — completed results stay queryable without unbounded growth
_DEFAULT_STORE_CAPACITY = 200


@dataclass
class CapturedEvent:
    """A single event captured during task execution."""

    type: str
    summary: str  # human-readable one-liner
    timestamp: datetime
    details: dict[str, object] | None = None


@dataclass
class TaskResult:
    """Full result record for a single task execution."""

    task_id: str
    status: Literal["running", "complete", "failed", "cancelled"]
    output: str  # final RESPONSE text; empty while running
    events: list[CapturedEvent]
    triggered_by: str
    started_at: datetime
    completed_at: datetime | None


class TaskResultStore:
    """Bounded in-memory store of task_id → TaskResult.

    All callers run on a single asyncio event loop so no locking is needed.
    Evicts the oldest entry when capacity is reached so memory usage stays bounded.

    Capacity defaults to ``_DEFAULT_STORE_CAPACITY`` (200 entries).
    """

    def __init__(self, capacity: int = _DEFAULT_STORE_CAPACITY) -> None:
        self._capacity = capacity
        self._store: dict[str, TaskResult] = {}
        self._insertion_order: list[str] = []

    def start(self, task_id: str, triggered_by: str) -> None:
        """Register a new running task.  Evicts oldest if at capacity."""
        result = TaskResult(
            task_id=task_id,
            status="running",
            output="",
            events=[],
            triggered_by=triggered_by,
            started_at=datetime.now(UTC),
            completed_at=None,
        )
        if task_id in self._store:
            self._store[task_id] = result
            return

        if len(self._store) >= self._capacity:
            oldest = self._insertion_order.pop(0)
            self._store.pop(oldest, None)
            logger.debug("task_result_store: evicted oldest entry %r", oldest)

        self._store[task_id] = result
        self._insertion_order.append(task_id)

    def append_event(self, task_id: str, event: CapturedEvent) -> None:
        """Add a captured event to the task's event list."""
        result = self._store.get(task_id)
        if result is None:
            return
        result.events.append(event)

    def set_output(self, task_id: str, output: str) -> None:
        """Set the final output text and mark the task complete."""
        result = self._store.get(task_id)
        if result is None:
            return
        result.output = output
        result.status = "complete"
        result.completed_at = datetime.now(UTC)

    def set_status(
        self, task_id: str, status: Literal["running", "complete", "failed", "cancelled"]
    ) -> None:
        """Update the task status (e.g. failed, cancelled)."""
        result = self._store.get(task_id)
        if result is None:
            return
        result.status = status
        if status in ("complete", "failed", "cancelled"):
            result.completed_at = datetime.now(UTC)

    def get(self, task_id: str) -> TaskResult | None:
        """Return the TaskResult for task_id, or None if not found."""
        return self._store.get(task_id)

    def active_ids(self) -> list[str]:
        """Return task IDs currently marked as running."""
        return [tid for tid, r in self._store.items() if r.status == "running"]

    def results(self) -> tuple[TaskResult, ...]:
        """Return the bounded results in insertion order for factual projections."""
        return tuple(
            result
            for task_id in self._insertion_order
            if (result := self._store.get(task_id)) is not None
        )


def _summarise_event(event: RavnEvent) -> str:
    """Produce a human-readable one-liner for each event type."""
    match event.type:
        case RavnEventType.THOUGHT:
            text = event.payload.get("text", "")
            return f"thinking: {text[:80]}"
        case RavnEventType.TOOL_START:
            tool_name = event.payload.get("tool_name", "")
            tool_input = event.payload.get("input", {})
            args_summary = ", ".join(
                f"{k}={str(v)[:20]!r}" for k, v in list(tool_input.items())[:3]
            )
            return f"tool: {tool_name}({args_summary})"
        case RavnEventType.TOOL_RESULT:
            result = event.payload.get("result", "")
            return f"→ {result[:80]}"
        case RavnEventType.RESPONSE:
            text = event.payload.get("text", "")
            return f"response: {text[:120]}"
        case RavnEventType.ERROR:
            message = event.payload.get("message", "")
            return f"error: {message}"
        case _:
            return f"{event.type}"
    raise AssertionError("Unreachable _summarise_event fallthrough")


def _capture_details(event: RavnEvent) -> dict[str, object] | None:
    """Retain bounded structured tool facts for live runtime projections."""
    if event.type == RavnEventType.TOOL_START:
        tool_input = event.payload.get("input")
        return {
            "tool_name": str(event.payload.get("tool_name") or ""),
            "input": _bounded_mapping(tool_input) if isinstance(tool_input, dict) else {},
        }
    if event.type == RavnEventType.TOOL_RESULT:
        result = str(event.payload.get("result") or "")
        try:
            parsed: object = json.loads(result)
        except (TypeError, ValueError):
            parsed = result[:8_000]
        if isinstance(parsed, dict):
            parsed = _bounded_mapping(parsed)
        elif not isinstance(parsed, str):
            parsed = str(parsed)[:8_000]
        return {
            "tool_name": str(event.payload.get("tool_name") or ""),
            "result": parsed,
            "is_error": bool(event.payload.get("is_error")),
        }
    return None


def _bounded_mapping(value: dict[object, object]) -> dict[str, object]:
    """Keep identifying tool inputs without retaining unbounded prompts."""
    bounded: dict[str, object] = {}
    for key, item in list(value.items())[:16]:
        name = str(key)[:128]
        if isinstance(item, str):
            bounded[name] = item[:2_000]
        elif item is None or isinstance(item, bool | int | float):
            bounded[name] = item
        else:
            rendered = json.dumps(item, default=str, sort_keys=True)
            bounded[name] = rendered[:2_000]
    return bounded


class CaptureChannel(ChannelPort):
    """Accumulates all events for a task into a retrievable result.

    Used by DriveLoop in place of SilentChannel when cascade is enabled.
    Writes to a shared TaskResultStore keyed by task_id.

    Preserves the SilentChannel contract:
    - ``surface_triggered`` property detects ``[SURFACE]`` prefix on RESPONSE
    - ``response_text`` property holds the final response text
    """

    def __init__(self, task_id: str, store: TaskResultStore) -> None:
        self._task_id = task_id
        self._store = store
        self._response_text: str = ""
        self._surface_triggered: bool = False

    async def emit(self, event: RavnEvent) -> None:
        """Accumulate event into the store; update output/status on terminal events."""
        summary = _summarise_event(event)
        captured = CapturedEvent(
            type=str(event.type),
            summary=summary,
            timestamp=event.timestamp,
            details=_capture_details(event),
        )
        self._store.append_event(self._task_id, captured)

        if event.type == RavnEventType.RESPONSE:
            text = event.payload.get("text", "")
            self._response_text = text
            self._surface_triggered = text.startswith(_SURFACE_PREFIX)
            self._store.set_output(self._task_id, text)

        if event.type == RavnEventType.ERROR:
            self._store.set_status(self._task_id, "failed")

    @property
    def surface_triggered(self) -> bool:
        """True if the response started with ``[SURFACE]`` — same as SilentChannel."""
        return self._surface_triggered

    @property
    def response_text(self) -> str:
        """The final response text — same contract as SilentChannel."""
        return self._response_text
