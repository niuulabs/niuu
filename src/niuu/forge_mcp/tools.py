"""Forge MCP tool definitions and dispatch, independent of the MCP transport.

The Skuld broker serves these tools to its own session over stdio (via
``python -m skuld.forge_mcp``); Forge can later serve the same toolbox over
Streamable HTTP for external agents. A transport only has to turn
:meth:`ForgeMcpToolbox.list_tools` / :meth:`ForgeMcpToolbox.call` into protocol
frames.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from niuu.cap_telemetry import record_cap_hit
from niuu.domain.artifact_reports import ARTIFACT_REPORT_TYPES, ArtifactReport
from niuu.domain.notifications import (
    AGENT_KINDS,
    ASK_DEFAULT_NONE,
    ASK_ITEM_ID_PATTERN,
    MAX_ASK_BYTES,
    MAX_ASK_HEADER_CHARS,
    MAX_ASK_ITEM_ID_CHARS,
    MAX_ASK_ITEMS,
    MAX_ASK_OPTION_DESCRIPTION_CHARS,
    MAX_ASK_OPTION_LABEL_CHARS,
    MAX_ASK_OPTIONS,
    MAX_ASK_TEXT_CHARS,
    MAX_BODY_CHARS,
    MAX_CORRELATION_ID_CHARS,
    MAX_IMAGE_ALT_CHARS,
    MAX_IMAGE_URL_CHARS,
    MAX_LINK_LABEL_CHARS,
    MAX_LINK_URL_CHARS,
    MAX_LINKS,
    MAX_RESOLVES,
    MAX_SPEAK_TEXT_CHARS,
    MAX_TITLE_CHARS,
    PRESENTED_FILE_ID_PATTERN,
    QUESTION_REF_PATTERN,
    NotificationDraft,
    NotificationKind,
    NotificationLinkKind,
    NotificationSeverity,
    NotificationSource,
    QuestionAction,
    SpeakPriority,
)
from niuu.domain.peer_envelope import derive_message_id
from niuu.domain.peer_message import (
    REPLY_TOOL,
    PeerAddress,
    PeerKind,
    PeerMessageError,
    ReplyKind,
)
from niuu.domain.session_description import (
    DESCRIPTION_FIELDS,
    DESCRIPTION_MAX_CHARS,
    SHORT_DESCRIPTION_MAX_CHARS,
)
from niuu.domain.workflow_step import (
    STEP_ENDINGS,
    WAITING_ON_KINDS,
    WORKFLOW_STEP_CLOSE_TOOL,
    WorkflowStepCloseArgs,
    WorkflowStepCloseError,
)
from niuu.forge_mcp.guidance import (
    COMPACT_NOTIFICATION_GUIDANCE,
    NOTIFICATION_IMAGE_GUIDANCE,
    NOTIFICATION_TRIGGER_GUIDANCE,
    QUESTION_GUIDANCE,
    QUESTION_STANDARD,
)
from niuu.forge_mcp.models import (
    SECRET_NAME_MAX_CHARS,
    SECRET_NAME_PATTERN,
    AskOutcome,
    ForgeApiError,
    ForgeMcpGrant,
    ForgeMcpLimits,
    ForgeMcpNotifyMode,
    SecretRequestError,
    ToolResult,
)
from niuu.forge_mcp.ports import ForgeClient, ForgeMcpHost
from niuu.forge_mcp.question_lint import LintedNotification, lint_question

# Wire-format patterns Forge itself enforces (request ids) or that keep ids from
# being spliced into a REST path; they are protocol shape, not tunables.
_REQUEST_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,200}$"
_ROLE_PATTERN = r"^[a-z][a-z0-9_-]{0,39}$"
_INSTANCE_ID_PATTERN = r"^[A-Za-z0-9._:-]{1,100}$"
_MCP_REQUEST_ID_PREFIX = "fmcp-"
#: The status ``send_mesh_message`` raises with when the peer's Skuld did not answer the RPC.
_MESH_RPC_TIMEOUT_STATUS = 504
_TRUNCATION_MARK = "…"


class UnknownToolError(KeyError):
    """The requested tool does not exist (a protocol error, not a tool failure)."""


# --------------------------------------------------------------------------- arguments


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _uuid_text(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError as exc:
        raise ValueError("must be a Forge session UUID") from exc


class _SessionRef(_Args):
    session_id: str
    instance_id: str | None = Field(default=None, pattern=_INSTANCE_ID_PATTERN)

    @field_validator("session_id")
    @classmethod
    def _session_uuid(cls, value: str) -> str:
        return _uuid_text(value)


class _NoArgs(_Args):
    pass


class _NotifyArgs(NotificationDraft):
    model_config = ConfigDict(extra="forbid")

    @field_validator("kind")
    @classmethod
    def _agent_kind(cls, value: NotificationKind) -> NotificationKind:
        if value not in AGENT_KINDS:
            allowed = ", ".join(sorted(kind.value for kind in AGENT_KINDS))
            raise ValueError(
                f"agents may raise only: {allowed} (reply_ready is reserved for Forge)"
            )
        return value


class _FeedNotifyArgs(_NotifyArgs):
    idempotency_key: str = Field(pattern=_REQUEST_ID_PATTERN)
    session_id: str | None = None
    instance_id: str | None = Field(default=None, pattern=_INSTANCE_ID_PATTERN)

    @field_validator("session_id")
    @classmethod
    def _session_uuid(cls, value: str | None) -> str | None:
        return None if value is None else _uuid_text(value)


class _ListNotificationsArgs(_Args):
    limit: int | None = Field(default=None, ge=1)
    before: int | None = Field(default=None, ge=0)
    after: int | None = Field(default=None, ge=0)
    kinds: list[NotificationKind] | None = None
    min_severity: NotificationSeverity | None = None
    sources: list[NotificationSource] | None = None
    session_id: str | None = None
    project_id: str | None = None
    unread: bool | None = None
    # LEX-1442: an agent checking "what is still open" should not see incidents that
    # are over. Ask for unresolved=false (or null) to see resolved ones too.
    unresolved: bool | None = True

    @field_validator("session_id")
    @classmethod
    def _session_uuid(cls, value: str | None) -> str | None:
        return None if value is None else _uuid_text(value)


class _ListSessionsArgs(_Args):
    limit: int | None = Field(default=None, ge=1)
    status: str | None = Field(default=None, pattern=r"^[a-z_]{1,32}$")
    project_id: str | None = None
    role: str | None = Field(default=None, pattern=_ROLE_PATTERN)
    parent_session_id: str | None = None
    scope: str = Field(default="guild", pattern=r"^(guild|local)$")
    include_archived: bool = False


class _TranscriptArgs(_SessionRef):
    turns: int | None = Field(default=None, ge=1)


class _SendMessageArgs(_Args):
    session_id: str | None = None
    to: str | None = Field(default=None, min_length=1)
    instance_id: str | None = Field(default=None, pattern=_INSTANCE_ID_PATTERN)
    content: str = Field(min_length=1)
    request_id: str | None = Field(default=None, pattern=_REQUEST_ID_PATTERN)
    urgent: bool = False

    @field_validator("session_id")
    @classmethod
    def _session_uuid(cls, value: str | None) -> str | None:
        return None if value is None else _uuid_text(value)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

    @model_validator(mode="after")
    def _one_address(self) -> _SendMessageArgs:
        if (self.session_id is None) == (self.to is None):
            raise ValueError(
                "name the recipient with exactly one of `to` (its session name) or "
                "`session_id` (its id)"
            )
        return self


class _AskArgs(_SendMessageArgs):
    deadline_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class _ReplyArgs(_Args):
    message_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    request_id: str | None = Field(default=None, pattern=_REQUEST_ID_PATTERN)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class _RoomPostArgs(_Args):
    room_id: str = Field(min_length=1, max_length=200)
    host_session_id: str
    content: str = Field(min_length=1)

    @field_validator("host_session_id")
    @classmethod
    def _session_uuid(cls, value: str) -> str:
        return _uuid_text(value)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class _MeshSendArgs(_Args):
    peer: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    expect_reply: bool = False
    request_id: str | None = Field(default=None, pattern=_REQUEST_ID_PATTERN)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class _MeshPublishArgs(_Args):
    topic: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    correlation_id: str = Field(default="", max_length=200)
    in_reply_to: str = Field(default="", max_length=200)
    request_id: str | None = Field(default=None, pattern=_REQUEST_ID_PATTERN)

    @field_validator("topic")
    @classmethod
    def _topic_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("topic must not be blank")
        return value.strip()

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class _MeshPeersArgs(_Args):
    role: str = Field(default="", max_length=200)
    capability: str = Field(default="", max_length=200)
    consumes: str = Field(default="", max_length=200)
    produces: str = Field(default="", max_length=200)


class _MeshAskArgs(_Args):
    content: str = Field(min_length=1)
    producers_of: str = Field(default="", max_length=200)
    peers: list[str] = Field(default_factory=list)
    wait_s: float = Field(default=0, ge=0)
    request_id: str | None = Field(default=None, pattern=_REQUEST_ID_PATTERN)

    @field_validator("content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

    @field_validator("peers")
    @classmethod
    def _peers_named_once(cls, value: list[str]) -> list[str]:
        if any(not peer.strip() for peer in value):
            raise ValueError("peers must be peer ids")
        if len(set(value)) != len(value):
            raise ValueError("peers must not repeat a peer")
        return value

    @model_validator(mode="after")
    def _exactly_one_audience(self) -> _MeshAskArgs:
        if bool(self.producers_of.strip()) == bool(self.peers):
            raise ValueError("give exactly one of producers_of or peers")
        return self


class _MeshFollowArgs(_Args):
    topic: str = Field(default="", max_length=200)
    correlation_id: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _exactly_one(self) -> _MeshFollowArgs:
        if bool(self.topic.strip()) == bool(self.correlation_id.strip()):
            raise ValueError("give exactly one of topic or correlation_id")
        return self


class _MessageStatusArgs(_SessionRef):
    request_id: str = Field(pattern=_REQUEST_ID_PATTERN)


class _CreateSessionArgs(_Args):
    name: str = Field(min_length=1)
    initial_prompt: str = ""
    model: str = ""
    definition: str | None = None
    launch_spec: str | None = None
    repo: str | None = None
    branch: str | None = None
    local_path: str | None = None
    system_prompt: str = ""
    project_id: str | None = None
    role: str | None = Field(default=None, pattern=_ROLE_PATTERN)
    parent_session_id: str | None = None
    parent_instance_id: str | None = None
    objective: str = Field(default="", max_length=4000)
    standalone: bool = False

    @field_validator("local_path")
    @classmethod
    def _absolute(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("/"):
            raise ValueError("local_path must be absolute")
        return value


class _DescribeSessionArgs(_Args):
    short_description: str | None = None
    description: str | None = None

    def changes(self) -> dict[str, str | None]:
        return {
            name: getattr(self, name)
            for name in DESCRIPTION_FIELDS
            if name in self.model_fields_set
        }


# --------------------------------------------------------------------------- schemas

_SESSION_ID_SCHEMA = {
    "type": "string",
    "format": "uuid",
    "description": "Forge session UUID (the `id` from list_sessions).",
}
_INSTANCE_ID_SCHEMA = {
    "type": "string",
    "pattern": _INSTANCE_ID_PATTERN,
    "description": "Guild instance that owns the session (the `instance_id` from list_sessions).",
}
_REQUEST_ID_SCHEMA = {"type": "string", "pattern": _REQUEST_ID_PATTERN}


def _object(properties: dict[str, Any], required: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_ASK_SCHEMA = _object(
    {
        "items": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_ASK_ITEMS,
            "description": "Items with unique stable ids, in answer order.",
            "items": _object(
                {
                    "id": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_ASK_ITEM_ID_CHARS,
                        "pattern": ASK_ITEM_ID_PATTERN,
                    },
                    "kind": {"type": "string", "enum": ["question", "action"]},
                    "text": {"type": "string", "minLength": 1, "maxLength": MAX_ASK_TEXT_CHARS},
                    "header": {"type": "string", "minLength": 1, "maxLength": MAX_ASK_HEADER_CHARS},
                    "options": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_ASK_OPTIONS,
                        "description": "Choices with unique, nonblank labels within this item.",
                        "items": _object(
                            {
                                "label": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_ASK_OPTION_LABEL_CHARS,
                                },
                                "description": {
                                    "type": "string",
                                    "maxLength": MAX_ASK_OPTION_DESCRIPTION_CHARS,
                                },
                                "recommended": {
                                    "type": "boolean",
                                    "description": "Your recommendation; mark exactly one.",
                                },
                            },
                            required=("label",),
                        ),
                    },
                    "multi_select": {"type": "boolean", "default": False},
                    "allow_text": {"type": "boolean", "default": True},
                    "default": {
                        **_object(
                            {
                                "option": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": MAX_ASK_OPTION_LABEL_CHARS,
                                    "description": f"An option label, or {ASK_DEFAULT_NONE!r}.",
                                },
                                "after": {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": 64,
                                    "description": (
                                        "ISO-8601 timestamp with timezone, or a duration "
                                        "from now (PT4H, P1D)."
                                    ),
                                },
                            },
                            required=("option", "after"),
                        ),
                        "description": "What happens if the user does not answer, and when.",
                    },
                    "deadline": {
                        "type": "string",
                        "maxLength": 64,
                        "description": "Optional ISO-8601 timestamp the answer is needed by.",
                    },
                },
                required=("id", "kind", "text"),
            ),
        },
    },
    required=("items",),
)
_ASK_SCHEMA["description"] = (
    f"Structured question/action items; requires question=true. At most {MAX_ASK_BYTES} "
    "UTF-8 bytes as compact JSON. An action asks the user to report completion, never executes it."
)
_ASK_SCHEMA["properties"]["items"]["items"]["allOf"] = [
    {
        "if": {
            "properties": {"kind": {"const": "question"}, "allow_text": {"const": False}},
            "required": ["kind", "allow_text"],
        },
        "then": {"required": ["options"]},
    }
]

_NOTIFY_SCHEMA = _object(
    {
        "kind": {
            "type": "string",
            "enum": sorted(kind.value for kind in AGENT_KINDS),
            "description": (
                "milestone = meaningful work finished; decision = the user must choose; "
                "attention = the user must act or unblock; error = a failure the user must "
                "know about; info = a brief FYI (including a choice you already made). "
                "decision and attention require question=true and an ask with 2-4 options, "
                "one recommended, and a default; without an ask, a question in the title or "
                "options in the body, they are recorded as info."
            ),
        },
        "severity": {
            "type": "string",
            "enum": [severity.value for severity in NotificationSeverity],
            "default": NotificationSeverity.INFO.value,
            "description": "info (default), success, warning (needs a look) or critical.",
        },
        "title": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_TITLE_CHARS,
            "description": "Key outcome or required action, standalone (aim <=72 characters).",
        },
        "body": {
            "type": "string",
            "maxLength": MAX_BODY_CHARS,
            "description": (
                "Optional one short sentence of new context/next action (aim <=160 characters). "
                "Never repeat the title or paste a report."
            ),
        },
        "links": {
            "type": "array",
            "maxItems": MAX_LINKS,
            "description": "Artifacts to open: PRs, URLs, presented files, other sessions.",
            "items": _object(
                {
                    "label": {"type": "string", "minLength": 1, "maxLength": MAX_LINK_LABEL_CHARS},
                    "url": {"type": "string", "maxLength": MAX_LINK_URL_CHARS},
                    "kind": {
                        "type": "string",
                        "enum": [kind.value for kind in NotificationLinkKind],
                        "default": NotificationLinkKind.URL.value,
                    },
                    "file_id": {
                        "type": "string",
                        "maxLength": MAX_LINK_LABEL_CHARS,
                        "description": "The file_id present-file returned, for kind=file.",
                    },
                },
                required=("label",),
            ),
        },
        "correlation_id": {
            "type": "string",
            "maxLength": MAX_CORRELATION_ID_CHARS,
            "description": (
                "One incident = one id (a ticket or PR number) on every notification about "
                "it. A later milestone with the same id resolves the incident's attention, "
                "error and critical notifications."
            ),
        },
        "image": {
            **_object(
                {
                    "file_id": {
                        "type": "string",
                        "pattern": PRESENTED_FILE_ID_PATTERN,
                        "description": "The file_id present-file returned for the image.",
                    },
                    "url": {
                        "type": "string",
                        "pattern": "^https://",
                        "maxLength": MAX_IMAGE_URL_CHARS,
                        "description": "An https:// URL of an image already online.",
                    },
                    "alt": {
                        "type": "string",
                        "maxLength": MAX_IMAGE_ALT_CHARS,
                        "description": "Short description of what the image shows.",
                    },
                }
            ),
            "description": (
                "Optional image shown with the notification: exactly one of file_id "
                "(from present-file) or url, plus alt text. Forge keeps its own copy."
            ),
        },
        "speak": {
            **_object(
                {
                    "text": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_SPEAK_TEXT_CHARS,
                        "description": "What Lexi says aloud, written to be heard.",
                    },
                    "priority": {
                        "type": "string",
                        "enum": [priority.value for priority in SpeakPriority],
                        "default": SpeakPriority.NOW.value,
                        "description": "now (default) or next_window (the next good moment).",
                    },
                },
                required=("text",),
            ),
            "description": (
                "Optional: ask for this notification to be spoken aloud to the user on "
                "their phone, only when it is worth interrupting them by voice. Forge only "
                "records the intent; the user's phone decides whether and when to speak it."
            ),
        },
        "question": {
            "type": "boolean",
            "description": (
                "true: this notice asks the user a Question they must answer. It stays "
                "open in their Needs-you list until resolved. Omit it for announcements."
            ),
        },
        "ask": _ASK_SCHEMA,
        "resolves": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_RESOLVES,
            "items": {"type": "string", "minLength": 1, "maxLength": MAX_CORRELATION_ID_CHARS},
            "description": (
                "What this notification closes: notification ids, or correlation ids (every "
                "earlier notification of that incident). A milestone with a correlation_id "
                "already closes that incident's attention, error and critical items."
            ),
        },
        "about_question": _object(
            {
                "id": {
                    "type": "string",
                    "pattern": QUESTION_REF_PATTERN,
                    "description": (
                        "The host-qualified question ref question:<host>:<notification_id>, "
                        "e.g. question:thor:<uuid>. A bare uuid is refused."
                    ),
                },
                "action": {
                    "type": "string",
                    "enum": [action.value for action in QuestionAction],
                    "description": (
                        "remind, replace, resolved, withdrawn: only the session that asked; "
                        "relay: any session filed in the same project, on any host."
                    ),
                },
            },
            required=("id", "action"),
        ),
    },
    required=("kind", "title"),
)
# Keep the root an ordinary object schema: Claude Code 2.1.309 silently drops
# this whole tool from its catalog when the root includes an allOf conditional.
# NotificationDraft still enforces ask -> question=true before any side effect;
# the ask description advertises that constraint to every MCP client.

_NOTIFY_DESCRIPTION = """\
Notify the user about THIS session. The notification appears inline in the \
conversation, in the Forge Notifications feed across every session and host, and \
may reach the user's phone or chat through their delivery rules.

