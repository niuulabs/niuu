"""``notify``: a resident tells its owner something on purpose.

The resident counterpart of the Forge MCP ``notify`` tool that Forge sessions
use. The notification is queued in the resident's durable notification outbox;
Forge drains that outbox over the resident's OpenClaw shim and records it in the
same notification feed, outbox and delivery rules (push, webhooks, Telegram) as
a Forge session's notification, anchored to this resident and to the channel the
turn runs in (the main room when it runs in none).

The tool holds no credential: Forge reads the outbox with the connection it
already authenticates to this resident, so there is nothing for the model to
leak and nothing to configure per cluster.
"""

from __future__ import annotations

import json
from uuid import uuid4

from pydantic import ValidationError

from niuu.domain.notifications import (
    AGENT_KINDS,
    MAX_BODY_CHARS,
    MAX_LINK_LABEL_CHARS,
    MAX_LINK_URL_CHARS,
    MAX_LINKS,
    MAX_TITLE_CHARS,
    NotificationDraft,
    NotificationLinkKind,
    NotificationSeverity,
)
from niuu.domain.resident_notifications import MAX_IDEMPOTENCY_KEY_CHARS
from niuu.forge_mcp.guidance import (
    COMPACT_NOTIFICATION_GUIDANCE,
    NOTIFICATION_TRIGGER_GUIDANCE,
)
from ravn.domain.models import ToolResult
from ravn.domain.notification_channel import current_notification_channel
from ravn.ports.resident_notifications import ResidentNotificationOutboxPort
from ravn.ports.tool import ToolPort

NOTIFY_TOOL_NAME = "notify"
_PERMISSION = "notify:send"

_DESCRIPTION = (
    "Notify your owner. The notification appears in their Forge Notifications feed "
    "(Lexi's Activities) and may reach their phone or chat through their delivery "
    "rules; opening it opens this conversation. It is not added to the transcript.\n\n"
    "Returns {state: queued, idempotency_key}: it is saved durably and reaches Forge "
    "automatically, even if Forge is briefly unreachable. Do not resend it; a retry "
    "with the same idempotency_key is recorded once.\n\n"
    + NOTIFICATION_TRIGGER_GUIDANCE
    + "\n\n"
    + COMPACT_NOTIFICATION_GUIDANCE
)

_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {
            "type": "string",
            "enum": sorted(kind.value for kind in AGENT_KINDS),
            "description": (
                "milestone = meaningful work finished; decision = a choice the owner must "
                "make or you made; attention = blocked or need the owner's input; error = a "
                "failure the owner must know about; info = a brief FYI."
            ),
        },
        "severity": {
            "type": "string",
            "enum": [severity.value for severity in NotificationSeverity],
            "default": NotificationSeverity.INFO.value,
        },
        "title": {"type": "string", "minLength": 1, "maxLength": MAX_TITLE_CHARS},
        "body": {"type": "string", "maxLength": MAX_BODY_CHARS},
        "links": {
            "type": "array",
            "maxItems": MAX_LINKS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "label": {"type": "string", "minLength": 1, "maxLength": MAX_LINK_LABEL_CHARS},
                    "url": {"type": "string", "maxLength": MAX_LINK_URL_CHARS},
                    "kind": {
                        "type": "string",
                        "enum": [kind.value for kind in NotificationLinkKind],
                    },
                },
                "required": ["label"],
            },
        },
        "idempotency_key": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_IDEMPOTENCY_KEY_CHARS,
            "description": "Your own id for this notification; a retry with it is recorded once.",
        },
    },
    "required": ["kind", "title"],
}


class ResidentNotifyTool(ToolPort):
    """Queue a notification for the resident's owner in its notification outbox."""

    def __init__(self, outbox: ResidentNotificationOutboxPort) -> None:
        self._outbox = outbox

    @property
    def name(self) -> str:
        return NOTIFY_TOOL_NAME

    @property
    def description(self) -> str:
        return _DESCRIPTION

    @property
    def input_schema(self) -> dict:
        return _INPUT_SCHEMA

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        fields = dict(input or {})
        idempotency_key = str(fields.pop("idempotency_key", "") or uuid4().hex)
        if len(idempotency_key) > MAX_IDEMPOTENCY_KEY_CHARS:
            return _error(f"idempotency_key is at most {MAX_IDEMPOTENCY_KEY_CHARS} characters")
        try:
            draft = NotificationDraft.model_validate(fields)
        except ValidationError as exc:
            return _error(f"Invalid notification: {exc.errors(include_url=False)}")
        if draft.kind not in AGENT_KINDS:
            return _error(f"Kind {draft.kind.value!r} is derived by Forge and cannot be sent")
        entry = await self._outbox.notify(
            draft=draft,
            idempotency_key=idempotency_key,
            channel=current_notification_channel.get(),
        )
        return ToolResult(
            tool_call_id="",
            content=json.dumps(
                {"state": "queued", "idempotency_key": entry.idempotency_key, "seq": entry.seq}
            ),
        )


def _error(message: str) -> ToolResult:
    return ToolResult(tool_call_id="", content=message, is_error=True)
