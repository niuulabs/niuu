"""Central ODIN review queue service — where every human decision converges.

Residents and courts file ``odin.review.requested`` events; this service
keeps them durably, lets the operator decide, publishes the one
``odin.review.decided`` command, and closes the loop when the resident
confirms with ``odin.review.resolved``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ravn.odin.review import REVIEW_DECISIONS, ReviewItem, ReviewStatus
from ravn.ports.review_queue import ReviewQueueStore
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent

logger = logging.getLogger(__name__)

_INGESTED_EVENT_TYPES = frozenset(
    {
        registry.ODIN_REVIEW_REQUESTED,
        registry.ODIN_REVIEW_DECIDED,
        registry.ODIN_REVIEW_RESOLVED,
    }
)

#: Statuses an incoming re-announce must never overwrite.
_SETTLED_STATUSES = frozenset(
    {
        ReviewStatus.APPROVED.value,
        ReviewStatus.REJECTED.value,
        ReviewStatus.APPLIED.value,
        ReviewStatus.APPLY_FAILED.value,
        ReviewStatus.EXPIRED.value,
    }
)


class ReviewDecisionPublisher(Protocol):
    """The command channel review decisions ride back to residents on."""

    async def publish_review_decision(
        self,
        item: ReviewItem,
    ) -> tuple[dict[str, Any], SleipnirEvent | None]:
        pass


class OdinReviewService:
    """Durable review inbox plus the decide → publish → resolve loop."""

    def __init__(
        self,
        store: ReviewQueueStore,
        *,
        publisher: ReviewDecisionPublisher | None = None,
        ttl_seconds_by_kind: dict[str, float] | None = None,
        default_ttl_seconds: float = 0.0,
    ) -> None:
        self._store = store
        self._publisher = publisher
        self._ttl_seconds_by_kind = dict(ttl_seconds_by_kind or {})
        self._default_ttl_seconds = default_ttl_seconds

    @property
    def store(self) -> ReviewQueueStore:
        return self._store

    async def ingest_event(self, event: SleipnirEvent | dict[str, Any]) -> bool:
        """Route one bus/telemetry event into the queue; True when consumed."""
        event_type, payload = _event_parts(event)
        if event_type not in _INGESTED_EVENT_TYPES:
            return False
        try:
            item = ReviewItem.from_payload(payload)
        except (ValueError, TypeError) as exc:
            logger.warning("odin review: ignoring malformed %s: %s", event_type, exc)
            return False
        if event_type == registry.ODIN_REVIEW_REQUESTED:
            await self._ingest_requested(item)
            return True
        # Decided events (operator-initiated commands included) and resolved
        # confirmations both carry the authoritative item state.
        await self._ingest_settled(item, event_type)
        return True

    async def _ingest_requested(self, item: ReviewItem) -> None:
        existing = await self._store.get(item.item_id)
        if existing is not None and existing.status in _SETTLED_STATUSES:
            # A resident re-announced an item the operator already settled.
            return
        await self._store.upsert(item)

    async def _ingest_settled(self, item: ReviewItem, event_type: str) -> None:
        existing = await self._store.get(item.item_id)
        if existing is not None:
            # Keep the richer original evidence; adopt only what the event
            # actually carries — the decision, or the resident's resolution.
            if item.status != ReviewStatus.PENDING.value:
                existing.status = item.status
            if event_type == registry.ODIN_REVIEW_DECIDED or item.decided_by:
                existing.decided_by = item.decided_by
                existing.decided_at = item.decided_at
                existing.decision_reason = item.decision_reason
            if event_type == registry.ODIN_REVIEW_RESOLVED:
                existing.apply_outcome = item.apply_outcome
                existing.apply_detail = item.apply_detail
                existing.resolved_at = item.resolved_at
                if item.apply_outcome == "applied" and existing.status == (
                    ReviewStatus.APPROVED.value
                ):
                    existing.status = ReviewStatus.APPLIED.value
                elif item.apply_outcome == "apply_failed":
                    existing.status = ReviewStatus.APPLY_FAILED.value
            item = existing
        await self._store.upsert(item)

    async def get(self, item_id: str) -> ReviewItem | None:
        return await self._store.get(item_id)

    async def list_items(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        environment_id: str | None = None,
        limit: int | None = None,
    ) -> list[ReviewItem]:
        return await self._store.list_items(
            status=status,
            kind=kind,
            environment_id=environment_id,
            limit=limit,
        )

    async def counts(self) -> dict[str, int]:
        return await self._store.counts()

    async def decide(
        self,
        item_id: str,
        *,
        decision: str,
        operator_id: str,
        reason: str = "",
    ) -> tuple[ReviewItem, dict[str, Any]]:
        """Apply an operator decision and publish it to the residents."""
        if decision not in REVIEW_DECISIONS:
            raise ReviewDecisionError(f"unknown decision {decision!r}", status_code=422)
        item = await self._store.get(item_id)
        if item is None:
            raise ReviewDecisionError(f"unknown review item: {item_id}", status_code=404)
        if not item.is_pending:
            raise ReviewDecisionError(
                f"review item {item_id} is already {item.status}",
                status_code=409,
            )
        if decision == ReviewStatus.REJECTED.value and not reason.strip():
            raise ReviewDecisionError(
                "a reason is required when rejecting a review item",
                status_code=422,
            )
        item.decide(decision=decision, operator_id=operator_id, reason=reason)
        await self._store.upsert(item)
        delivery: dict[str, Any] = {"published": False, "message": "no publisher configured"}
        if self._publisher is not None:
            delivery, _event = await self._publisher.publish_review_decision(item)
        if not delivery.get("published"):
            logger.warning(
                "odin review: decision for %s recorded but not delivered: %s",
                item_id,
                delivery.get("message"),
            )
        return item, delivery

    async def record_decided(self, item: ReviewItem) -> ReviewItem:
        """Persist an operator-initiated, already-decided item (one ledger)."""
        await self._ingest_settled(item, registry.ODIN_REVIEW_DECIDED)
        return item

    async def sweep_expired(self, *, now: datetime | None = None) -> int:
        """Expire pending items older than their kind's TTL; returns the count."""
        current = now or datetime.now(UTC)
        expired = 0
        for item in await self._store.list_items(status=ReviewStatus.PENDING.value):
            ttl = self._ttl_seconds_by_kind.get(item.kind, self._default_ttl_seconds)
            if ttl <= 0:
                continue
            try:
                requested_at = datetime.fromisoformat(item.requested_at)
            except ValueError:
                continue
            if current - requested_at < timedelta(seconds=ttl):
                continue
            item.status = ReviewStatus.EXPIRED.value
            item.resolved_at = current.isoformat()
            item.apply_detail = f"expired after {int(ttl)}s without an operator decision"
            await self._store.upsert(item)
            expired += 1
        if expired:
            logger.info("odin review: expired %d pending item(s)", expired)
        return expired


class ReviewDecisionError(Exception):
    """A decide() call the API layer should surface with an HTTP status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _event_parts(event: SleipnirEvent | dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if isinstance(event, SleipnirEvent):
        return event.event_type, dict(event.payload)
    event_type = str(event.get("event_type") or event.get("eventType") or "")
    payload = event.get("payload")
    return event_type, dict(payload) if isinstance(payload, dict) else {}
