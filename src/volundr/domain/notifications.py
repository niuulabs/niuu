"""Forge notification domain: the stored feed, reader state, delivery rules and outbox.

The wire vocabulary (kinds, severities, sources, links, drafts and deterministic ids)
is the shared contract in :mod:`niuu.domain.notifications`. This module adds what
only Forge owns: the stored, cursor-ordered notification, how a reader's scope and
feed query are expressed, the per-reader read watermark, owner delivery rules, the
delivery outbox rows a dispatcher settles and the durable copy of an attached image.

Image state. A notification recorded with an image reference gets an image row in
the same transaction, ``pending``. Forge then copies the bytes once, outside the
projection: ``ready`` when the image and its thumbnail are stored, ``failed`` (with
the reason) when the copy was refused or could not be made. The notification itself
is never withheld or rolled back because of its image; a failed copy is an explicit,
reported state (``image_state``/``image_error`` on the item), never a silent drop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from niuu.domain.models import Principal
from niuu.domain.notifications import (
    NOTIFICATION_ADMIN_ROLE,
    NOTIFICATION_IMAGE_THUMB_VARIANT,
    NotificationImageRef,
    NotificationKind,
    NotificationLink,
    NotificationSeverity,
    NotificationSource,
    NotificationSpeak,
    notification_id,
    notification_image_url,
    notification_visible_to,
    severity_rank,
)

# Protocol limits for owner rules. Like the draft limits in the shared contract they
# bound what a client may persist per rule; they are not tunables.
MAX_RULE_NAME_CHARS = 200
MAX_RULE_MATCH_VALUES = 100
MAX_RULE_MATCH_VALUE_CHARS = 200
MAX_INTEGRATION_ID_CHARS = 200
MAX_RULE_CONFIG_KEYS = 50
MAX_SINK_NAME_CHARS = 64
SINK_NAME_PATTERN = r"^[a-z0-9][a-z0-9_.-]*$"

_CLOCK_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

#: Metadata keys that anchor a notification to a resident and one of its channels.
#: A resident's notification has no Forge session (``session_id`` is null); a null
#: ``resident_session_id`` is the resident's main room.
RESIDENT_ID_KEY = "resident_id"
RESIDENT_SESSION_ID_KEY = "resident_session_id"

#: Metadata keys that anchor a notification to an item of the ODIN review queue,
#: where the operator answers it (``POST`` to the decide path with the item id).
ODIN_REVIEW_ID_KEY = "odin_review_id"
ODIN_REVIEW_DECIDE_PATH_KEY = "odin_review_decide_path"

#: A notification recorded for the feed only: no rule schedules a delivery for it,
#: so it is never pushed or relayed to an external sink. A producer sets it for
#: items the owner should be able to see but must not be interrupted by.
FEED_ONLY_KEY = "feed_only"

#: Per-notification push shaping read by the push sink: ``push_category`` is the
#: APNs ``aps.category`` (so the phone can offer inline actions) and ``push_data``
#: a ``{key: str}`` map of extra custom keys carried in the push payload.
PUSH_CATEGORY_KEY = "push_category"
PUSH_DATA_KEY = "push_data"


def _metadata_text(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return value if isinstance(value, str) and value else None


class NotificationImageState(StrEnum):
    """Where a notification's image copy stands (see the module docstring)."""

    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"


class NotificationImageVariant(StrEnum):
    """Which stored bytes the image route serves."""

    ORIGINAL = "original"
    THUMB = NOTIFICATION_IMAGE_THUMB_VARIANT


class NotificationImage(BaseModel):
    """A notification's image as readers see it: its state and, once stored, its shape."""

    model_config = ConfigDict(frozen=True)

    state: NotificationImageState
    alt: str | None = None
    mime: str | None = None
    width: int | None = None
    height: int | None = None
    error: str | None = None

    def wire(self, notification_id: UUID) -> dict[str, Any] | None:
        """The item's ``image`` object, or ``None`` until the bytes are stored."""
        if self.state is not NotificationImageState.READY:
            return None
        return {
            "url": notification_image_url(notification_id),
            "thumbnail_url": notification_image_url(notification_id, thumbnail=True),
            "mime": self.mime,
            "width": self.width,
            "height": self.height,
            "alt": self.alt,
        }