Link artifacts (PR URLs, files you presented with present-file via kind=file and \
file_id, other sessions) in `links`. Attach an image with `image: {file_id}` from \
present-file, or `image: {url}`, and add `alt` text.

Returns {notification_id, turn_id, session_seq, state}. state=committed means \
Forge recorded it; state=pending means it is saved in this session's durable log \
and will reach Forge automatically when Forge is reachable — do not resend it."""
_NOTIFY_DESCRIPTION += (
    "\n\n"
    + NOTIFICATION_TRIGGER_GUIDANCE
    + "\n\nThe question standard: "
    + QUESTION_STANDARD
    + "\n\n"
    + QUESTION_GUIDANCE
    + "\n\n"
    + COMPACT_NOTIFICATION_GUIDANCE
    + "\n\n"
    + NOTIFICATION_IMAGE_GUIDANCE
)


def _with_lint(result: dict[str, Any], linted: LintedNotification) -> dict[str, Any]:
    """The notify result plus the question-standard verdict, when there is one."""
    if not linted.warnings:
        return result
    out = {**result, "warnings": list(linted.warnings)}
    if linted.downgraded_from is not None:
        out["kind"] = linted.draft.kind.value
        out["requested_kind"] = linted.downgraded_from.value
    return out


_FEED_NOTIFY_SCHEMA = {
    **_NOTIFY_SCHEMA,
    "properties": {
        **_NOTIFY_SCHEMA["properties"],
        "idempotency_key": {
            "type": "string",
            "pattern": _REQUEST_ID_PATTERN,
            "description": (
                "Your own id for this notification. Retrying with the same key returns "
                "the notification already recorded instead of a second one."
            ),
        },
        "session_id": {
            **_SESSION_ID_SCHEMA,
            "description": (
                "The session the notification is about. Defaults to your own session "
                "when you are a Forge session; required otherwise."
            ),
        },
        "instance_id": _INSTANCE_ID_SCHEMA,
    },
    "required": [*_NOTIFY_SCHEMA["required"], "idempotency_key"],
}

