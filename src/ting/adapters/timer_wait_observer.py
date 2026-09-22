"""Timer wait observer: satisfied once a resolved absolute deadline has passed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ting.domain.workflow_execution import WorkflowExecution
from ting.domain.workflow_wait import WaitObservation, WaitObservationStatus, WorkflowWait
from ting.ports.workflow_wait import WaitConditionObserver


class TimerWaitObserver(WaitConditionObserver):
    """Waits for a plain elapsed-time condition; the only state it reads is the clock.

    A request names either an absolute ``until`` (an RFC 3339 timestamp) or a
    relative ``after_seconds``. ``validate`` resolves the relative form to an
    absolute ``until`` in place, so the persisted request always carries a
    stable deadline that never shifts between polls.
    """

    def __init__(self, *, poll_interval_seconds: float = 30.0) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("timer wait poll_interval_seconds must be positive")
        self._poll_interval_seconds = poll_interval_seconds

    @property
    def condition_type(self) -> str:
        return "timer"

    def validate(self, request: dict[str, Any], execution: WorkflowExecution) -> None:
        del execution
        _resolve_deadline(request)

    async def observe(self, wait: WorkflowWait, execution: WorkflowExecution) -> WaitObservation:
        del execution
        deadline = _resolve_deadline(dict(wait.request))
        now = datetime.now(UTC)
        if now >= deadline:
            return WaitObservation(
                status=WaitObservationStatus.SATISFIED,
                observed_at=now,
                reason="The configured deadline has passed",
                detail={"until": deadline.isoformat()},
            )
        return WaitObservation(
            status=WaitObservationStatus.PENDING,
            observed_at=now,
            reason="The configured deadline has not passed",
            detail={"until": deadline.isoformat()},
            retry_after_seconds=min(self._poll_interval_seconds, (deadline - now).total_seconds()),
        )


def _resolve_deadline(request: dict[str, Any]) -> datetime:
    until_raw = request.get("until")
    after_seconds = request.get("after_seconds")
    if (until_raw is None) == (after_seconds is None):
        raise ValueError("timer wait request must set exactly one of until or after_seconds")
    if until_raw is not None:
        if not isinstance(until_raw, str):
            raise ValueError("timer wait until must be an RFC 3339 timestamp string")
        try:
            until = datetime.fromisoformat(until_raw)
        except ValueError as exc:
            raise ValueError("timer wait until must be an RFC 3339 timestamp") from exc
        if until.tzinfo is None:
            raise ValueError("timer wait until must include a timezone")
        request["until"] = until.isoformat()
        request.pop("after_seconds", None)
        return until
    if not isinstance(after_seconds, int) or isinstance(after_seconds, bool) or after_seconds < 0:
        raise ValueError("timer wait after_seconds must be a non-negative integer")
    resolved = datetime.now(UTC) + timedelta(seconds=after_seconds)
    request.pop("after_seconds", None)
    request["until"] = resolved.isoformat()
    return resolved
