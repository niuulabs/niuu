"""Subscribe to canonical Ravn help-needed events and surface them in Ting."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirSubscriber, Subscription
from ting.domain.models import SessionMessage
from ting.ports.event_bus import EventBusPort, TingEvent
from ting.ports.tracker import TrackerFactory, TrackerPort

logger = logging.getLogger(__name__)

HELP_NEEDED = "help_needed"
RAVN_HELP_NEEDED = "ravn.help.needed"


class RavnHelpNeededHandler:
    """Persist help-needed requests as Ting session messages and notifications."""

    def __init__(
        self,
        *,
        subscriber: SleipnirSubscriber,
        tracker_factory: TrackerFactory,
        event_bus: EventBusPort,
        owner_id: str,
    ) -> None:
        self._subscriber = subscriber
        self._tracker_factory = tracker_factory
        self._event_bus = event_bus
        self._owner_id = owner_id
        self._subscription: Subscription | None = None
        self._pending_tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        if self._subscription is not None:
            return
        self._subscription = await self._subscriber.subscribe(
            [HELP_NEEDED, RAVN_HELP_NEEDED],
            self._handle_event,
        )
        logger.info("RavnHelpNeededHandler started")

    async def stop(self) -> None:
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None
        for task in list(self._pending_tasks):
            task.cancel()
        if self._pending_tasks:
            await asyncio.gather(*self._pending_tasks, return_exceptions=True)
        self._pending_tasks.clear()
        logger.info("RavnHelpNeededHandler stopped")

    async def _handle_event(self, event: SleipnirEvent) -> None:
        task = asyncio.create_task(self._process_event(event), name=f"ravn-help:{event.event_id}")
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _process_event(self, event: SleipnirEvent) -> None:
        resolved = await self._resolve_run(event)
        if resolved is None:
            logger.warning(
                "RavnHelpNeededHandler: no run for session=%s correlation=%s",
                event.payload.get("session_id") or event.payload.get("ravn_session_id") or "-",
                event.correlation_id or "-",
            )
            return

        tracker, run = resolved
        saga = await tracker.get_saga_for_run(run.tracker_id)
        payload = _help_payload(event)
        if run.session_id:
            payload["session_id"] = run.session_id

        serialized = json.dumps(payload, default=str, sort_keys=True)
        messages = await tracker.get_session_messages(run.tracker_id)
        if _is_duplicate_open_request(messages, serialized):
            return

        now = datetime.now(UTC)
        await tracker.save_session_message(
            SessionMessage(
                id=uuid4(),
                run_id=run.id,
                session_id=run.session_id or payload.get("session_id") or "",
                content=serialized,
                sender="help_needed",
                created_at=now,
            )
        )

        await self._event_bus.emit(
            TingEvent(
                event="run.feedback_requested",
                owner_id=self._owner_id,
                data={
                    "owner_id": self._owner_id,
                    "run_id": str(run.id),
                    "run_name": run.name,
                    "tracker_id": run.tracker_id,
                    "session_id": run.session_id or payload.get("session_id") or "",
                    "saga_id": str(saga.id) if saga is not None else "",
                    "saga_name": saga.name if saga is not None else "",
                    "summary": payload.get("summary", ""),
                    "reason": payload.get("reason", ""),
                    "recommendation": payload.get("recommendation", ""),
                    "ui_path": f"/ting/sagas/{saga.id}" if saga is not None else "",
                },
            )
        )

    async def _resolve_run(self, event: SleipnirEvent) -> tuple[TrackerPort, object] | None:
        trackers = await self._tracker_factory.for_owner(self._owner_id)
        if not trackers:
            return None

        payload = event.payload if isinstance(event.payload, dict) else {}
        explicit_run_id = str(payload.get("run_id") or "").strip()
        session_candidates = [
            str(payload.get("session_id") or payload.get("ravn_session_id") or "").strip(),
            str(event.correlation_id or "").strip(),
        ]

        for tracker in trackers:
            if explicit_run_id:
                run = await _resolve_run_by_id(tracker, explicit_run_id)
                if run is not None:
                    return tracker, run

            for session_id in session_candidates:
                if not session_id:
                    continue
                run = await tracker.get_run_by_session(session_id)
                if run is not None:
                    return tracker, run

        return None


async def _resolve_run_by_id(tracker: TrackerPort, run_id: str):
    try:
        parsed = UUID(run_id)
    except ValueError:
        parsed = None

    if parsed is not None:
        run = await tracker.get_run_by_id(parsed)
        if run is not None:
            return run

    try:
        return await tracker.get_run(run_id)
    except Exception:
        logger.debug("RavnHelpNeededHandler: tracker lookup failed for %s", run_id, exc_info=True)
        return None


def _help_payload(event: SleipnirEvent) -> dict[str, object]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    attempted = payload.get("attempted")
    if not isinstance(attempted, list):
        attempted = []
    context = payload.get("context")
    if not isinstance(context, dict):
        context = {}
    return {
        "summary": str(payload.get("summary") or "Agent requested human feedback."),
        "reason": str(payload.get("reason") or "needs_context"),
        "attempted": [str(item) for item in attempted if str(item).strip()],
        "recommendation": str(payload.get("recommendation") or ""),
        "context": context,
        "persona": str(payload.get("persona") or ""),
        "target_peer_id": _peer_id_from_source(event.source),
        "session_id": str(payload.get("session_id") or payload.get("ravn_session_id") or ""),
    }


def _peer_id_from_source(source: str) -> str:
    if source.startswith("ravn:"):
        return source.removeprefix("ravn:")
    return source


def _is_duplicate_open_request(messages: list[SessionMessage], serialized_payload: str) -> bool:
    latest_help = next(
        (message for message in reversed(messages) if message.sender == "help_needed"),
        None,
    )
    if latest_help is None or latest_help.content != serialized_payload:
        return False
    latest_user = next(
        (message for message in reversed(messages) if message.sender == "user"),
        None,
    )
    if latest_user is None:
        return True
    return latest_user.created_at <= latest_help.created_at