_FEED_NOTIFY_DESCRIPTION = """\
Record a notification in the Forge Notifications feed for a session you own \
(your own session by default when you are a Forge session). It is feed-only: it \
is not added to the session's transcript. It appears in the feed across every \
host and may reach the owner's phone or chat through their delivery rules.

Link artifacts in `links`. Attach an image with `image: {file_id}` from \
present-file, or `image: {url}`, and add `alt` text.

idempotency_key is required: retrying with the same key returns the notification \
already recorded. Returns {notification_id, seq, session_id, state}."""
_FEED_NOTIFY_DESCRIPTION += (
    "\n\n"
    + NOTIFICATION_TRIGGER_GUIDANCE
    + "\n\nThe question standard: "
    + QUESTION_STANDARD
    + "\n\n"
    + QUESTION_GUIDANCE
    + "\n\n"
    + COMPACT_NOTIFICATION_GUIDANCE
    + "\n\n"
    + NOTIFICATION_IMAGE_GUIDANCE
)


@dataclass(frozen=True)
class ToolSpec:
    """One tool: its MCP metadata, argument model and required grant."""

    name: str
    description: str
    input_schema: dict[str, Any]
    args_model: type[BaseModel]
    grant: ForgeMcpGrant | None = None

    def to_mcp(self) -> dict[str, Any]:
        description = self.description
        if self.grant is not None:
            description += (
                f"\n\nRequires the '{self.grant.value}' grant; without it the call returns "
                "an error explaining how an operator can grant it."
            )
        return {"name": self.name, "description": description, "inputSchema": self.input_schema}


_DESCRIBE_SESSION_DESCRIPTION = (
    "Set what your own Forge session is doing, so anyone glancing at the session list "
    "(web, phone) can tell. Keep `short_description` current: set it when you start, "
    "whenever the phase of the work changes, when you are blocked ('Blocked on …') and "
    "when you finish ('Done: …'). One plain-text line naming the current step and the "
    f"goal; it is folded to one line and cut at {SHORT_DESCRIPTION_MAX_CHARS} characters. "
    "Use `description` for the fuller status someone opening the session would want "
    f"(markdown, up to {DESCRIPTION_MAX_CHARS} characters). Send only the keys you are "
    "changing: an omitted key is kept, null clears it. Setting the same text again is "
    "harmless. This is not a notification and interrupts no one."
)

_DESCRIBE_SESSION_SCHEMA = _object(
    {
        "short_description": {
            "type": ["string", "null"],
            "description": (
                "One line: what this session is doing now and its goal, e.g. "
                "'Adding retry to the upload client; tests next'. null clears it."
            ),
        },
        "description": {
            "type": ["string", "null"],
            "description": "Fuller status in markdown: progress, decisions, next steps. "
            "null clears it.",
        },
    }
)


class _ReportArtifactArgs(_Args):
    url: str
    title: str
    provider_version: str | None = None
    version_label: str | None = None
    type: str | None = None


_REPORT_ARTIFACT_DESCRIPTION = (
    "Report a claude.ai artifact this session made or updated, when Forge cannot see "
    "the publish itself (it was published another way, or your engine has no Artifact "
    "tool). Publishes made with the claude.ai Artifact tool, and files you present with "
    "present-file, are recorded automatically: do not report those. The report appears "
    "in the control plane's Artifacts list with this session as its producer. It is not "
    "a notification; use `notify` to tell the user about it. Reporting the same version "
    "again is harmless."
)

_REPORT_ARTIFACT_SCHEMA = _object(
    {
        "url": {
            "type": "string",
            "description": "The artifact's durable URL: https://claude.ai/artifact/<id>.",
        },
        "title": {"type": "string", "description": "The artifact's title, one line."},
        "provider_version": {
            "type": ["string", "null"],
            "description": "The provider's version string, when the publish returned one.",
        },
        "version_label": {
            "type": ["string", "null"],
            "description": "A human label for this version (e.g. 'v21'), if you use one.",
        },
        "type": {
            "type": ["string", "null"],
            "enum": [*ARTIFACT_REPORT_TYPES, None],
            "description": "What kind of artifact it is, when you know.",
        },
    },
    required=("url", "title"),
)


class _RequestSecretArgs(_Args):
    name: str = Field(pattern=SECRET_NAME_PATTERN, max_length=SECRET_NAME_MAX_CHARS)
    purpose: str = Field(min_length=1)
    target: str | None = Field(default=None, min_length=1)


_REQUEST_SECRET_DESCRIPTION = (
    "Ask the user for a secret (an API key, token or password) you need. Use this instead "
    "of ever asking them to paste a secret into the chat: the chat shows them a masked "
    "field that goes straight to Forge, so the value never enters the transcript or your "
    "context. The call waits until they answer. On success the value is in a private file "
    "and you get only its `path`: refer to the secret by that path, e.g. "
    '`curl -H "Authorization: Bearer $(cat <path>)" …` inside a command. Never echo, '
    "print, log or copy it into a file you present. `name` is the identifier you will "
    "know it by (UPPER_SNAKE_CASE, e.g. GITLAB_TOKEN); `purpose` tells the user what it is "
    "for; `target` optionally names the host or service it unlocks. Asking again for a "
    "name that is still open returns to the same request instead of a second card. "
    "Errors say if the user declined, the request was cancelled, or the wait ended while "
    "the request stays open (call again with the same name to keep waiting)."
)

_REQUEST_SECRET_SCHEMA = _object(
    {
        "name": {
            "type": "string",
            "pattern": SECRET_NAME_PATTERN,
            "maxLength": SECRET_NAME_MAX_CHARS,
            "description": "UPPER_SNAKE_CASE name of the secret; it becomes the file's name.",
        },
        "purpose": {
            "type": "string",
            "minLength": 1,
            "description": "Why you need it, in words the user will recognise.",
        },
        "target": {
            "type": ["string", "null"],
            "description": "The host or service it is for (e.g. gitlab.example.com), if any.",
        },
    },
    required=("name", "purpose"),
)

_WORKFLOW_STEP_CLOSE_DESCRIPTION = (
    "Close the workflow step you were activated for. Call it exactly once when the step's "
    "instructions say you are done (or cannot continue), naming the `activation_id` from "
    "those instructions. `ending` is one of: `verdict` (hand off: give a `verdict` from the "
    "step's valid verdicts and, when the step defines an outcome schema, an `outcome` that "
    "matches it), `waiting` (you are blocked on something that will resolve later: give "
    "`waiting_on`; the step stays open and you close it again when it resolves), `done` "
    "(the step is finished and hands nothing off) or `failed` (give `failure_reason`). Only "
    "the endings the step lists are allowed. Ending your turn without calling this tool "
    "leaves the step open and is reported as unclosed; nothing is inferred from your reply. "
    "A rejected call returns the exact reason and records nothing: fix the arguments and "
    "call again."
)

