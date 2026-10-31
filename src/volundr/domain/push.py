"""Apple push domain: what Forge pushes to the Lexi app, and the exact payloads.

A device registers one token per *kind* (see :class:`PushTokenKind`). For every
notification the Guild records, the push gateway sends:

- an **alert** (``apns-push-type: alert``) that opens the notification in the app;
- a **Live Activity** update (``liveactivity``) carrying the newest notifications,
  or a push-to-start when no activity is running;
- a **widget** reload (``widgets``);
- a silent **background** push (``background``) that lets iOS wake the app.

The payload shapes below are a wire contract with the app (its Swift
``ForgeNotificationsActivityContent`` and its deep-link parser), so the limits and
key names are constants here, not configuration. Everything in this module is pure.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import quote, urlencode

from niuu.domain.notifications import notification_image_url

# -- Contract with the app ------------------------------------------------------

#: Swift's ``JSONDecoder`` default date strategy: seconds since 2001-01-01T00:00:00Z.
SWIFT_REFERENCE_DATE_UNIX = 978_307_200
#: ``ForgeNotificationsActivityContent.maxItems``.
LIVE_ACTIVITY_MAX_ITEMS = 3
#: ``ForgeNotificationsActivityContent.titleLimit`` / ``nameLimit``: the Swift
#: initialiser cuts longer values, so the server stays within them.
LIVE_ACTIVITY_TITLE_CHARS = 90
LIVE_ACTIVITY_SESSION_NAME_CHARS = 40
#: The ActivityKit attributes type a push-to-start names.
LIVE_ACTIVITY_ATTRIBUTES_TYPE = "ForgeNotificationsActivityAttributes"
#: APNs refuses a larger payload (alert, Live Activity and widget pushes alike).
APNS_MAX_PAYLOAD_BYTES = 4096
#: A feed item's ``image_state`` while its node is still copying the image.
IMAGE_PENDING_STATE = "pending"
#: APNs refuses a longer ``apns-collapse-id``.
APNS_MAX_COLLAPSE_ID_BYTES = 64

#: Why a background push was sent, as the app reads it (``forge.reason``).
BACKGROUND_REASON = "notification"
#: Topic suffixes APNs requires for the non-alert push types.
LIVE_ACTIVITY_TOPIC_SUFFIX = ".push-type.liveactivity"
WIDGETS_TOPIC_SUFFIX = ".push-type.widgets"

#: APNs answers that mean "this token will never work again": the token is deleted.
APNS_GONE_STATUS = 410
APNS_DEAD_TOKEN_REASONS: frozenset[str] = frozenset(
    {"BadDeviceToken", "Unregistered", "ExpiredToken"}
)
#: The provider JWT was refused as stale; a fresh one may be accepted.
APNS_EXPIRED_PROVIDER_TOKEN = "ExpiredProviderToken"
#: APNs statuses: accepted, throttled, and the first server-error status.
APNS_ACCEPTED_STATUS = 200
APNS_TOO_MANY_REQUESTS_STATUS = 429
APNS_SERVER_ERROR_STATUS = 500
#: ``apns-priority``: deliver immediately, or when power allows.
APNS_PRIORITY_IMMEDIATE = 10
APNS_PRIORITY_POWER_SAVING = 5


class PushTokenKind(StrEnum):
    """What a registered token can receive."""

    #: The app's device token: user-visible alerts.
    ALERT = "alert"
    #: The update token of one running Live Activity (``activity_id`` names it).
    LIVE_ACTIVITY = "live_activity"
    #: The push-to-start token: lets Forge start the Live Activity.
    LIVE_ACTIVITY_START = "live_activity_start"
    #: The widget extension's token: WidgetKit reloads.
    WIDGET = "widget"
    #: A widget token the app read itself (WidgetCenter.currentPushInfo) instead of the
    #: extension's handler. Stored and counted per environment so its source can be
    #: told apart; never pushed to (LEX-1186).
    WIDGET_FALLBACK = "widget_fallback"


#: Hex digits of a token's SHA-256 shown in logs: enough to tell two tokens apart,
#: never enough to recover one.
TOKEN_FINGERPRINT_HEX = 12


def token_fingerprint(token: str) -> str:
    """A short SHA-256 prefix of a push token, for logs that must compare tokens."""
    return hashlib.sha256(token.encode()).hexdigest()[:TOKEN_FINGERPRINT_HEX]


class UnsupportedTokenKindError(ValueError):
    """A device registry cannot hold this token kind (it keeps alert tokens only)."""


#: Why a registration's environment was assumed: none was sent, or one we do not know.
EnvironmentAssumed = Literal["missing", "unknown"]


class PushEnvironment(StrEnum):
    """Which APNs host issued a token: development installs use the sandbox."""

    SANDBOX = "sandbox"
    PRODUCTION = "production"


class ApnsPushType(StrEnum):
    """``apns-push-type`` values Forge sends."""

    ALERT = "alert"
    BACKGROUND = "background"
    LIVE_ACTIVITY = "liveactivity"
    WIDGETS = "widgets"


class PushTestKind(StrEnum):
    """What an operator's test push sends; each goes to one kind of token."""

    ALERT = "alert"
    BACKGROUND = "background"
    LIVE_ACTIVITY = "live_activity"
    LIVE_ACTIVITY_START = "live_activity_start"
    WIDGET = "widget"

    @property
    def token_kind(self) -> PushTokenKind:
        """The registered tokens this push goes to (a background push wakes the
        app through its alert token)."""
        if self is PushTestKind.BACKGROUND:
            return PushTokenKind.ALERT
        return PushTokenKind(self.value)


