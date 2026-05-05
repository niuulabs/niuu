"""Domain models for Ravn."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

from niuu.domain.mimir import (  # noqa: F401 — re-exported for existing importers
    MimirLintReport,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
)

if TYPE_CHECKING:
    from ravn.domain.events import RavnEvent

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OutputMode(StrEnum):
    """Output mode for initiative (drive loop) tasks."""

    SILENT = "silent"  # agent runs, memory records, nothing delivered
    AMBIENT = "ambient"  # published to Sleipnir for attention model to route
    SURFACE = "surface"  # delivered directly via configured channel (Telegram etc.)


class TodoStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"


class Outcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    INTERRUPTED = "interrupted"


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"


class StreamEventType(StrEnum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    MESSAGE_DONE = "message_done"
    THINKING = "thinking"  # Extended thinking block content (Anthropic-only)


# ---------------------------------------------------------------------------
# Episodic memory models
# ---------------------------------------------------------------------------


@dataclass
class Episode:
    """A single recorded episode — what happened during one agent turn."""

    episode_id: str
    session_id: str
    timestamp: datetime
    summary: str
    task_description: str
    tools_used: list[str]
    outcome: Outcome
    tags: list[str]
    embedding: list[float] | None = None
    # Outcome fields merged from task_outcomes (NIU-574)
    reflection: str | None = None
    errors: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    duration_seconds: float | None = None
    # NIU-594: structured outcome parsed from ---outcome--- block
    structured_outcome: dict[str, Any] | None = None
    outcome_valid: bool = False


@dataclass(frozen=True)
class EpisodeMatch:
    """An episode returned by a memory query, annotated with its relevance score."""

    episode: Episode
    relevance: float


@dataclass(frozen=True)
class SessionSummary:
    """A summary of all episodes from a single session, returned by session search."""

    session_id: str
    summary: str
    episode_count: int
    last_active: datetime
    tags: list[str]


@dataclass
class SharedContext:
    """Shared blackboard context injected into a memory adapter from external regions."""

    data: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Skill models (NIU-436)
# ---------------------------------------------------------------------------


@dataclass
class Skill:
    """A reusable procedure extracted from successful episode patterns.

    Skills are Markdown documents with YAML frontmatter describing conditions
    for applicability.  They are discovered automatically when N successful
    episodes share the same tool/tag patterns.
    """

    skill_id: str
    name: str
    description: str
    content: str  # Markdown body with YAML frontmatter
    requires_tools: list[str]
    fallback_for_tools: list[str]
    source_episodes: list[str]  # episode_ids that triggered skill creation
    created_at: datetime
    success_count: int = 0


# ---------------------------------------------------------------------------
# Todo domain models
# ---------------------------------------------------------------------------


@dataclass
class TodoItem:
    """A single todo item in the agent's task list."""

    id: str
    content: str
    status: TodoStatus = TodoStatus.PENDING
    priority: int = 0


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TokenUsage:
    """Token usage for a single LLM call or cumulative across a session.

    ``thinking_tokens`` tracks the subset of ``output_tokens`` consumed by
    extended-thinking blocks (Anthropic-only).  They are already included in
    ``output_tokens``; this field provides a separate breakdown for cost
    accounting (Bifrost).
    """

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    thinking_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            thinking_tokens=self.thinking_tokens + other.thinking_tokens,
        )


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation requested by the LLM."""

    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ToolResult:
    """The result of executing a tool."""

    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Message:
    """A single message in a conversation."""

    role: str  # "user" or "assistant"
    content: str | list[dict]


@dataclass(frozen=True)
class LLMResponse:
    """A complete (non-streaming) response from the LLM."""

    content: str
    tool_calls: list[ToolCall]
    stop_reason: StopReason
    usage: TokenUsage


@dataclass(frozen=True)
class StreamEvent:
    """A single event from the LLM streaming API."""

    type: StreamEventType
    text: str | None = None
    tool_call: ToolCall | None = None
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class TurnResult:
    """The result of a single agent turn."""

    response: str
    tool_calls: list[ToolCall]
    tool_results: list[ToolResult]
    usage: TokenUsage
    # NIU-594: episode recorded for this turn (None if no memory configured and no outcome block)
    episode: Episode | None = None


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass
class Session:
    """A Ravn conversation session."""

    id: UUID = field(default_factory=uuid4)
    messages: list[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    turn_count: int = 0
    total_usage: TokenUsage = field(
        default_factory=lambda: TokenUsage(input_tokens=0, output_tokens=0)
    )
    todos: list[TodoItem] = field(default_factory=list)

    def add_message(self, message: Message) -> None:
        self.messages.append(message)

    def record_turn(self, usage: TokenUsage) -> None:
        self.turn_count += 1
        self.total_usage = self.total_usage + usage

    def upsert_todo(self, item: TodoItem) -> None:
        """Insert or replace a todo item by id."""
        for idx, existing in enumerate(self.todos):
            if existing.id == item.id:
                self.todos[idx] = item
                return
        self.todos.append(item)

    def remove_todo(self, todo_id: str) -> bool:
        """Remove a todo item by id. Returns True if found and removed."""
        before = len(self.todos)
        self.todos = [t for t in self.todos if t.id != todo_id]
        return len(self.todos) < before

    def clear_todos(self) -> None:
        """Remove all todo items (call at task start)."""
        self.todos.clear()


# ---------------------------------------------------------------------------
# Sleipnir event envelope (NIU-438)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SleipnirEnvelope:
    """Routing envelope for publishing RavnEvents to the ODIN event backbone.

    Wraps the existing RavnEvent unchanged and adds routing metadata required
    by the Valkyrie attention model and downstream consumers.

    Attributes:
        event:          The domain event, unchanged.
        source_agent:   Agent instance ID (from config or socket.gethostname()).
        session_id:     Session this event belongs to.
        task_id:        Drive-loop task ID, or None for interactive turns.
        urgency:        0.0–1.0 hint for the Valkyrie attention model.
        correlation_id: Groups related events within a task/session.
        published_at:   UTC timestamp of publication.
    """

    event: RavnEvent
    source_agent: str
    session_id: str
    task_id: str | None
    urgency: float
    correlation_id: str
    published_at: datetime


# ---------------------------------------------------------------------------
# Initiative / drive loop models (NIU-539)
# ---------------------------------------------------------------------------


@dataclass
class AgentTask:
    """A task enqueued by the drive loop for autonomous execution.

    Created by trigger adapters and consumed by DriveLoop._task_executor.
    The ``session_id`` is auto-generated as ``daemon_{task_id}`` so that
    episodic memory records for drive-loop turns are distinguishable from
    human-initiated sessions.
    """

    task_id: str  # "task_{hex_timestamp}_{counter}" — unique, stable
    title: str
    initiative_context: str  # the synthetic "message" given to the agent
    triggered_by: str  # "cron:morning_check", "event:tyr.raid.stalled"
    output_mode: OutputMode
    persona: str | None = None
    priority: int = 10  # lower = higher priority
    max_tokens: int | None = None
    deadline: datetime | None = None  # task discarded if queue time exceeds this
    output_path: Path | None = None  # where to save task output (cron tasks)
    root_correlation_id: str = ""  # Propagated from triggering event for fan-in chain tracking
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    session_id: str = field(init=False)

    def __post_init__(self) -> None:
        self.session_id = f"daemon_{self.task_id}"


# ---------------------------------------------------------------------------
# Flock discovery models (NIU-538)
# ---------------------------------------------------------------------------


@dataclass
class RavnCandidate:
    """Pre-handshake peer candidate discovered via mDNS or K8s (unverified).

    Carries enough information to attempt a handshake but has not yet proven
    realm membership.  ``realm_id_hash`` is SHA-256(realm_key)[:16] — the raw
    secret is never transmitted.
    """

    peer_id: str
    realm_id_hash: str  # SHA-256(realm_key)[:16] — not the raw secret
    host: str
    rep_address: str | None  # nng REP address
    pub_address: str | None  # nng PUB address
    handshake_port: int | None  # temp nng PAIR port for HMAC exchange
    metadata: dict = field(default_factory=dict)  # raw TXT records / pod annotations


@dataclass
class RavnIdentity:
    """This Ravn instance's own identity — announced to the flock.

    ``rep_address`` and ``pub_address`` are set by the active mesh adapter
    on startup so that peers know where to connect.
    """

    peer_id: str
    realm_id: str  # raw realm secret (never transmitted — hashed before announcing)
    persona: str
    capabilities: list[str]
    permission_mode: str  # read_only | workspace_write | full_access
    version: str
    consumes_event_types: list[str] = field(
        default_factory=list
    )  # event types this persona handles
    emits_event_types: list[str] = field(default_factory=list)
    rep_address: str | None = None  # nng REP address for mesh.send()
    pub_address: str | None = None  # nng PUB address for mesh.send()
    spiffe_id: str | None = None  # infra mode only
    sleipnir_routing_key: str | None = None  # for SleipnirMeshAdapter routing


@dataclass
class RavnPeer(RavnIdentity):
    """A verified (or pending/rejected) flock member.

    Extends ``RavnIdentity`` with trust metadata and liveness state.
    ``status`` and ``task_count`` are updated via heartbeat so that the
    cascade coordinator can pick idle peers.
    """

    trust_level: Literal["verified", "unverified", "rejected"] = "unverified"
    first_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(UTC))
    latency_ms: float | None = None
    status: Literal["idle", "busy"] = "idle"
    task_count: int = 0