_WORKFLOW_STEP_CLOSE_SCHEMA = _object(
    {
        "activation_id": {
            "type": "string",
            "minLength": 1,
            "description": "The activation id given in the step's instructions.",
        },
        "ending": {"type": "string", "enum": list(STEP_ENDINGS)},
        "verdict": {
            "type": ["string", "null"],
            "description": "Required for ending=verdict; one of the step's valid verdicts.",
        },
        "outcome": {
            "type": ["object", "null"],
            "description": "The outcome fields the step's outcome schema defines.",
        },
        "note": {"type": ["string", "null"], "description": "A short note for the record."},
        "evidence": {
            "type": "array",
            "items": _object(
                {
                    "kind": {"type": "string", "minLength": 1},
                    "ref": {"type": "string", "minLength": 1},
                    "summary": {"type": "string"},
                },
                required=("kind", "ref"),
            ),
            "description": "What supports the ending: commits, files, test runs, links.",
        },
        "waiting_on": {
            "type": ["object", "null"],
            "description": "Required for ending=waiting: what the step is waiting for.",
            "properties": {
                "kind": {"type": "string", "enum": list(WAITING_ON_KINDS)},
                "ref": {"type": "string", "minLength": 1},
                "reason": {"type": "string"},
            },
            "required": ["kind", "ref"],
            "additionalProperties": False,
        },
        "failure_reason": {
            "type": ["string", "null"],
            "description": "Required for ending=failed: why the step cannot be completed.",
        },
    },
    required=("activation_id", "ending"),
)


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="environment",
        description=(
            "Describe where this session runs: Forge session id and name, host/node, "
            "workspace, engine and model, project, Forge URL, runtime version, attached "
            "MCP servers and the grants this session holds. Contains no secrets. Call it "
            "before referring to your own session or acting on other sessions."
        ),
        input_schema=_object({}),
        args_model=_NoArgs,
    ),
    ToolSpec(
        name="notify",
        description=_NOTIFY_DESCRIPTION,
        input_schema=_NOTIFY_SCHEMA,
        args_model=_NotifyArgs,
    ),
    ToolSpec(
        name="report_artifact",
        description=_REPORT_ARTIFACT_DESCRIPTION,
        input_schema=_REPORT_ARTIFACT_SCHEMA,
        args_model=_ReportArtifactArgs,
    ),
    ToolSpec(
        name="request_secret",
        description=_REQUEST_SECRET_DESCRIPTION,
        input_schema=_REQUEST_SECRET_SCHEMA,
        args_model=_RequestSecretArgs,
    ),
    ToolSpec(
        name=WORKFLOW_STEP_CLOSE_TOOL,
        description=_WORKFLOW_STEP_CLOSE_DESCRIPTION,
        input_schema=_WORKFLOW_STEP_CLOSE_SCHEMA,
        args_model=WorkflowStepCloseArgs,
    ),
    ToolSpec(
        name="describe_session",
        description=_DESCRIBE_SESSION_DESCRIPTION,
        input_schema=_DESCRIBE_SESSION_SCHEMA,
        args_model=_DescribeSessionArgs,
    ),
    ToolSpec(
        name="list_notifications",
        description=(
            "List Forge notifications you can see, newest first (bounded). Use it to check "
            "what was already reported before notifying again, or what other sessions "
            "raised. Page older items with `before` (the previous next_before) or catch up "
            "with `after` (a seq). Resolved notifications (an incident that is over) are "
            "left out unless you pass unresolved=false (only resolved) or null (all)."
        ),
        input_schema=_object(
            {
                "limit": {"type": "integer", "minimum": 1},
                "before": {"type": "integer", "minimum": 0},
                "after": {"type": "integer", "minimum": 0},
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": [k.value for k in NotificationKind]},
                },
                "min_severity": {
                    "type": "string",
                    "enum": [s.value for s in NotificationSeverity],
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string", "enum": [s.value for s in NotificationSource]},
                },
                "session_id": _SESSION_ID_SCHEMA,
                "project_id": {"type": "string"},
                "unread": {"type": "boolean"},
                "unresolved": {"type": ["boolean", "null"], "default": True},
            }
        ),
        args_model=_ListNotificationsArgs,
    ),
    ToolSpec(
        name="list_sessions",
        description=(
            "List Forge sessions you can see (bounded, newest activity first as Forge "
            "returns them), across every Guild host by default. Each item carries id and "
            "instance_id — pass both to the other session tools."
        ),
        input_schema=_object(
            {
                "limit": {"type": "integer", "minimum": 1},
                "status": {
                    "type": "string",
                    "description": "e.g. running, stopped, starting, failed.",
                },
                "project_id": {"type": "string"},
                "role": {"type": "string", "description": "e.g. coordinator or worker."},
                "parent_session_id": {
                    "type": "string",
                    "description": "Only sessions whose coordinator/parent is this session.",
                },
                "scope": {"type": "string", "enum": ["guild", "local"], "default": "guild"},
                "include_archived": {"type": "boolean", "default": False},
            }
        ),
        args_model=_ListSessionsArgs,
    ),
    ToolSpec(
        name="get_session",
        description="Get one Forge session's state: status, activity, model, source, project.",
        input_schema=_object(
            {"session_id": _SESSION_ID_SCHEMA, "instance_id": _INSTANCE_ID_SCHEMA},
            required=("session_id",),
        ),
        args_model=_SessionRef,
    ),
    ToolSpec(
        name="session_transcript",
        description=(
            "Read the tail of a session's conversation: the last `turns` turns, each "
            "truncated, with the tools each turn used. Use it to see what a peer session "
            "is doing before messaging it."
        ),
        input_schema=_object(
            {
                "session_id": _SESSION_ID_SCHEMA,
                "instance_id": _INSTANCE_ID_SCHEMA,
                "turns": {"type": "integer", "minimum": 1},
            },
            required=("session_id",),
        ),
        args_model=_TranscriptArgs,
    ),
    ToolSpec(
        name="send_message",
        description=(
            "Send a message to ONE specific other session that you already know you need. To "
            "put a question to whoever can answer it use `mesh_ask`, and to announce something "
            "use `mesh_publish`; this is for when only that session will do. The recipient "
            "reads it with a header the platform wrote: your session name and id, your node, "
            "the time and how to answer, so you cannot be mistaken for the user and nobody can "
            "forge who sent it. Name the recipient with `to` (its session name, resolved among "
            "your owner's sessions on every node of the Guild) or `session_id` (+ "
            "`instance_id`) from list_sessions. An unknown name is an error that names it; a "
            "name two sessions carry is an error that lists them (name, id, node): pick by id "
            "then. The message waits for the end of the recipient's current turn unless you "
            "pass `urgent`. The recipient answers with its `reply` tool, so ask a question when "
            "you want one and tell it what you need. Reuse the same request_id when retrying so "
            "it is never delivered twice (one is generated and returned if you omit it); check "
            "delivery with message_status. Who you can reach: any session your owner owns, on "
            "any node of your Guild (a message to another node is relayed by your node). "
            "Sessions of other owners are refused with the reason."
        ),
        input_schema=_object(
            {
                "to": {
                    "type": "string",
                    "description": "The recipient's session name (the `name` from list_sessions).",
                },
                "session_id": _SESSION_ID_SCHEMA,
                "instance_id": _INSTANCE_ID_SCHEMA,
                "content": {"type": "string", "minLength": 1},
                "request_id": _REQUEST_ID_SCHEMA,
                "urgent": {
                    "type": "boolean",
                    "default": False,
                    "description": "Interrupt the recipient's running turn instead of waiting "
                    "for its end. Use it only when waiting would do harm.",
                },
            },
            required=("content",),
        ),
        args_model=_SendMessageArgs,
        grant=ForgeMcpGrant.MESSAGE,
    ),
    ToolSpec(
        name="ask",
        description=(
            "Send a message to ONE specific other session and wait for its answer, instead of "
            "polling or saying you will wait. Use it only when you cannot go on without that "
            "session's answer; to put a question to the peers who can answer it and carry on, "
            "use `mesh_ask`. Addressing, the sender header and who you can reach are those of "
            "send_message. The recipient answers with its `reply` tool; the answer is the "
            "result of this call and is consumed by it, so it is not also delivered to you as a "
            "message later. Every other way the call can end is named in `outcome`, with "
            "isError set, and never an empty answer: `blocked` (the recipient waits on an "
            "approval or a question to a person), `gone` (it stopped, failed or was deleted), "
            "`not_started` (delivered, but it took no turn that read the message), "
            "`delivery_unknown` (Forge did not confirm the send; it may or may not have "
            "arrived) and `deadline` (it had not answered in time). The message stays sent in "
            "every case, and an answer that comes later is delivered to you at your next turn "
            "boundary as usual. To keep waiting, call ask again with the SAME request_id (it is "
            "returned in every result): the message is not sent twice. `deadline_s` is how long "
            "to wait; it defaults to, and may not exceed, ask.wait_max_s (the longest an MCP "
            "tool call may run here), and a larger one is refused with that named. A mesh peer "
            "is asked directly over the mesh, and its own state is read from it."
        ),
        input_schema=_object(
            {
                "to": {
                    "type": "string",
                    "description": "The recipient's session name (the `name` from list_sessions).",
                },
                "session_id": _SESSION_ID_SCHEMA,
                "instance_id": _INSTANCE_ID_SCHEMA,
                "content": {"type": "string", "minLength": 1},
                "request_id": {
                    **_REQUEST_ID_SCHEMA,
                    "description": "Reuse it to keep waiting for the same question.",
                },
                "urgent": {
                    "type": "boolean",
                    "default": False,
                    "description": "Interrupt the recipient's running turn instead of waiting "
                    "for its end. Use it only when waiting would do harm.",
                },
                "deadline_s": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Seconds to wait for the answer (default and ceiling: "
                    "ask.wait_max_s).",
                },
            },
            required=("content",),
        ),
        args_model=_AskArgs,
        grant=ForgeMcpGrant.MESSAGE,
    ),
    ToolSpec(
        name="message_status",
        description=(
            "Delivery status of a message sent with send_message (accepted, pending, "
            "delivered or failed). Delivered means the agent received it, not that it "
            "acted on it."
        ),
        input_schema=_object(
            {
                "session_id": _SESSION_ID_SCHEMA,
                "instance_id": _INSTANCE_ID_SCHEMA,
                "request_id": _REQUEST_ID_SCHEMA,
            },
            required=("session_id", "request_id"),
        ),
        args_model=_MessageStatusArgs,
        grant=ForgeMcpGrant.MESSAGE,
    ),
    ToolSpec(
        name=REPLY_TOOL,
        description=(
            "Answer a message another agent sent you, directly to its sender. Give the "
            "`message_id` from the message's header and your `content`. The answer goes to "
            "whoever sent that message, by the route the sender gave: back to their session, to "
            "their mesh peer, or into the room it was said in; nobody else sees it. When the "
            "message came from a mesh publication and others should see your answer too, "
            "publish it instead with `mesh_publish` and `in_reply_to` set to the message id: "
            "the asker receives it as well and everyone following the thread sees it. Text you "
            "write in your own turn does not reach the sender. A message sent as a notice takes "
            "no reply and this tool refuses it with the reason. Retries with the same "
            "request_id are never delivered twice."
        ),
        input_schema=_object(
            {
                "message_id": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The message id in the header of the message you answer.",
                },
                "content": {"type": "string", "minLength": 1},
                "request_id": _REQUEST_ID_SCHEMA,
            },
            required=("message_id", "content"),
        ),
        args_model=_ReplyArgs,
    ),
    ToolSpec(
        name="room_post",
        description=(
            "Say something in a room you are a member of. The room belongs to a session, its "
            "host: pass that session's id as `host_session_id` (the 'Relayed by' line of a message "
            "from the room shows it) and the `room_id` (the 'Said in room' line). The host records "
            "the message in the room as said by this session, with you named as the speaker, and "
            "refuses it, with the reason, when you are not a member of that room."
        ),
        input_schema=_object(
            {
                "room_id": {"type": "string", "minLength": 1},
                "host_session_id": {
                    **_SESSION_ID_SCHEMA,
                    "description": "The session that hosts the room.",
                },
                "content": {"type": "string", "minLength": 1},
            },
            required=("room_id", "host_session_id", "content"),
        ),
        args_model=_RoomPostArgs,
        grant=ForgeMcpGrant.MESSAGE,
    ),
    ToolSpec(
        name="mesh_send",
        description=(
            "Send a message directly to ONE specific mesh peer by its peer id, when you know "
            "you need that peer (a Valkyrie or another agent in your flock). To put a question "
            "to whoever can answer it use `mesh_ask`, and to announce something use "
            "`mesh_publish`. The platform stamps you as the sender: this session and your mesh "
            "peer id. Set `expect_reply` when you want an answer: the peer is then told how to "
            "reach you, and answers with its `reply` tool. Retries with the same request_id are "
            "never delivered twice. Listed only on a session whose mesh is enabled."
        ),
        input_schema=_object(
            {
                "peer": {"type": "string", "minLength": 1, "description": "The mesh peer id."},
                "content": {"type": "string", "minLength": 1},
                "expect_reply": {"type": "boolean", "default": False},
                "request_id": _REQUEST_ID_SCHEMA,
            },
            required=("peer", "content"),
        ),
        args_model=_MeshSendArgs,
    ),
    ToolSpec(
        name="mesh_peers",
        description=(
            "Look up the peers on your mesh and what each announced about itself: its name, "
            "persona, role, a description of what it does, capabilities, the topics it consumes "
            "and produces, and its current activity state. Use it when you want to choose peers "
            "yourself (for example to pick the `peers` of a `mesh_ask` by their description, or "
            "one specific peer to message); it is not a step before every message. To ask "
            "whoever produces a kind of knowledge or work, `mesh_ask(producers_of=<topic>)` "
            "resolves them for you, and `mesh_peers(produces=<topic>)` shows who that would be. "
            "It returns facts and nothing ranks them: the choice is yours. Optional filters are "
            "exact matches on one announced fact (role, capability, a topic consumed or "
            "produced); all given filters must match. A fact a peer's discovery did not carry "
            "is null and listed in `not_announced`: that is unknown, not an empty role. Peers "
            "are announced on a heartbeat, so check last_heartbeat before relying on "
            "activity_state. Listed only on a session whose mesh is enabled."
        ),
        input_schema=_object(
            {
                "role": {"type": "string", "description": "Only peers announcing this role."},
                "capability": {
                    "type": "string",
                    "description": "Only peers announcing this capability.",
                },
                "consumes": {
                    "type": "string",
                    "description": "Only peers that consume this topic.",
                },
                "produces": {
                    "type": "string",
                    "description": "Only peers that produce this topic.",
                },
            },
            required=(),
        ),
        args_model=_MeshPeersArgs,
    ),
    ToolSpec(
        name="mesh_ask",
        description=(
            "Ask a QUESTION of the peers best placed to answer it, instead of publishing an "
            "event. Name them one of two ways: `producers_of` a topic (the question goes to "
            "every peer that announces it produces that topic, because it is the source of that "
            "kind of knowledge or work; not to the topic's consumers, and your own `produces` "
            "list does not limit a question), or `peers`, a set of peer ids you chose from "
            "`mesh_peers` by their description. Matching is your judgment; the platform only "
            "resolves 'producers of X' from the peer table as facts. Each recipient gets the "
            "question directly, with a header saying who asked and how to answer. A question "
            "nobody can receive is refused with the reason: unlike an event, silence is never "
            "the outcome. A recipient that could not be reached is listed in the result. "
            "Answers are events correlated to the question: they come back as they come, from "
            "any number of recipients or from none, to your inbox at your turn boundaries, "
            "grouped by thread, and nothing waits. A recipient answers with `reply` (to you "
            "alone) or by publishing with `in_reply_to` (everyone following the thread sees it "
            "too). Pass `wait_s` to also wait, at most ask.wait_max_s: the call returns every "
            "answer so far (taken by the call, not delivered to you again) as soon as every "
            "peer the question reached has answered (`ended`: all_asked_answered), else at the "
            "deadline (`ended`: deadline). Answers from peers you did not ask are returned too "
            "and never end the wait early; one answerer counts once however it answered. "
            "Retries with the same request_id ask once. Listed only on a session whose mesh is "
            "enabled."
        ),
        input_schema=_object(
            {
                "content": {"type": "string", "minLength": 1, "description": "The question."},
                "producers_of": {
                    "type": "string",
                    "description": "Ask every peer that announces it produces this topic.",
                },
                "peers": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "description": "Ask exactly these peers (ids chosen from mesh_peers).",
                },
                "wait_s": {
                    "type": "number",
                    "minimum": 0,
                    "description": (
                        "Also wait this many seconds and return the answers so far. 0 (the "
                        "default) waits for nothing."
                    ),
                },
                "request_id": _REQUEST_ID_SCHEMA,
            },
            required=("content",),
        ),
        args_model=_MeshAskArgs,
    ),
    ToolSpec(
        name="mesh_subscribe",
        description=(
            "Start listening to a mesh topic, or follow a thread, without a persona change. "
            "Give a `topic` (what you want to hear, for example review.requested) or a "
            "`correlation_id` (the thread of a published question: you then see every answer "
            "published into it). It adds to your persona's `consumes`, which stays your "
            "standing subscription. What you follow survives a restart and is announced to your "
            "peers. Any topic may be followed; only publishing is limited to declared topics. "
            "What arrives lands in your inbox at your turn boundaries, so follow what you will "
            "act on. Following something you already follow is not an error. Listed only on a "
            "session whose mesh is enabled."
        ),
        input_schema=_object(
            {
                "topic": {
                    "type": "string",
                    "description": "A topic to listen to, e.g. review.requested.",
                },
                "correlation_id": {
                    "type": "string",
                    "description": "The thread to follow: its correlation id.",
                },
            },
            required=(),
        ),
        args_model=_MeshFollowArgs,
    ),
    ToolSpec(
        name="mesh_unsubscribe",
        description=(
            "Stop following a topic or thread you started following with `mesh_subscribe`. "
            "Give the same `topic` or `correlation_id`. Your persona's `consumes` is a standing "
            "subscription and cannot be unsubscribed this way; something you do not follow is an "
            "error that lists what you do. Listed only on a session whose mesh is enabled."
        ),
        input_schema=_object(
            {
                "topic": {"type": "string", "description": "The topic to stop listening to."},
                "correlation_id": {
                    "type": "string",
                    "description": "The thread to stop following.",
                },
            },
            required=(),
        ),
        args_model=_MeshFollowArgs,
    ),
    ToolSpec(
        name="mesh_publish",
        description=(
            "Publish an EVENT: announce something (a result, a hand-off, a status) on a topic "
            "your persona declares it produces, for whoever consumes that topic. Every session "
            "that follows the topic (its persona's `consumes`, or `mesh_subscribe`) receives it "
            "in its inbox: any number of them, or nobody. Having no subscribers is normal, not "
            "an error. You name no recipient and nothing waits: you get the event id back, and "
            "the answer is only that the mesh accepted the event. The topic must be one your "
            "persona declares it produces (or the session's mesh.publish_event_types); any "
            "other topic is refused with the declared list. This is not how you ask a question: "
            "to put a question to the peers who can answer it, use `mesh_ask`. Consumers may "
            "answer an event, and may also start something of their own later, as events too. "
            "An answer reaches your inbox as it comes, at your turn boundaries, grouped by "
            "thread. Answer an event or a question the same way: publish with `in_reply_to` set "
            "to its message id (and a topic you may publish); the asker receives it without "
            "having subscribed to anything, and everyone following the thread sees it. "
            "`correlation_id` names the thread; it defaults to the thread of the message you "
            "answer, else to the new event. The platform stamps you as the publisher. Retries "
            "with the same request_id publish the event once. For one specific peer you know "
            "you need, use `send_message`, `ask` or `mesh_send`. Listed only on a session whose "
            "mesh is enabled."
        ),
        input_schema=_object(
            {
                "topic": {
                    "type": "string",
                    "minLength": 1,
                    "description": "The event type, e.g. review.requested.",
                },
                "content": {"type": "string", "minLength": 1},
                "correlation_id": {
                    "type": "string",
                    "description": "Optional thread tag consumers see; defaults to the event id.",
                },
                "in_reply_to": {
                    "type": "string",
                    "description": "Optional: the event or message id this publication answers.",
                },
                "request_id": _REQUEST_ID_SCHEMA,
            },
            required=("topic", "content"),
        ),
        args_model=_MeshPublishArgs,
    ),
    ToolSpec(
        name="create_session",
        description=(
            "Create and start a new Forge session. Returns its launch state — creation is "
            "not completion; follow it with get_session and session_transcript. Give "
            "either repo (+branch) or an absolute local_path as the workspace. When you "
            "belong to a Forge project, the new session joins it as your worker (you are "
            "its parent) unless you pass standalone, another project_id or a parent."
        ),
        input_schema=_object(
            {
                "name": {
                    "type": "string",
                    "description": "Lowercase RFC 1123 label (a-z, 0-9, hyphens), max 63.",
                },
                "initial_prompt": {"type": "string", "description": "The first user message."},
                "model": {"type": "string"},
                "definition": {
                    "type": "string",
                    "description": "Session definition, e.g. skuldClaude or skuldCodex.",
                },
                "launch_spec": {"type": "string"},
                "repo": {"type": "string"},
                "branch": {"type": "string"},
                "local_path": {"type": "string"},
                "system_prompt": {"type": "string"},
                "project_id": {
                    "type": "string",
                    "description": "Project to join. Defaults to your own project.",
                },
                "role": {
                    "type": "string",
                    "description": "Project role, default worker (coordinator for a new lead).",
                },
                "parent_session_id": {
                    "type": "string",
                    "description": "Coordinator of the new session. Defaults to you.",
                },
                "parent_instance_id": {"type": "string"},
                "objective": {
                    "type": "string",
                    "description": "The assignment, recorded with the session's membership.",
                },
                "standalone": {
                    "type": "boolean",
                    "default": False,
                    "description": "Create an independent session outside any project.",
                },
            },
            required=("name",),
        ),
        args_model=_CreateSessionArgs,
        grant=ForgeMcpGrant.LIFECYCLE,
    ),
    ToolSpec(
        name="start_session",
        description="Start (or resume) a stopped Forge session.",
        input_schema=_object(
            {"session_id": _SESSION_ID_SCHEMA, "instance_id": _INSTANCE_ID_SCHEMA},
            required=("session_id",),
        ),
        args_model=_SessionRef,
        grant=ForgeMcpGrant.LIFECYCLE,
    ),
    ToolSpec(
        name="stop_session",
        description="Stop another Forge session. Its workspace and transcript are kept.",
        input_schema=_object(
            {"session_id": _SESSION_ID_SCHEMA, "instance_id": _INSTANCE_ID_SCHEMA},
            required=("session_id",),
        ),
        args_model=_SessionRef,
        grant=ForgeMcpGrant.LIFECYCLE,
    ),
)