class Notification(BaseModel):
    """One stored notification: a durable, cursor-ordered feed entry."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    seq: int = Field(ge=1)
    dedupe_key: str
    session_id: UUID | None = None
    session_seq: int | None = None
    session_name: str | None = None
    owner_id: str
    tenant_id: str | None = None
    project_id: str | None = None
    kind: NotificationKind
    severity: NotificationSeverity
    source: NotificationSource
    title: str
    body: str = ""
    links: list[NotificationLink] = Field(default_factory=list)
    engine: str | None = None
    model: str | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    image: NotificationImage | None = None
    speak: NotificationSpeak | None = None
    resolved_at: datetime | None = None
    resolved_by_notification_id: UUID | None = None
    resolution: str | None = None
    #: Computed at read time, never stored: see ``notifications.stale_after_seconds``.
    stale: bool = False

    @property
    def resolved(self) -> bool:
        return self.resolved_at is not None

    @property
    def turn_id(self) -> str | None:
        """The transcript turn this notification anchors to, or ``None`` when it has none.

        Agent notifications anchor to their own notification turn and ``reply_ready`` to the
        final reply turn; attention and directly submitted notifications have no turn.
        """
        return _metadata_text(self.metadata, "turn_id")

    @property
    def resident_id(self) -> str | None:
        """The resident this notification is about, or ``None`` for a session's."""
        return _metadata_text(self.metadata, RESIDENT_ID_KEY)

    @property
    def resident_session_id(self) -> str | None:
        """The resident channel it is about; ``None`` is the main room (or no resident)."""
        return _metadata_text(self.metadata, RESIDENT_SESSION_ID_KEY)

    @property
    def odin_review_id(self) -> str | None:
        """The ODIN review item this notification asks the operator to answer, if any."""
        return _metadata_text(self.metadata, ODIN_REVIEW_ID_KEY)

    @property
    def odin_review_decide_path(self) -> str | None:
        """The path (on the host serving the review queue) that decides that item."""
        return _metadata_text(self.metadata, ODIN_REVIEW_DECIDE_PATH_KEY)

    @property
    def input_request(self) -> dict[str, str | None] | None:
        """The pending request a system ``attention`` item stands for, or ``None``.

        ``{kind: question|confirmation|permission, request_id}``: the session (or
        resident) is blocked on that request, and it is answered where the transcript's
        question card answers it (``ask_user_answer`` / the permission response on the
        session socket), never through the notification. ``request_id`` is null when
        the runtime reported none. Agent notices carry ``question``/``ask`` instead.
        """
        if (
            self.kind is not NotificationKind.ATTENTION
            or self.source is not NotificationSource.SYSTEM
        ):
            return None
        kind = _metadata_text(self.metadata, "input_kind")
        if kind is None:
            return None
        return {"kind": kind, "request_id": _metadata_text(self.metadata, "request_id")}

    def wire(self) -> dict[str, Any]:
        """The ``NotificationResponse`` JSON without the per-reader ``read`` flag.

        This is exactly the ``session_notification`` SSE payload (contract §5).
        """
        data = self.model_dump(mode="json", exclude={"dedupe_key", "metadata", "image"})
        data["turn_id"] = self.turn_id
        data["resident_id"] = self.resident_id
        data["resident_session_id"] = self.resident_session_id
        data["odin_review_id"] = self.odin_review_id
        data["odin_review_decide_path"] = self.odin_review_decide_path
        # Question signals (F11b) live in metadata; null when the notice carries none.
        data["question"] = self.metadata.get("question")
        data["about_question"] = self.metadata.get("about_question")
        data["ask"] = self.metadata.get("ask")
        data["resolves"] = self.metadata.get("resolves")
        data["resolved"] = self.resolved
        data["input_request"] = self.input_request
        data["image"] = self.image.wire(self.id) if self.image is not None else None
        data["image_state"] = self.image.state.value if self.image is not None else None
        data["image_error"] = self.image.error if self.image is not None else None
        return data


