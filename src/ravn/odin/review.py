"""Unified ODIN review contract — one envelope for every human decision.

Anything in the system that wants a human verdict files a :class:`ReviewItem`
and publishes ``odin.review.requested``. The operator decides centrally and
the platform publishes ``odin.review.decided``; the target resident applies
the decision through one dispatcher and confirms with ``odin.review.resolved``.

The ``kind`` field is the only thing that varies between producers:

* ``evolution_build``  — a self-built skill+tool held for install approval
* ``skill_promotion``  — a proven private skill held for scope promotion
* ``flock_learning``   — a peer learning held for adoption approval
* ``court_escalation`` — an ODIN court draft-for-review action
* ``autonomy_change``  — an operator-initiated autonomy mode change

Operator-initiated commands ride the same envelope as auto-decided items so
every human intervention lands in one auditable ledger.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher

logger = logging.getLogger(__name__)


class ReviewKind(StrEnum):
    """What is being reviewed; the only axis of variation in the contract."""

    EVOLUTION_BUILD = "evolution_build"
    SKILL_PROMOTION = "skill_promotion"
    FLOCK_LEARNING = "flock_learning"
    COURT_ESCALATION = "court_escalation"
    AUTONOMY_CHANGE = "autonomy_change"


class ReviewStatus(StrEnum):
    """Lifecycle of a review item from request to resident confirmation."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    APPLIED = "applied"
    APPLY_FAILED = "apply_failed"


#: Operator decisions; everything else in ReviewStatus is machine-driven.
REVIEW_DECISIONS = frozenset({ReviewStatus.APPROVED.value, ReviewStatus.REJECTED.value})

#: Capability an operator must hold to decide each kind of item.
REVIEW_CAPABILITIES: dict[str, str] = {
    ReviewKind.AUTONOMY_CHANGE.value: "change_autonomy",
}
DEFAULT_REVIEW_CAPABILITY = "approve"


def capability_for_kind(kind: str) -> str:
    return REVIEW_CAPABILITIES.get(kind, DEFAULT_REVIEW_CAPABILITY)


@dataclass
class ReviewItem:
    """One reviewable thing: who asks, what for, with what evidence."""

    item_id: str
    kind: str
    requested_action: str
    environment_id: str
    valkyrie_id: str
    title: str
    summary: str
    #: Who applies the decision: one valkyrie, every resident of an
    #: environment, or every member of a flock (relevance-filtered).
    audience: str = "valkyrie"
    flock_id: str = ""
    domain: str = ""
    risk_class: str = "low"
    safety_class: str = "read_only"
    urgency: float = 0.5
    requested_capability: str = DEFAULT_REVIEW_CAPABILITY
    #: Producers set this so re-dreams and restarts never file duplicates.
    dedupe_key: str = ""
    #: Lineage for humans plus the full apply payload for the resident.
    evidence: dict[str, Any] = field(default_factory=dict)
    status: str = ReviewStatus.PENDING.value
    requested_by: str = ""
    requested_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    decided_by: str = ""
    decided_at: str = ""
    decision_reason: str = ""
    resolved_at: str = ""
    apply_outcome: str = ""
    apply_detail: str = ""
    correlation_id: str = ""
    causation_id: str = ""

    @classmethod
    def new(
        cls,
        *,
        kind: str,
        requested_action: str,
        environment_id: str,
        valkyrie_id: str,
        title: str,
        summary: str,
        **kwargs: Any,
    ) -> ReviewItem:
        return cls(
            item_id=f"review:{kind}:{uuid4().hex[:12]}",
            kind=kind,
            requested_action=requested_action,
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            title=title,
            summary=summary,
            requested_capability=capability_for_kind(kind),
            **kwargs,
        )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ReviewItem:
        known = {f.name for f in fields(cls)}
        data = {key: value for key, value in payload.items() if key in known}
        for required in ("item_id", "kind", "environment_id"):
            if not str(data.get(required) or "").strip():
                raise ValueError(f"review item payload is missing {required!r}")
        data.setdefault("requested_action", "")
        data.setdefault("valkyrie_id", "")
        data.setdefault("title", data["item_id"])
        data.setdefault("summary", "")
        item = cls(**data)
        item.evidence = dict(item.evidence or {})
        return item

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_pending(self) -> bool:
        return self.status == ReviewStatus.PENDING.value

    @property
    def is_approved(self) -> bool:
        return self.status in {ReviewStatus.APPROVED.value, ReviewStatus.APPLIED.value}

    def decide(self, *, decision: str, operator_id: str, reason: str = "") -> None:
        if decision not in REVIEW_DECISIONS:
            raise ValueError(f"unknown review decision: {decision!r}")
        self.status = decision
        self.decided_by = operator_id
        self.decided_at = datetime.now(UTC).isoformat()
        self.decision_reason = reason

    def resolve(self, *, outcome: str, detail: str = "") -> None:
        self.apply_outcome = outcome
        self.apply_detail = detail
        self.resolved_at = datetime.now(UTC).isoformat()
        if outcome == "applied" and self.status == ReviewStatus.APPROVED.value:
            self.status = ReviewStatus.APPLIED.value
        elif outcome == "apply_failed":
            self.status = ReviewStatus.APPLY_FAILED.value