TOOL_NAMES: tuple[str, ...] = tuple(spec.name for spec in TOOL_SPECS)

#: ``notify`` as the Forge-hosted HTTP endpoint serves it (direct feed submit).
FEED_NOTIFY_SPEC = ToolSpec(
    name="notify",
    description=_FEED_NOTIFY_DESCRIPTION,
    input_schema=_FEED_NOTIFY_SCHEMA,
    args_model=_FeedNotifyArgs,
)


#: Tools that write the caller's own durable transcript (or, for ``request_secret``, put a
#: card in it and a file in the session's runtime), or that act through the caller's own broker
#: (``reply`` reads its inbox, ``room_post`` speaks as the session, ``mesh_send``,
#: ``mesh_publish``, ``mesh_peers`` and ``mesh_subscribe`` use its mesh),
#: so only a session's own broker (transcript mode) can serve them.
TRANSCRIPT_ONLY_TOOLS = frozenset(
    {
        "report_artifact",
        "request_secret",
        "ask",
        WORKFLOW_STEP_CLOSE_TOOL,
        REPLY_TOOL,
        "room_post",
        "mesh_send",
        "mesh_publish",
        "mesh_peers",
        "mesh_subscribe",
        "mesh_unsubscribe",
        "mesh_ask",
    }
)

#: Tools listed only on a session whose host says it can use them, with the reason a call is
#: refused otherwise. They are listed for the whole session, so every engine sees them whether
#: or not it honours tool-list change notifications; the host refuses a call it cannot act on.
_HOST_GATES: dict[str, tuple[Callable[[ForgeMcpHost], bool], str]] = {
    WORKFLOW_STEP_CLOSE_TOOL: (
        lambda host: host.workflow_step_host(),
        "this session is not a workflow step host",
    ),
    "mesh_send": (
        lambda host: host.mesh_host(),
        "this session has no mesh (mesh.enabled is false)",
    ),
    "mesh_publish": (
        lambda host: host.mesh_host(),
        "this session has no mesh (mesh.enabled is false)",
    ),
    "mesh_peers": (
        lambda host: host.mesh_host(),
        "this session has no mesh (mesh.enabled is false)",
    ),
    "mesh_ask": (
        lambda host: host.mesh_host(),
        "this session has no mesh (mesh.enabled is false)",
    ),
    "mesh_subscribe": (
        lambda host: host.mesh_host(),
        "this session has no mesh (mesh.enabled is false)",
    ),
    "mesh_unsubscribe": (
        lambda host: host.mesh_host(),
        "this session has no mesh (mesh.enabled is false)",
    ),
}
HOST_GATED_TOOLS = frozenset(_HOST_GATES)