class NotificationCandidate(BaseModel):
    """A notification Forge wants to record; its id and order are assigned on insert."""

    model_config = ConfigDict(frozen=True)

    dedupe_key: str = Field(min_length=1)
    session_id: UUID | None = None
    session_seq: int | None = None
    session_name: str | None = None
    owner_id: str
    tenant_id: str | None = None
    project_id: str | None = None
    kind: NotificationKind
    severity: NotificationSeverity
    source: NotificationSource
    title: str = Field(min_length=1)
    body: str = ""
    links: list[NotificationLink] = Field(default_factory=list)
    engine: str | None = None
    model: str | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    image: NotificationImageRef | None = None
    speak: NotificationSpeak | None = None

    @property
    def id(self) -> UUID:
        return notification_id(self.dedupe_key)


@dataclass(frozen=True)
class NotificationScope:
    """Which notifications a reader may see: their own, or (admins) their tenant's."""

    user_id: str
    tenant_id: str
    is_admin: bool

    @classmethod
    def for_principal(cls, principal: Principal) -> NotificationScope:
        return cls(
            user_id=principal.user_id,
            tenant_id=principal.tenant_id or "",
            is_admin=NOTIFICATION_ADMIN_ROLE in principal.roles,
        )

    def allows(self, owner_id: str | None, tenant_id: str | None) -> bool:
        roles = [NOTIFICATION_ADMIN_ROLE] if self.is_admin else []
        return notification_visible_to(
            user_id=self.user_id,
            roles=roles,
            tenant_id=self.tenant_id,
            owner_id=owner_id,
            notification_tenant_id=tenant_id,
        )


@dataclass(frozen=True)
class NotificationQuery:
    """Filters and one cursor for a feed page.

    ``before`` pages newest first; ``after`` pages ascending (gap-fill). Neither
    means the newest page. Empty filter tuples mean "any".
    """

    limit: int
    before: int | None = None
    after: int | None = None
    kinds: tuple[NotificationKind, ...] = ()
    min_severity: NotificationSeverity | None = None
    sources: tuple[NotificationSource, ...] = ()
    session_id: UUID | None = None
    project_id: str | None = None
    unread: bool | None = None
    unresolved: bool | None = None

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be at least 1")
        if self.before is not None and self.after is not None:
            raise ValueError("Use either before or after, not both")

    @property
    def ascending(self) -> bool:
        return self.after is not None

    def severities(self) -> tuple[NotificationSeverity, ...]:
        """Severities at or above ``min_severity``; empty means any."""
        if self.min_severity is None:
            return ()
        floor = severity_rank(self.min_severity)
        return tuple(s for s in NotificationSeverity if severity_rank(s) >= floor)


@dataclass(frozen=True)
class NotificationPage:
    """One page from the repository; ``next_before`` is set when older rows remain."""

    items: list[Notification]
    next_before: int | None = None


@dataclass(frozen=True)
class NotificationFeed:
    """A reader's feed page with their watermark and counters."""

    items: list[Notification]
    next_before: int | None
    head_seq: int
    read_through_seq: int
    unread_count: int
    revision: int = 0
    read_ids: set[UUID] = field(default_factory=set)


@dataclass(frozen=True)
class NotificationReadState:
    """A reader's feed watermark plus the counters derived from it."""

    read_through_seq: int
    revision: int
    unread_count: int
    head_seq: int


@dataclass(frozen=True)
class ReadWatermark:
    read_through_seq: int = 0
    revision: int = 0


class NotificationError(Exception):
    """Base class for notification domain errors."""


class NotificationNotFoundError(NotificationError):
    """The notification or rule does not exist for this reader."""


class NotificationValidationError(NotificationError, ValueError):
    """A request is well-formed but not acceptable (unknown sink, future cursor, ...)."""


class NotificationReadStateConflictError(NotificationError):
    """Another change to the reader's watermark won; re-read before retrying."""


class NotificationImageError(NotificationError):
    """An attached image could not be copied. The message is the reader-facing reason."""


class NotificationStoreUnavailableError(NotificationError):
    """The notification store could not be reached; the caller may retry safely.

    Projection is idempotent (dedupe keys), so a producer that retries after this
    error cannot create duplicates.
    """