class LiveActivityEvent(StrEnum):
    UPDATE = "update"
    START = "start"


class InterruptionLevel(StrEnum):
    ACTIVE = "active"
    TIME_SENSITIVE = "time-sensitive"


def apns_topic(bundle_id: str, push_type: ApnsPushType) -> str:
    """The ``apns-topic`` for a push type: the bundle id, suffixed for non-alerts."""
    if push_type is ApnsPushType.LIVE_ACTIVITY:
        return bundle_id + LIVE_ACTIVITY_TOPIC_SUFFIX
    if push_type is ApnsPushType.WIDGETS:
        return bundle_id + WIDGETS_TOPIC_SUFFIX
    return bundle_id


@dataclass(frozen=True)
class ApnsPush:
    """One request to APNs: a token, its routing headers and the JSON payload."""

    token: str
    environment: PushEnvironment
    topic: str
    push_type: ApnsPushType
    priority: int
    payload: Mapping[str, Any]
    collapse_id: str | None = None
    #: Unix seconds after which APNs stops trying; ``None`` lets APNs decide.
    expiration: int | None = None

    def body(self) -> bytes:
        """The exact bytes sent, checked against the APNs payload limit."""
        encoded = json.dumps(self.payload, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        if len(encoded) > APNS_MAX_PAYLOAD_BYTES:
            raise ValueError(
                f"APNs payload is {len(encoded)} bytes; the limit is {APNS_MAX_PAYLOAD_BYTES}"
            )
        return encoded


@dataclass(frozen=True)
class ApnsResult:
    """APNs' answer for one push. ``status_code`` is ``None`` when APNs was not reached."""

    status_code: int | None
    reason: str = ""
    apns_id: str = ""
    retry_after: float | None = None

    @property
    def accepted(self) -> bool:
        return self.status_code == APNS_ACCEPTED_STATUS

    @property
    def token_is_dead(self) -> bool:
        """APNs says the token is gone or was never valid: delete it."""
        return self.status_code == APNS_GONE_STATUS or self.reason in APNS_DEAD_TOKEN_REASONS

    @property
    def retryable(self) -> bool:
        """A later attempt can succeed: APNs unreachable, throttling or a server error."""
        if self.status_code is None:
            return True
        return (
            self.status_code == APNS_TOO_MANY_REQUESTS_STATUS
            or self.status_code >= APNS_SERVER_ERROR_STATUS
        )

    def describe(self) -> str:
        """A short, secret-free description for logs and delivery rows."""
        if self.accepted:
            return "accepted"
        if self.status_code is None:
            return f"not reached ({self.reason})" if self.reason else "not reached"
        if self.reason:
            return f"HTTP {self.status_code} {self.reason}"
        return f"HTTP {self.status_code}"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class GuildNotification:
    """One notification as the Guild's merged feed serves it.

    ``instance_id`` is the Guild's id for the node that recorded it: the app keys a
    session by ``<instance_id>:<session_id>``. ``image_url`` / ``image_thumbnail_url``
    are the node-routed relative paths of its image: the feed's once the image is
    stored, and the same paths while the node is still copying it (the route then
    answers 404 until the copy is stored). A failed copy has none.
    """

    id: str
    instance_id: str
    seq: int
    owner_id: str
    kind: str
    severity: str
    source: str
    title: str
    body: str = ""
    session_id: str | None = None
    session_name: str | None = None
    #: The session's engine family (``claude``, ``codex``, …), as the feed reports it.
    engine: str | None = None
    turn_id: str | None = None
    created_at: datetime | None = None
    read: bool = False
    image_url: str | None = None
    image_thumbnail_url: str | None = None

    @classmethod
    def from_feed_item(cls, item: Mapping[str, Any]) -> GuildNotification:
        """Parse a feed item; raises ``ValueError`` when it is not a notification."""
        notification_id, instance_id = item.get("id"), item.get("instance_id")
        owner_id, seq = item.get("owner_id"), item.get("seq")
        kind, title = item.get("kind"), item.get("title")
        if not isinstance(notification_id, str) or not notification_id:
            raise ValueError("Feed item has no id")
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError(f"Feed item {notification_id} has no instance_id")
        if not isinstance(owner_id, str) or not owner_id:
            raise ValueError(f"Feed item {notification_id} has no owner_id")
        if type(seq) is not int:
            raise ValueError(f"Feed item {notification_id} has no seq")
        if not isinstance(kind, str) or not kind or not isinstance(title, str) or not title:
            raise ValueError(f"Feed item {notification_id} has no kind or title")
        image = item.get("image")
        if isinstance(image, Mapping):
            image_url = _optional_text(image.get("url"))
            thumbnail_url = _optional_text(image.get("thumbnail_url"))
        elif item.get("image_state") == IMAGE_PENDING_STATE:
            # A push must not wait for the copy; the paths are the same once stored.
            image_url = notification_image_url(notification_id, instance_id=instance_id)
            thumbnail_url = notification_image_url(
                notification_id, thumbnail=True, instance_id=instance_id
            )
        else:
            image_url = thumbnail_url = None
        return cls(
            id=notification_id,
            instance_id=instance_id,
            seq=seq,
            owner_id=owner_id,
            kind=kind,
            severity=str(item.get("severity") or "info"),
            source=str(item.get("source") or ""),
            title=title,
            body=str(item.get("body") or ""),
            session_id=_optional_text(item.get("session_id")),
            session_name=_optional_text(item.get("session_name")),
            engine=_optional_text(item.get("engine")),
            turn_id=_optional_text(item.get("turn_id")),
            created_at=_parse_time(item.get("created_at")),
            read=item.get("read") is True,
            image_url=image_url,
            image_thumbnail_url=thumbnail_url,
        )

    def stored(self) -> dict[str, Any]:
        """The feed-item fields the gateway keeps until the alert is settled."""
        data: dict[str, Any] = {
            "id": self.id,
            "instance_id": self.instance_id,
            "seq": self.seq,
            "owner_id": self.owner_id,
            "kind": self.kind,
            "severity": self.severity,
            "source": self.source,
            "title": self.title,
            "body": self.body,
            "session_id": self.session_id,
            "session_name": self.session_name,
            "engine": self.engine,
            "turn_id": self.turn_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "read": self.read,
        }
        if self.image_url:
            data["image"] = {"url": self.image_url, "thumbnail_url": self.image_thumbnail_url}
        return data


def swift_reference_seconds(moment: datetime) -> float:
    """``moment`` as Swift's default ``Date`` JSON: seconds since 2001-01-01 UTC."""
    if moment.tzinfo is None:
        raise ValueError("A Swift date needs a timezone-aware instant")
    return moment.timestamp() - SWIFT_REFERENCE_DATE_UNIX


def clip(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` characters, exactly as the app's ``prefix`` does."""
    return text[:limit]


def shorten(text: str, limit: int) -> str:
    """Collapse whitespace and end a cut text with an ellipsis (alert bodies)."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: max(limit - 1, 0)].rstrip() + "…"


def activity_deep_link(
    base: str,
    notification_id: str,
    *,
    instance_id: str | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> str:
    """``<base>/<notification id>?inst=&session=&turn=`` with only the known parts.

    ``base`` is the app's activity link (``lexichat://activity``). The id is one
    percent-encoded path segment; absent query items are left out.
    """
    link = f"{base.rstrip('/')}/{quote(notification_id, safe='')}"
    query = [
        (name, value)
        for name, value in (("inst", instance_id), ("session", session_id), ("turn", turn_id))
        if value
    ]
    if not query:
        return link
    return f"{link}?{urlencode(query, quote_via=quote)}"


def absolute_url(base: str, path: str | None) -> str | None:
    """``path`` on the origin ``base``; ``None`` without both. Absolute URLs pass through."""
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    if not base or not path.startswith("/"):
        return None
    return base.rstrip("/") + path


def alert_payload(
    *,
    title: str,
    body: str,
    deep_link: str,
    interruption_level: InterruptionLevel,
    category: str,
    sound: str,
    subtitle: str | None = None,
    notification: GuildNotification | None = None,
    attention: bool = False,
    image_url: str | None = None,
    image_thumbnail_url: str | None = None,
) -> dict[str, Any]:
    """The alert push: what iOS shows, plus the keys the app reads on a tap.

    An ``attention`` push (a kind that needs the user) also lets the app's notification
    service extension run (``mutable-content``) and carries what it needs to show the
    agent as the sender (LEX-1186): the ``attention`` marker, ``agent_id`` (the session),
    ``agent_name``, ``conversation_id`` (``<instance_id>:<session_id>``) and ``engine``
    (the engine family, lowercase, for the avatar).
    """
    alert: dict[str, Any] = {"title": title, "body": body}
    if subtitle:
        alert["subtitle"] = subtitle
    aps: dict[str, Any] = {
        "alert": alert,
        "interruption-level": interruption_level.value,
    }
    if sound:
        aps["sound"] = sound
    if category:
        aps["category"] = category
    payload: dict[str, Any] = {"aps": aps, "deepLink": deep_link}
    if notification is None:
        return payload
    if notification.session_id:
        aps["thread-id"] = notification.session_id
        payload["session_id"] = notification.session_id
    payload["notification_id"] = notification.id
    payload["instance_id"] = notification.instance_id
    payload["kind"] = notification.kind
    if attention:
        aps["mutable-content"] = 1
        payload["attention"] = True
        if notification.session_id:
            payload["agent_id"] = notification.session_id
            payload["conversation_id"] = f"{notification.instance_id}:{notification.session_id}"
        if notification.session_name:
            payload["agent_name"] = notification.session_name
        if notification.engine:
            payload["engine"] = notification.engine.lower()
    if image_url:
        # Lets a notification service extension attach the image before display.
        aps["mutable-content"] = 1
        payload["image_url"] = image_url
        if image_thumbnail_url:
            payload["image_thumbnail_url"] = image_thumbnail_url
    return payload


def live_activity_item(notification: GuildNotification) -> dict[str, Any]:
    """One ``ForgeNotificationsActivityContent.Item``; absent values are left out."""
    item: dict[str, Any] = {
        "id": notification.id,
        "kind": notification.kind,
        "title": clip(notification.title, LIVE_ACTIVITY_TITLE_CHARS),
    }
    if notification.session_name:
        item["sessionName"] = clip(notification.session_name, LIVE_ACTIVITY_SESSION_NAME_CHARS)
    item["instanceID"] = notification.instance_id
    if notification.session_id:
        item["sessionID"] = notification.session_id
    if notification.turn_id:
        item["turnID"] = notification.turn_id
    if notification.created_at is not None:
        item["createdAt"] = swift_reference_seconds(notification.created_at)
    item["unread"] = not notification.read
    return item


def live_activity_content_state(
    notifications: Sequence[GuildNotification],
    *,
    unread_count: int,
    updated_at: datetime,
    origin: str | None = None,
) -> dict[str, Any]:
    """The Live Activity's ``content-state``: the newest items, newest first.

    Keys and types match the app's synthesized ``Codable`` exactly; dates are Swift
    reference-date numbers. ``origin`` names the server that pushed the content.
    """
    state: dict[str, Any] = {
        "items": [live_activity_item(n) for n in notifications[:LIVE_ACTIVITY_MAX_ITEMS]],
        "unreadCount": max(0, unread_count),
        "updatedAt": swift_reference_seconds(updated_at),
    }
    if origin:
        state["origin"] = origin
    return state


def live_activity_payload(
    event: LiveActivityEvent,
    content_state: Mapping[str, Any],
    *,
    timestamp: int,
    stale_date: int,
    relevance_score: float,
    alert_title: str = "",
    alert_body: str = "",
) -> dict[str, Any]:
    """A Live Activity push. ``start`` also names the attributes and carries an alert.

    ``relevance-score`` ranks this activity against the app's others for the Dynamic
    Island (LEX-1353): notifications sit below a live call, which the app scores higher.
    """
    aps: dict[str, Any] = {
        "timestamp": timestamp,
        "event": event.value,
        "content-state": dict(content_state),
        "stale-date": stale_date,
        "relevance-score": relevance_score,
    }
    if event is LiveActivityEvent.START:
        aps["attributes-type"] = LIVE_ACTIVITY_ATTRIBUTES_TYPE
        aps["attributes"] = {}
        aps["alert"] = {"title": alert_title, "body": alert_body}
    return {"aps": aps}


def widget_payload() -> dict[str, Any]:
    """The WidgetKit reload push."""
    return {"aps": {"content-changed": True}}


def background_payload() -> dict[str, Any]:
    """The silent push that lets iOS wake the app to read every server it knows."""
    return {"aps": {"content-available": 1}, "forge": {"reason": BACKGROUND_REASON}}