def _specs_for(mode: ForgeMcpNotifyMode) -> tuple[ToolSpec, ...]:
    if mode is ForgeMcpNotifyMode.TRANSCRIPT:
        return TOOL_SPECS
    return tuple(
        FEED_NOTIFY_SPEC if spec.name == "notify" else spec
        for spec in TOOL_SPECS
        if spec.name not in TRANSCRIPT_ONLY_TOOLS
    )


# --------------------------------------------------------------------------- projections


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)] + _TRUNCATION_MARK


def _session_summary(raw: dict[str, Any]) -> dict[str, Any]:
    """The fields a model needs to reason about a session; endpoints and tokens are dropped."""
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    coordination = raw.get("coordination") if isinstance(raw.get("coordination"), dict) else {}
    summary = {
        "id": raw.get("id"),
        "instance_id": raw.get("instance_id"),
        "instance_name": raw.get("instance_name"),
        "name": raw.get("name"),
        "status": raw.get("status"),
        "activity_state": raw.get("activity_state"),
        "activity_state_since": raw.get("activity_state_since"),
        "needs_attention": raw.get("needs_attention"),
        "model": raw.get("model"),
        "workload_type": raw.get("workload_type"),
        "project_id": coordination.get("project_id"),
        "role": coordination.get("role"),
        "parent": coordination.get("parent") or None,
        "source": {
            key: source.get(key)
            for key in ("type", "repo", "branch", "local_path")
            if source.get(key)
        },
        "created_at": raw.get("created_at"),
        "last_active": raw.get("last_active"),
        "message_count": raw.get("message_count"),
        "error": raw.get("error"),
        "short_description": raw.get("short_description"),
        "description": raw.get("description"),
        "short_description_updated_at": raw.get("short_description_updated_at"),
        "description_updated_at": raw.get("description_updated_at"),
        "last_work_at": raw.get("last_work_at"),
        "tool_open": raw.get("tool_open") or None,
    }
    return {key: value for key, value in summary.items() if value not in (None, {}, "")}