class NotificationRuleMatch(BaseModel):
    """What a rule selects. An empty list means "any"."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kinds: list[NotificationKind] = Field(default_factory=list, max_length=MAX_RULE_MATCH_VALUES)
    min_severity: NotificationSeverity = NotificationSeverity.INFO
    sources: list[NotificationSource] = Field(
        default_factory=list, max_length=MAX_RULE_MATCH_VALUES
    )
    project_ids: list[str] = Field(default_factory=list, max_length=MAX_RULE_MATCH_VALUES)
    session_ids: list[UUID] = Field(default_factory=list, max_length=MAX_RULE_MATCH_VALUES)

    @field_validator("project_ids")
    @classmethod
    def _bounded_project_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > MAX_RULE_MATCH_VALUE_CHARS for item in value):
            raise ValueError(
                f"project_ids must contain 1 to {MAX_RULE_MATCH_VALUE_CHARS} characters"
            )
        return value

    def matches(self, notification: Notification) -> bool:
        if self.kinds and notification.kind not in self.kinds:
            return False
        if severity_rank(notification.severity) < severity_rank(self.min_severity):
            return False
        if self.sources and notification.source not in self.sources:
            return False
        if self.project_ids and notification.project_id not in self.project_ids:
            return False
        return not self.session_ids or notification.session_id in self.session_ids


def _parse_clock(value: str) -> time:
    if not _CLOCK_PATTERN.fullmatch(value):
        raise ValueError("Use a 24-hour HH:MM time")
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


class NotificationQuietHours(BaseModel):
    """A daily window in which only severe notifications are delivered externally.

    Evaluated by the dispatcher at claim time; a held-back delivery becomes
    ``suppressed``. A window whose start is after its end spans midnight.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: str
    end: str
    timezone: str
    allow_min_severity: NotificationSeverity = NotificationSeverity.CRITICAL

    @field_validator("start", "end")
    @classmethod
    def _valid_clock(cls, value: str) -> str:
        _parse_clock(value)
        return value

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown IANA timezone: {value}") from exc
        return value

    @model_validator(mode="after")
    def _non_empty_window(self) -> NotificationQuietHours:
        if self.start == self.end:
            raise ValueError("Quiet hours start and end must differ")
        return self

    def is_quiet(self, at: datetime) -> bool:
        """Whether ``at`` (timezone-aware) falls inside the window, in its timezone."""
        if at.tzinfo is None:
            raise ValueError("Quiet hours need a timezone-aware instant")
        local = at.astimezone(ZoneInfo(self.timezone)).time().replace(second=0, microsecond=0)
        start, end = _parse_clock(self.start), _parse_clock(self.end)
        if start < end:
            return start <= local < end
        return local >= start or local < end

    def holds_back(self, severity: NotificationSeverity, at: datetime) -> bool:
        """True when a notification of ``severity`` must not be delivered at ``at``."""
        if not self.is_quiet(at):
            return False
        return severity_rank(severity) < severity_rank(self.allow_min_severity)


class NotificationRateLimit(BaseModel):
    """At most ``max_count`` external deliveries per rule in any ``window_seconds``.

    Stored under ``config["rate_limit"]``; evaluated by the dispatcher at claim time.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_count: int = Field(ge=1)
    window_seconds: int = Field(ge=1)


class NotificationRuleSpec(BaseModel):
    """The writable part of a delivery rule (create and full replace)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_RULE_NAME_CHARS)
    enabled: bool = True
    match: NotificationRuleMatch = Field(default_factory=NotificationRuleMatch)
    sink: str = Field(min_length=1, max_length=MAX_SINK_NAME_CHARS, pattern=SINK_NAME_PATTERN)
    integration_connection_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_INTEGRATION_ID_CHARS
    )
    config: dict[str, Any] = Field(default_factory=dict)
    quiet_hours: NotificationQuietHours | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = " ".join(value.split())
        if not stripped:
            raise ValueError("name must not be blank")
        return stripped

    @field_validator("config")
    @classmethod
    def _valid_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > MAX_RULE_CONFIG_KEYS:
            raise ValueError(f"config accepts at most {MAX_RULE_CONFIG_KEYS} keys")
        if "rate_limit" in value:
            NotificationRateLimit.model_validate(value["rate_limit"])
        return value

    @property
    def rate_limit(self) -> NotificationRateLimit | None:
        raw = self.config.get("rate_limit")
        if raw is None:
            return None
        return NotificationRateLimit.model_validate(raw)