def item_targets(
    item: ReviewItem,
    *,
    valkyrie_id: str,
    environment_id: str,
    flock_ids: list[str],
) -> bool:
    """True when a resident with this identity should apply the decision."""
    if item.audience == "environment":
        return item.environment_id == environment_id
    if item.audience == "flock":
        if not item.flock_id:
            return True
        return _normalise_flock(item.flock_id) in {_normalise_flock(f) for f in flock_ids}
    if item.valkyrie_id:
        return item.valkyrie_id == valkyrie_id
    return item.environment_id == environment_id


def _normalise_flock(flock_id: str) -> str:
    value = flock_id.strip()
    if not value:
        return ""
    return value if value.startswith("flock:") else f"flock:{value}"


def review_requested_event(item: ReviewItem, *, source: str) -> SleipnirEvent:
    return _review_event(
        registry.ODIN_REVIEW_REQUESTED,
        item,
        source=source,
        summary=f"review requested: {item.title} ({item.kind})",
        urgency=item.urgency,
    )


def review_decided_event(item: ReviewItem, *, source: str) -> SleipnirEvent:
    if item.status not in REVIEW_DECISIONS | {ReviewStatus.APPLIED.value}:
        raise ValueError(f"review item {item.item_id} has no decision to publish")
    return _review_event(
        registry.ODIN_REVIEW_DECIDED,
        item,
        source=source,
        summary=(
            f"review {item.status}: {item.title} ({item.kind}) by {item.decided_by or 'operator'}"
        ),
        urgency=item.urgency,
    )


def review_resolved_event(item: ReviewItem, *, source: str) -> SleipnirEvent:
    return _review_event(
        registry.ODIN_REVIEW_RESOLVED,
        item,
        source=source,
        summary=f"review {item.apply_outcome or 'resolved'}: {item.title} ({item.kind})",
        urgency=0.3,
    )


def _review_event(
    event_type: str,
    item: ReviewItem,
    *,
    source: str,
    summary: str,
    urgency: float,
) -> SleipnirEvent:
    return SleipnirEvent(
        event_type=event_type,
        source=source,
        payload=item.to_payload(),
        summary=summary,
        urgency=urgency,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=item.correlation_id or item.item_id,
        causation_id=item.causation_id or None,
    )


class JsonReviewStore:
    """File-backed review item store.

    Serves as the resident-side outbox (pending requests survive restarts and
    re-announce) and as the test/in-process implementation of the central
    queue's storage contract.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        self._items: dict[str, ReviewItem] = {}
        self._load()

    def list(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        environment_id: str | None = None,
    ) -> list[ReviewItem]:
        rows = list(self._items.values())
        if status:
            rows = [item for item in rows if item.status == status]
        if kind:
            rows = [item for item in rows if item.kind == kind]
        if environment_id:
            rows = [item for item in rows if item.environment_id == environment_id]
        return sorted(rows, key=lambda item: item.requested_at)

    def get(self, item_id: str) -> ReviewItem:
        try:
            return self._items[item_id]
        except KeyError as exc:
            raise ValueError(f"unknown review item: {item_id}") from exc

    def find_pending(self, dedupe_key: str) -> ReviewItem | None:
        if not dedupe_key:
            return None
        for item in self._items.values():
            if item.dedupe_key == dedupe_key and item.is_pending:
                return item
        return None

    def save(self, item: ReviewItem) -> ReviewItem:
        self._items[item.item_id] = item
        self._save()
        return item

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._items = {}
            return
        items = raw.get("items", [])
        loaded: dict[str, ReviewItem] = {}
        for entry in items:
            try:
                item = ReviewItem.from_payload(entry)
            except (ValueError, TypeError) as exc:
                logger.warning("review store: skipping unreadable item: %s", exc)
                continue
            loaded[item.item_id] = item
        self._items = loaded

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"items": [item.to_payload() for item in self.list()]}
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class ReviewRequester:
    """The one way anything files a review request.

    Persists the pending item in the local store (outbox), publishes
    ``odin.review.requested``, dedupes by ``dedupe_key`` so repeated dreams
    and restarts never flood the queue, and re-announces pending items when a
    resident comes back up.
    """

    def __init__(
        self,
        *,
        publisher: SleipnirPublisher,
        store: JsonReviewStore | None = None,
        source: str = "ravn:review-requester",
    ) -> None:
        self._publisher = publisher
        self._store = store
        self._source = source

    @property
    def store(self) -> JsonReviewStore | None:
        return self._store

    async def request(self, item: ReviewItem) -> ReviewItem | None:
        """File a review item; returns None when an equivalent one is pending."""
        if self._store is not None:
            existing = self._store.find_pending(item.dedupe_key)
            if existing is not None:
                return None
            self._store.save(item)
        await self._publisher.publish(review_requested_event(item, source=self._source))
        return item

    def has_pending(self, dedupe_key: str) -> bool:
        if self._store is None:
            return False
        return self._store.find_pending(dedupe_key) is not None

    def record_decision(self, item: ReviewItem) -> None:
        """Sync the outbox copy with a decided/resolved item."""
        if self._store is None:
            return
        self._store.save(item)

    async def reannounce(self) -> int:
        """Re-publish pending items after a restart; returns the count."""
        if self._store is None:
            return 0
        pending = self._store.list(status=ReviewStatus.PENDING.value)
        for item in pending:
            await self._publisher.publish(review_requested_event(item, source=self._source))
        return len(pending)
