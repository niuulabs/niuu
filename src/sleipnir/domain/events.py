"""Sleipnir event model and event type registry."""

from __future__ import annotations

import fnmatch
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event type hierarchy
# ---------------------------------------------------------------------------

#: Known top-level namespaces and their sub-patterns.
#: Keys are namespace prefixes; values are documentation strings.
EVENT_NAMESPACES: dict[str, str] = {
    "ravn": "Ravn agent events (tool calls, reasoning steps, completions)",
    "tyr": "Tyr autonomous dispatcher events (tasks, sagas, raids, sessions)",
    "volundr": "Volundr platform events (sessions, repos, PRs, tokens, chronicles)",
    "bifrost": "Bifrost gateway events (routing, connection, auth)",
    "system": "Infrastructure and lifecycle events (health, config, restart)",
    "skuld": "Skuld broker events (session pod activity, tool use, token updates)",
    "mimir": "Mimir knowledge-base events (page writes, dream cycles)",
    "github": "GitHub webhook events (PR opened/merged, push to main, issue opened)",
}

#: Known high-level domain tags for events.
KNOWN_DOMAINS: frozenset[str] = frozenset(
    {"code", "infrastructure", "home", "business", "personal"}
)

#: Valid event type pattern: lowercase alphanumerics and dots, min 2 segments.
_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


def validate_event_type(event_type: str) -> None:
    """Raise ValueError if *event_type* is not a valid Sleipnir event type.

    Rules:
    - Must be lowercase, dot-separated segments (e.g. ``ravn.tool.complete``)
    - Must have at least two segments
    - First segment should be a known namespace; unknown namespaces emit a
      warning instead of raising to allow extensibility.
    """
    if not _EVENT_TYPE_RE.match(event_type):
        raise ValueError(
            f"Invalid event type {event_type!r}: must be lowercase dot-separated "
            "segments (e.g. 'ravn.tool.complete')"
        )
    namespace = event_type.split(".")[0]
    if namespace not in EVENT_NAMESPACES:
        logger.warning(
            "Unknown namespace %r in event type %r — not in registered namespaces %s. "
            "Register the namespace in EVENT_NAMESPACES to suppress this warning.",
            namespace,
            event_type,
            sorted(EVENT_NAMESPACES),
        )


def match_event_type(pattern: str, event_type: str) -> bool:
    """Return True if *event_type* matches *pattern*.

    Pattern syntax uses shell-style wildcards via :func:`fnmatch.fnmatch`:

    - ``ravn.*`` matches ``ravn.tool.complete`` and ``ravn.step.start``
    - ``ravn.tool.*`` matches ``ravn.tool.complete`` but not ``ravn.step.start``
    - ``*`` matches any event type
    - Exact strings match only themselves
    """
    return fnmatch.fnmatch(event_type, pattern)


# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class SleipnirEvent:
    """A structured event published over the Sleipnir event bus.

    :param event_id: Unique identifier (UUID4 string). Auto-generated if not supplied.
    :param event_type: Hierarchical dot-separated type (e.g. ``ravn.tool.complete``).
    :param source: Publisher identity string (e.g. ``ravn:agent-abc123``).
    :param payload: Event-specific data dictionary.
    :param summary: Human-readable one-liner describing the event.
    :param urgency: Priority hint in the range ``0.0`` (lowest) to ``1.0`` (highest).
    :param domain: High-level domain tag; one of :data:`KNOWN_DOMAINS`.
    :param timestamp: When the event occurred (UTC).
    :param correlation_id: Groups causally related events across services.
    :param causation_id: ID of the event that directly caused this one.
    :param tenant_id: Tenant scope; ``None`` for single-tenant deployments.
    :param ttl: Seconds until the event expires; ``None`` means no expiry.
    """

    event_type: str
    source: str
    payload: dict
    summary: str
    urgency: float
    domain: str
    timestamp: datetime
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str | None = None
    causation_id: str | None = None
    tenant_id: str | None = None
    ttl: int | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.urgency <= 1.0:
            raise ValueError(f"urgency must be between 0.0 and 1.0, got {self.urgency}")
        if self.domain not in KNOWN_DOMAINS:
            raise ValueError(
                f"Unknown domain {self.domain!r}. Known domains: {sorted(KNOWN_DOMAINS)}"
            )
        validate_event_type(self.event_type)

    @staticmethod
    def now() -> datetime:
        """Return the current UTC datetime (convenience for callers)."""
        return datetime.now(UTC)

    def to_dict(self) -> dict:
        """Serialise to a plain dictionary suitable for JSON or msgpack."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source": self.source,
            "payload": self.payload,
            "summary": self.summary,
            "urgency": self.urgency,
            "domain": self.domain,
            "timestamp": self.timestamp.isoformat(),
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "tenant_id": self.tenant_id,
            "ttl": self.ttl,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SleipnirEvent:
        """Deserialise from a plain dictionary."""
        ts = data["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        return cls(
            event_id=data["event_id"],
            event_type=data["event_type"],
            source=data["source"],
            payload=data["payload"],
            summary=data["summary"],
            urgency=data["urgency"],
            domain=data["domain"],
            timestamp=ts,
            correlation_id=data.get("correlation_id"),
            causation_id=data.get("causation_id"),
            tenant_id=data.get("tenant_id"),
            ttl=data.get("ttl"),
        )