class NotificationRule(NotificationRuleSpec):
    """An owner's rule: which notifications to deliver, and through which sink."""

    id: UUID
    owner_id: str
    created_at: datetime
    updated_at: datetime


#: Namespace of the per-owner needs-input push rule ids (see ``attention_push_rule``).
_ATTENTION_PUSH_RULE_NAMESPACE = UUID("5b0c9d6e-3f1a-4c7e-9a2b-8d4e6f1a7c3b")
ATTENTION_PUSH_RULE_NAME = "Needs-input pushes"


def attention_push_rule(owner_id: str, sink: str, now: datetime) -> NotificationRule:
    """The rule that pushes an owner every "session needs you" moment (``push.enabled``).

    Its id is derived from the owner, so provisioning it twice (two devices
    registering at once) creates one rule. It selects only the attention items
    Forge raises itself, which is what the needs-input push always covered.
    """
    return NotificationRule(
        id=uuid5(_ATTENTION_PUSH_RULE_NAMESPACE, owner_id),
        owner_id=owner_id,
        name=ATTENTION_PUSH_RULE_NAME,
        match=NotificationRuleMatch(
            kinds=[NotificationKind.ATTENTION], sources=[NotificationSource.SYSTEM]
        ),
        sink=sink,
        created_at=now,
        updated_at=now,
    )


def rules_matching(
    notification: Notification, rules: list[NotificationRule]
) -> list[NotificationRule]:
    """The enabled rules of the notification's owner that select it.

    Pure: the projection calls it inside the insert transaction to schedule outbox
    rows. Quiet hours and rate limits are NOT applied here; the dispatcher evaluates
    them when it claims a delivery, so a held-back message is recorded as suppressed.
    A feed-only notification (``FEED_ONLY_KEY``) matches no rule at all.
    """
    if notification.metadata.get(FEED_ONLY_KEY) is True:
        return []
    return [
        rule
        for rule in rules
        if rule.enabled
        and rule.owner_id == notification.owner_id
        and rule.match.matches(notification)
    ]


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DELIVERED = "delivered"
    FAILED = "failed"
    DEAD = "dead"
    SUPPRESSED = "suppressed"


class NotificationDelivery(BaseModel):
    """One outbox row: a notification scheduled for one rule's sink."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    notification_id: UUID
    rule_id: UUID
    sink: str
    status: DeliveryStatus
    attempts: int = Field(ge=0)
    next_attempt_at: datetime
    lease_until: datetime | None = None
    last_error: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ClaimedDelivery:
    """A leased delivery with everything a dispatcher needs to render and route it.

    ``delivery.attempts`` already counts this claim. The pair ``(delivery.id,
    delivery.attempts)`` fences the settle calls: a worker whose lease expired and
    was re-claimed by another can no longer settle the row.
    """

    delivery: NotificationDelivery
    notification: Notification
    rule: NotificationRule


@dataclass(frozen=True)
class NotificationSinkInfo:
    """A sink a rule may name, as shown by ``GET /notifications/sinks``."""

    name: str
    label: str
    requires_integration: bool = False


#: The sink name for rules that deliver through an owner's messaging integration.
INTEGRATION_SINK = NotificationSinkInfo(
    name="integration", label="Messaging integration", requires_integration=True
)


@dataclass(frozen=True)
class PendingNotificationImage:
    """A claimed image copy: what to fetch, for which notification, and its claim fence.

    ``attempts`` already counts this claim; ``(notification_id, attempts)`` fences the
    settle calls so a worker whose lease expired can no longer settle the row.
    """

    notification_id: UUID
    session_id: UUID | None
    ref: NotificationImageRef
    attempts: int


@dataclass(frozen=True)
class FetchedImage:
    """Bytes fetched for an image, with the content type their source declared."""

    content: bytes
    content_type: str


@dataclass(frozen=True)
class ProcessedNotificationImage:
    """A decoded image ready to store: the original bytes plus a bounded thumbnail."""

    mime: str
    width: int
    height: int
    content: bytes
    thumbnail_mime: str
    thumbnail_width: int
    thumbnail_height: int
    thumbnail: bytes


@dataclass(frozen=True)
class NotificationImageContent:
    """Stored image bytes as the image route serves them."""

    mime: str
    content: bytes