def _turn_summary(turn: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    parts = turn.get("parts") if isinstance(turn.get("parts"), list) else []
    tools = [
        str(part.get("name"))
        for part in parts
        if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("name")
    ]
    summary: dict[str, Any] = {
        "id": turn.get("id"),
        "role": turn.get("role"),
        "created_at": turn.get("created_at"),
        "content": _truncate(str(turn.get("content") or ""), max_chars),
    }
    if tools:
        summary["tools"] = tools
    return summary


def _notification_summary(raw: dict[str, Any], *, max_body_chars: int) -> dict[str, Any]:
    keys = (
        "id",
        "seq",
        "session_id",
        "session_name",
        "instance_id",
        "kind",
        "severity",
        "source",
        "title",
        "links",
        "image_state",
        "image_error",
        "speak",
        "created_at",
        "read",
        "correlation_id",
        "resolved_at",
        "resolved_by_notification_id",
        "stale",
    )
    summary = {key: raw.get(key) for key in keys if raw.get(key) is not None}
    body = str(raw.get("body") or "")
    if body:
        summary["body"] = _truncate(body, max_body_chars)
    return summary


def _forge_error_text(tool: str, exc: ForgeApiError) -> str:
    if exc.status is None:
        return f"{tool} failed: Forge is unreachable ({exc.detail}). Try again later."
    return f"{tool} failed: Forge answered {exc.status}: {exc.detail}"


#: HTTP statuses after which a send may nonetheless have been delivered: Forge could not be
#: reached or timed out (no status, 408), or an upstream failed after the message was
#: handed on (any 5xx, including the mesh's 504 for a recipient that did not answer).
_UNCERTAIN_SEND_MIN_STATUS = 500
_REQUEST_TIMEOUT_STATUS = 408


def _unconfirmed_send(tool: str, request_id: str, exc: ForgeApiError) -> ToolResult | None:
    """The tool result for a send that was not confirmed, or ``None`` when it plainly failed.

    A 4xx answer (unknown session, no grant, refused) means the message was not delivered and
    the plain error says so. Anything else may have reached the recipient before the failure,
    so the caller is told that, and told to retry with the same ``request_id``: that id is the
    message's identity, so the retry is delivered once. A new ``request_id`` is a new message.
    """
    uncertain = (
        exc.status is None
        or exc.status == _REQUEST_TIMEOUT_STATUS
        or exc.status >= _UNCERTAIN_SEND_MIN_STATUS
    )
    if not uncertain:
        return None
    return ToolResult.error(
        f"{tool}: delivery not confirmed, the message may have been delivered ({exc.detail}). "
        f"To retry, call {tool} again with the same request_id {request_id!r}: the recipient "
        "takes that one message once. A different request_id sends it a second time."
    )


def _pending_note(request_id: str) -> str:
    return (
        "Not confirmed delivered, and it may have been. To retry, send again with the same "
        f"request_id {request_id!r}: the recipient takes that one message once."
    )


def _validation_text(tool: str, exc: ValidationError) -> str:
    problems = "; ".join(
        f"{'.'.join(str(loc) for loc in error['loc']) or 'arguments'}: {error['msg']}"
        for error in exc.errors()
    )
    return f"Invalid arguments for {tool}: {problems}"


# --------------------------------------------------------------------------- dispatch


class ForgeMcpToolbox:
    """Lists and runs the Forge MCP tools against a :class:`ForgeClient` and a host."""

    def __init__(
        self,
        *,
        client: ForgeClient,
        host: ForgeMcpHost,
        grants: Iterable[ForgeMcpGrant | str],
        limits: ForgeMcpLimits,
        notify_mode: ForgeMcpNotifyMode = ForgeMcpNotifyMode.TRANSCRIPT,
    ) -> None:
        self._client = client
        self._host = host
        self._grants = frozenset(ForgeMcpGrant(grant) for grant in grants)
        self._limits = limits
        self._tool_specs = _specs_for(notify_mode)
        self._specs = {spec.name: spec for spec in self._tool_specs}
        notify = self._notify if notify_mode is ForgeMcpNotifyMode.TRANSCRIPT else self._feed_notify
        self._handlers: dict[str, Callable[[Any], Awaitable[ToolResult]]] = {
            "environment": self._environment,
            "notify": notify,
            "describe_session": self._describe_session,
            "report_artifact": self._report_artifact,
            "request_secret": self._request_secret,
            WORKFLOW_STEP_CLOSE_TOOL: self._workflow_step_close,
            "list_notifications": self._list_notifications,
            "list_sessions": self._list_sessions,
            "get_session": self._get_session,
            "session_transcript": self._session_transcript,
            "send_message": self._send_message,
            "ask": self._ask,
            "message_status": self._message_status,
            REPLY_TOOL: self._reply,
            "room_post": self._room_post,
            "mesh_send": self._mesh_send,
            "mesh_publish": self._mesh_publish,
            "mesh_peers": self._mesh_peers,
            "mesh_ask": self._mesh_ask,
            "mesh_subscribe": self._mesh_subscribe,
            "mesh_unsubscribe": self._mesh_unsubscribe,
            "create_session": self._create_session,
            "start_session": self._start_session,
            "stop_session": self._stop_session,
        }

    @property
    def grants(self) -> frozenset[ForgeMcpGrant]:
        return self._grants

    def _offered(self, spec: ToolSpec) -> bool:
        gate = _HOST_GATES.get(spec.name)
        return gate is None or gate[0](self._host)

    def list_tools(self) -> list[dict[str, Any]]:
        """Every tool, granted or not, so the model learns why a denied one fails.

        A host-gated tool (``workflow_step_close``, ``mesh_send``) is listed on every session whose
        host says it can use it, for the whole session.
        """
        return [spec.to_mcp() for spec in self._tool_specs if self._offered(spec)]

    async def call(self, name: str, arguments: dict[str, Any] | None) -> ToolResult:
        spec = self._specs.get(name)
        if spec is None:
            raise UnknownToolError(name)
        if not self._offered(spec):
            return ToolResult.error(f"{name} is not available: {_HOST_GATES[name][1]}.")
        if spec.grant is not None and spec.grant not in self._grants:
            return ToolResult.error(
                f"The {name} tool needs the '{spec.grant.value}' grant, which this session "
                "does not have. An operator can add it to the session's launch spec or "
                "session create request (forge_mcp.grants). Do not try to work around it; "
                "tell the user what you wanted to do instead."
            )
        try:
            args = spec.args_model.model_validate(arguments or {})
        except ValidationError as exc:
            return ToolResult.error(_validation_text(name, exc))
        try:
            return await self._handlers[name](args)
        except ForgeApiError as exc:
            return ToolResult.error(_forge_error_text(name, exc))
        except SecretRequestError as exc:
            return ToolResult.error(f"{name} failed: {exc}")
        except WorkflowStepCloseError as exc:
            return ToolResult.error(f"{name} rejected: {exc}")
        except PeerMessageError as exc:
            return ToolResult.error(f"{name} failed: {exc}")

    def _ok(self, payload: dict[str, Any]) -> ToolResult:
        return ToolResult.of(payload, max_chars=self._limits.output_max_chars)

    def _refuse_self(self, session_id: str, action: str) -> ToolResult | None:
        if self._host.session_id and session_id == self._host.session_id:
            return ToolResult.error(f"{action} targets your own session; that is not allowed.")
        return None

    # -- own session

    async def _environment(self, _args: _NoArgs) -> ToolResult:
        environment = await self._host.environment()
        environment["grants"] = sorted(grant.value for grant in self._grants)
        environment["tools"] = [
            spec.name
            for spec in self._tool_specs
            if self._offered(spec) and (spec.grant is None or spec.grant in self._grants)
        ]
        return self._ok(environment)

    async def _notify(self, args: _NotifyArgs) -> ToolResult:
        linted = lint_question(NotificationDraft.model_validate(args.model_dump()))
        return self._ok(_with_lint(await self._host.notify(linted.draft), linted))

    async def _feed_notify(self, args: _FeedNotifyArgs) -> ToolResult:
        session_id = args.session_id or self._host.session_id
        if not session_id:
            return ToolResult.error(
                "notify needs session_id: name the session the notification is about "
                "(its id from list_sessions)."
            )
        linted = lint_question(
            NotificationDraft.model_validate(
                args.model_dump(exclude={"idempotency_key", "session_id", "instance_id"})
            )
        )
        recorded = await self._client.submit_notification(
            session_id,
            linted.draft,
            idempotency_key=args.idempotency_key,
            instance_id=args.instance_id,
        )
        return self._ok(
            _with_lint(
                {
                    "notification_id": recorded.get("id"),
                    "seq": recorded.get("seq"),
                    "session_id": recorded.get("session_id"),
                    "source": recorded.get("source"),
                    "state": "committed",
                },
                linted,
            )
        )

    async def _report_artifact(self, args: _ReportArtifactArgs) -> ToolResult:
        try:
            report = ArtifactReport.model_validate(args.model_dump())
        except ValidationError as exc:
            return ToolResult.error(_validation_text("report_artifact", exc))
        return self._ok(await self._host.report_artifact(report))

    async def _workflow_step_close(self, args: WorkflowStepCloseArgs) -> ToolResult:
        """The host checks the close against its open step and records it; a bad one raises."""
        return self._ok(await self._host.close_workflow_step(args))

    async def _request_secret(self, args: _RequestSecretArgs) -> ToolResult:
        """The host's result carries a path only; the value never passes through here."""
        return self._ok(await self._host.request_secret(args.name, args.purpose, args.target))

    async def _describe_session(self, args: _DescribeSessionArgs) -> ToolResult:
        session_id = self._host.session_id
        if not session_id:
            return ToolResult.error(
                "describe_session describes your own Forge session, and this agent is "
                "not running in one."
            )
        changes = args.changes()
        if not changes:
            return ToolResult.error(
                "describe_session needs short_description, description, or both (null clears one)."
            )
        stored = await self._client.update_description(session_id, changes)
        return self._ok(
            {
                "session_id": session_id,
                **{
                    key: stored.get(key)
                    for key in (
                        "short_description",
                        "description",
                        "short_description_updated_at",
                        "description_updated_at",
                    )
                },
            }
        )

    # -- reads

    async def _list_notifications(self, args: _ListNotificationsArgs) -> ToolResult:
        limit = self._limits.clamp_list(args.limit)
        params: dict[str, str] = {"limit": str(limit)}
        if args.before is not None:
            params["before"] = str(args.before)
        if args.after is not None:
            params["after"] = str(args.after)
        if args.kinds:
            params["kind"] = ",".join(kind.value for kind in args.kinds)
        if args.min_severity is not None:
            params["min_severity"] = args.min_severity.value
        if args.sources:
            params["source"] = ",".join(source.value for source in args.sources)
        if args.session_id:
            params["session_id"] = args.session_id
        if args.project_id:
            params["project_id"] = args.project_id
        if args.unread is not None:
            params["unread"] = "true" if args.unread else "false"
        if args.unresolved is not None:
            params["unresolved"] = "true" if args.unresolved else "false"
        page = await self._client.list_notifications(params)
        items = page.get("items") if isinstance(page.get("items"), list) else []
        body_chars = self._limits.transcript_turn_max_chars
        payload = {
            "items": [
                _notification_summary(item, max_body_chars=body_chars)
                for item in items[:limit]
                if isinstance(item, dict)
            ],
            "next_before": page.get("next_before"),
            "head_seq": page.get("head_seq"),
            "unread_count": page.get("unread_count"),
        }
        return self._ok(payload)

    async def _list_sessions(self, args: _ListSessionsArgs) -> ToolResult:
        limit = self._limits.clamp_list(args.limit)
        params = {"scope": args.scope}
        if args.status:
            params["status"] = args.status
        if args.project_id:
            params["project_id"] = args.project_id
        if args.role:
            params["role"] = args.role
        if args.parent_session_id:
            params["parent_session_id"] = args.parent_session_id
        if args.include_archived:
            params["include_archived"] = "true"
        sessions = await self._client.list_sessions(params)
        summaries = [_session_summary(s) for s in sessions if isinstance(s, dict)]
        return self._ok(
            {
                "total": len(summaries),
                "returned": min(len(summaries), limit),
                "sessions": summaries[:limit],
            }
        )

    async def _get_session(self, args: _SessionRef) -> ToolResult:
        session = await self._client.get_session(args.session_id, instance_id=args.instance_id)
        return self._ok({"session": _session_summary(session)})

    async def _session_transcript(self, args: _TranscriptArgs) -> ToolResult:
        turns = self._limits.clamp_turns(args.turns)
        page = await self._client.get_conversation(
            args.session_id, turns=turns, instance_id=args.instance_id
        )
        raw_turns = page.get("turns") if isinstance(page.get("turns"), list) else []
        max_chars = self._limits.transcript_turn_max_chars
        tail = [
            _turn_summary(turn, max_chars=max_chars)
            for turn in raw_turns[-turns:]
            if isinstance(turn, dict) and not turn.get("in_progress")
        ]
        return self._ok(
            {
                "session_id": args.session_id,
                "total_turns": page.get("total_turns", len(raw_turns)),
                "turns": tail,
            }
        )

    # -- granted: message

    async def _send_message(self, args: _SendMessageArgs) -> ToolResult:
        peer = await self._mesh_peer(args)
        if peer is not None:
            refused = self._refuse_self(peer, "send_message")
            if refused is not None:
                return refused
            request_id = args.request_id or f"{_MCP_REQUEST_ID_PREFIX}{uuid.uuid4().hex}"
            try:
                sent = await self._host.send_mesh_message(
                    peer,
                    args.content,
                    expect_reply=True,
                    request_id=request_id,
                    in_reply_to="",
                    urgent=args.urgent,
                )
            except ForgeApiError as exc:
                unconfirmed = _unconfirmed_send("send_message", request_id, exc)
                if unconfirmed is None:
                    raise
                return unconfirmed
            return self._ok(
                {
                    "path": "mesh",
                    "peer": peer,
                    "request_id": request_id,
                    "status": "delivered" if sent.get("status") == "accepted" else sent["status"],
                    "delivery": sent["status"],
                }
            )
        target = await self._resolve_recipient(args)
        if isinstance(target, ToolResult):
            return target
        session_id, instance_id = target
        refused = self._refuse_self(session_id, "send_message")
        if refused is not None:
            return refused
        request_id = args.request_id or f"{_MCP_REQUEST_ID_PREFIX}{uuid.uuid4().hex}"
        try:
            result = await self._client.send_message(
                session_id,
                content=args.content,
                request_id=request_id,
                instance_id=instance_id,
                urgent=args.urgent,
            )
        except ForgeApiError as exc:
            unconfirmed = _unconfirmed_send("send_message", request_id, exc)
            if unconfirmed is None:
                raise
            return unconfirmed
        sent: dict[str, Any] = {
            "path": "forge",
            "session_id": session_id,
            "request_id": request_id,
            "status": result.get("status"),
            "delivery": result.get("delivery"),
        }
        if result.get("status") == "pending":
            sent["note"] = _pending_note(request_id)
        return self._ok(sent)

    async def _mesh_peer(self, args: _SendMessageArgs) -> str | None:
        """The recipient's mesh peer id when it is in this session's peer table, else ``None``."""
        return await self._host.mesh_peer_for(session_id=args.session_id, name=args.to)

    async def _resolve_recipient(
        self, args: _SendMessageArgs
    ) -> tuple[str, str | None] | ToolResult:
        """The session id (and node) a message is for: by id as given, or by name.

        A name is looked up among the sessions the caller can see (its owner's, on every node
        of the Guild). An unknown name and an ambiguous one are errors; nothing is guessed.
        """
        if args.session_id is not None:
            return args.session_id, args.instance_id
        sessions = await self._client.list_sessions({"scope": "guild"})
        matches = [
            session
            for session in sessions
            if isinstance(session, dict)
            and session.get("name") == args.to
            and (args.instance_id is None or session.get("instance_id") == args.instance_id)
        ]
        if not matches:
            return ToolResult.error(
                f"send_message: no session named {args.to!r} among your owner's sessions on "
                "any node of your Guild. list_sessions shows the names; an archived session "
                "is not listed."
            )
        if len(matches) > 1:
            candidates = "; ".join(
                f"{match.get('name')} (id {match.get('id')}, node "
                f"{match.get('instance_name') or match.get('instance_id') or 'unknown'}, "
                f"{match.get('status') or 'unknown status'})"
                for match in matches
            )
            return ToolResult.error(
                f"send_message: {len(matches)} sessions are named {args.to!r}: {candidates}. "
                "Send again with the `session_id` (and `instance_id`) of the one you mean."
            )
        match = matches[0]
        return str(match["id"]), match.get("instance_id") or None

    def _require_message_grant(self, action: str) -> ToolResult | None:
        if ForgeMcpGrant.MESSAGE in self._grants:
            return None
        return ToolResult.error(
            f"{action} needs the '{ForgeMcpGrant.MESSAGE.value}' grant, which this session does "
            "not have. An operator can add it to the session's launch spec or session create "
            "request (forge_mcp.grants). Do not try to work around it; tell the user what you "
            "wanted to do instead."
        )

    async def _reply(self, args: _ReplyArgs) -> ToolResult:
        original = await self._host.received_peer_message(args.message_id)
        route = original.reply
        if route is None:
            return ToolResult.error(
                f"reply: message {args.message_id} from {original.sender.label()} takes no "
                "reply: its sender sent it as a notice and gave no route to answer by."
            )
        request_id = args.request_id or f"{_MCP_REQUEST_ID_PREFIX}{uuid.uuid4().hex}"
        match route.kind:
            case ReplyKind.SESSION:
                denied = self._require_message_grant("reply")
                if denied is not None:
                    return denied
                peer = await self._host.mesh_peer_for(session_id=route.target, name=None)
                try:
                    if peer is not None:
                        sent = await self._host.send_mesh_message(
                            peer,
                            args.content,
                            expect_reply=True,
                            request_id=request_id,
                            in_reply_to=args.message_id,
                        )
                        answer = {"path": "mesh", "peer": peer, "status": sent["status"]}
                    else:
                        result = await self._client.send_message(
                            route.target,
                            content=args.content,
                            request_id=request_id,
                            instance_id=None,
                            in_reply_to=args.message_id,
                        )
                        answer = {
                            "path": "forge",
                            "status": result.get("status"),
                            "delivery": result.get("delivery"),
                        }
                        if result.get("status") == "pending":
                            answer["note"] = _pending_note(request_id)
                except ForgeApiError as exc:
                    unconfirmed = _unconfirmed_send("reply", request_id, exc)
                    if unconfirmed is None:
                        raise
                    return unconfirmed
            case ReplyKind.ROOM:
                denied = self._require_message_grant("reply")
                if denied is not None:
                    return denied
                if not original.room_id:
                    return ToolResult.error(
                        f"reply: message {args.message_id} names a room route but no room"
                    )
                answer = await self._client.post_room_message(
                    route.target, room_id=original.room_id, content=args.content
                )
            case ReplyKind.MESH:
                try:
                    answer = await self._host.send_mesh_message(
                        route.target,
                        args.content,
                        expect_reply=False,
                        request_id=request_id,
                        in_reply_to=args.message_id,
                        thread=original.thread,
                    )
                except ForgeApiError as exc:
                    unconfirmed = _unconfirmed_send("reply", request_id, exc)
                    if unconfirmed is None:
                        raise
                    return unconfirmed
        return self._ok(
            {
                "message_id": args.message_id,
                "replied_to": original.sender.label(),
                "via": route.kind.value,
                "request_id": request_id,
                **answer,
            }
        )

    async def _room_post(self, args: _RoomPostArgs) -> ToolResult:
        refused = self._refuse_self(args.host_session_id, "room_post")
        if refused is not None:
            return refused
        answer = await self._client.post_room_message(
            args.host_session_id, room_id=args.room_id, content=args.content
        )
        return self._ok(
            {"room_id": args.room_id, "host_session_id": args.host_session_id, **answer}
        )

    async def _mesh_send(self, args: _MeshSendArgs) -> ToolResult:
        request_id = args.request_id or f"{_MCP_REQUEST_ID_PREFIX}{uuid.uuid4().hex}"
        try:
            answer = await self._host.send_mesh_message(
                args.peer,
                args.content,
                expect_reply=args.expect_reply,
                request_id=request_id,
                in_reply_to="",
            )
        except ForgeApiError as exc:
            unconfirmed = _unconfirmed_send("mesh_send", request_id, exc)
            if unconfirmed is None:
                raise
            return unconfirmed
        return self._ok({"peer": args.peer, "request_id": request_id, **answer})

    async def _mesh_ask(self, args: _MeshAskArgs) -> ToolResult:
        ceiling_s = self._host.ask_wait_max_s()
        if args.wait_s > ceiling_s:
            message = (
                f"mesh_ask: wait_s={args.wait_s:g} is above ask.wait_max_s={ceiling_s:g}, the "
                "longest one MCP tool call may run here (it must stay shorter than the engine's "
                "tool timeout, or the engine would end the call before it could answer). "
                f"Nothing was sent. Pass wait_s of at most {ceiling_s:g}, or none: answers "
                "arrive in your inbox either way."
            )
            record_cap_hit("ask.wait_max_s", message)
            return ToolResult.error(message)
        request_id = args.request_id or f"{_MCP_REQUEST_ID_PREFIX}{uuid.uuid4().hex}"
        answer = await self._host.mesh_ask(
            args.content,
            producers_of=args.producers_of.strip(),
            peers=args.peers,
            request_id=request_id,
            wait_s=args.wait_s,
        )
        return self._ok({"request_id": request_id, **answer})

    async def _mesh_subscribe(self, args: _MeshFollowArgs) -> ToolResult:
        return self._ok(
            await self._host.mesh_subscribe(
                topic=args.topic.strip(), correlation_id=args.correlation_id.strip()
            )
        )

    async def _mesh_unsubscribe(self, args: _MeshFollowArgs) -> ToolResult:
        return self._ok(
            await self._host.mesh_unsubscribe(
                topic=args.topic.strip(), correlation_id=args.correlation_id.strip()
            )
        )

    async def _mesh_peers(self, args: _MeshPeersArgs) -> ToolResult:
        answer = await self._host.mesh_peers(
            role=args.role,
            capability=args.capability,
            consumes=args.consumes,
            produces=args.produces,
        )
        return self._ok(answer)

    async def _mesh_publish(self, args: _MeshPublishArgs) -> ToolResult:
        request_id = args.request_id or f"{_MCP_REQUEST_ID_PREFIX}{uuid.uuid4().hex}"
        answer = await self._host.publish_mesh_event(
            args.topic,
            args.content,
            request_id=request_id,
            correlation_id=args.correlation_id,
            in_reply_to=args.in_reply_to,
        )
        return self._ok({"request_id": request_id, **answer})

    async def _ask(self, args: _AskArgs) -> ToolResult:
        ceiling_s = self._host.ask_wait_max_s()
        deadline_s = ceiling_s if args.deadline_s is None else args.deadline_s
        if deadline_s > ceiling_s:
            message = (
                f"ask: deadline_s={deadline_s:g} is above ask.wait_max_s={ceiling_s:g}, "
                "the longest one MCP tool call may run here (it must stay shorter than the "
                "engine's tool timeout, or the engine would end the call before it could answer). "
                "Nothing was "
                f"sent. Pass deadline_s of at most {ceiling_s:g}; to keep waiting for a slow "
                "recipient, call ask again with the same request_id."
            )
            record_cap_hit("ask.wait_max_s", message)
            return ToolResult.error(message)
        peer = await self._mesh_peer(args)
        if peer is not None:
            return await self._ask_over_mesh(args, peer, deadline_s)
        target = await self._resolve_recipient(args)
        if isinstance(target, ToolResult):
            return target
        session_id, instance_id = target
        refused = self._refuse_self(session_id, "ask")
        if refused is not None:
            return refused
        request_id = args.request_id or f"{_MCP_REQUEST_ID_PREFIX}{uuid.uuid4().hex}"
        try:
            sent = await self._client.send_message(
                session_id,
                content=args.content,
                request_id=request_id,
                instance_id=instance_id,
                urgent=args.urgent,
            )
        except ForgeApiError as exc:
            unconfirmed = _unconfirmed_send("ask", request_id, exc)
            if unconfirmed is None:
                raise
            return unconfirmed
        message_id = derive_message_id(
            PeerAddress(kind=PeerKind.SESSION, id=str(self._host.session_id)),
            PeerAddress(kind=PeerKind.SESSION, id=session_id),
            request_id,
        )
        ended = await self._host.ask_for_answer(
            recipient_id=session_id,
            instance_id=instance_id,
            message_id=message_id,
            request_id=request_id,
            delivery_confirmed=sent.get("status") == "delivered",
            deadline_s=deadline_s,
        )
        return self._ask_result(
            {
                "path": "forge",
                "session_id": session_id,
                "request_id": request_id,
                "message_id": message_id,
                **ended,
            }
        )

    async def _ask_over_mesh(self, args: _AskArgs, peer: str, deadline_s: float) -> ToolResult:
        """Ask a mesh peer: the recipient's Skuld acknowledges the send and reports its state."""
        refused = self._refuse_self(peer, "ask")
        if refused is not None:
            return refused
        request_id = args.request_id or f"{_MCP_REQUEST_ID_PREFIX}{uuid.uuid4().hex}"
        message_id = derive_message_id(
            PeerAddress(kind=PeerKind.SESSION, id=str(self._host.session_id)),
            PeerAddress(kind=PeerKind.MESH_PEER, id=peer),
            request_id,
        )
        try:
            sent = await self._host.send_mesh_message(
                peer,
                args.content,
                expect_reply=True,
                request_id=request_id,
                in_reply_to="",
                urgent=args.urgent,
            )
            confirmed = sent.get("status") == "accepted"
        except ForgeApiError as exc:
            if exc.status != _MESH_RPC_TIMEOUT_STATUS:
                unconfirmed = _unconfirmed_send("ask", request_id, exc)
                if unconfirmed is None:
                    raise
                return unconfirmed
            # No answer from the recipient's Skuld: the message may have arrived. That is
            # delivery_unknown, and the wait goes on (an answer would settle it).
            confirmed = False
        ended = await self._host.ask_for_answer(
            recipient_id=peer,
            instance_id=None,
            message_id=message_id,
            request_id=request_id,
            delivery_confirmed=confirmed,
            deadline_s=deadline_s,
            mesh_peer=peer,
        )
        return self._ask_result(
            {
                "path": "mesh",
                "peer": peer,
                "request_id": request_id,
                "message_id": message_id,
                **ended,
            }
        )

    def _ask_result(self, payload: dict[str, Any]) -> ToolResult:
        result = self._ok(payload)
        if payload.get("outcome") == AskOutcome.ANSWERED:
            return result
        return replace(result, is_error=True)

    async def _message_status(self, args: _MessageStatusArgs) -> ToolResult:
        status = await self._client.get_message_delivery(
            args.session_id, request_id=args.request_id, instance_id=args.instance_id
        )
        return self._ok({"session_id": args.session_id, "delivery": status})

    # -- granted: lifecycle

    async def _create_session(self, args: _CreateSessionArgs) -> ToolResult:
        if args.repo and args.local_path:
            return ToolResult.error("create_session takes repo or local_path, not both.")
        body: dict[str, Any] = {"name": args.name}
        for key in ("initial_prompt", "model", "system_prompt", "definition", "launch_spec"):
            value = getattr(args, key)
            if value:
                body[key] = value
        if args.repo:
            body["source"] = {"type": "git", "repo": args.repo}
            if args.branch:
                body["source"]["branch"] = args.branch
        if args.local_path:
            body["source"] = {"type": "local_mount", "local_path": args.local_path}
        coordination = await self._launch_coordination(args)
        if coordination is not None:
            body["coordination"] = coordination
        created = await self._client.create_session(body)
        return self._ok({"session": _session_summary(created)})

    async def _launch_coordination(self, args: _CreateSessionArgs) -> dict[str, Any] | None:
        """Project membership for a new session: explicit fields, else "my worker"."""
        if args.standalone:
            return None
        caller = await self._own_membership()
        project_id = args.project_id or (caller or {}).get("project_id")
        if not project_id:
            return None
        coordination: dict[str, Any] = {"project_id": project_id, "role": args.role or "worker"}
        if args.objective:
            coordination["objective"] = args.objective
        if args.parent_session_id:
            parent_instance = args.parent_instance_id or (caller or {}).get("instance_id")
            if parent_instance:
                coordination["parent"] = {
                    "instance_id": parent_instance,
                    "session_id": args.parent_session_id,
                }
        elif caller and caller.get("project_id") == project_id and caller.get("instance_id"):
            coordination["parent"] = {
                "instance_id": caller["instance_id"],
                "session_id": caller["session_id"],
            }
        return coordination

    async def _own_membership(self) -> dict[str, Any] | None:
        session_id = self._host.session_id
        if not session_id:
            return None
        # A session that cannot read its own membership must not launch outside its project.
        own = await self._client.get_session(session_id, instance_id=None)
        coordination = own.get("coordination") if isinstance(own, dict) else None
        if not isinstance(coordination, dict):
            return {"session_id": session_id, "instance_id": own.get("instance_id")}
        return {
            "session_id": session_id,
            "instance_id": own.get("instance_id"),
            "project_id": coordination.get("project_id"),
        }

    async def _start_session(self, args: _SessionRef) -> ToolResult:
        started = await self._client.start_session(args.session_id, instance_id=args.instance_id)
        return self._ok({"session": _session_summary(started)})

    async def _stop_session(self, args: _SessionRef) -> ToolResult:
        refused = self._refuse_self(args.session_id, "stop_session")
        if refused is not None:
            return refused
        stopped = await self._client.stop_session(args.session_id, instance_id=args.instance_id)
        return self._ok({"session": _session_summary(stopped)})
