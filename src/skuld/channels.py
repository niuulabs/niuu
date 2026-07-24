"""Message channel abstraction for Skuld broker.

Provides a uniform interface for sending CLI events to different
channel types (browser WebSocket, Telegram, etc.). The broker
broadcasts events to all registered channels via send_event().
"""

import asyncio
import html
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Literal

from fastapi import WebSocketDisconnect

from niuu.domain.outcome import parse_outcome_block
from niuu.observability import get_observability

logger = logging.getLogger("skuld.channels")

# Telegram API max message length
TELEGRAM_MAX_MESSAGE_LENGTH = 4096

# Buffer flush interval for streaming text (seconds)
TELEGRAM_BUFFER_FLUSH_INTERVAL = 1.5
TELEGRAM_TOPIC_NAME_MAX_LENGTH = 128
TELEGRAM_REPLY_CACHE_SIZE = 1024
TELEGRAM_OUTCOME_SUMMARY_MAX_LENGTH = 450
TELEGRAM_OUTCOME_DETAIL_MAX_LENGTH = 220
TELEGRAM_OUTCOME_LIST_LIMIT = 2

TelegramTopicMode = Literal["shared_chat", "fixed_topic", "topic_per_session"]


def _is_expected_ws_disconnect(exc: Exception) -> bool:
    if isinstance(exc, WebSocketDisconnect):
        return True
    if exc.__class__.__name__ == "ClientDisconnected":
        return True
    if isinstance(exc, RuntimeError):
        text = str(exc)
        return (
            "WebSocket is not connected" in text
            or 'Cannot call "send" once a close message has been sent.' in text
        )
    return False


try:
    from telegram import Bot, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        filters,
    )

    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False
    ForceReply = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# MessageChannel ABC
# ---------------------------------------------------------------------------


class MessageChannel(ABC):
    """Abstract base class for a message delivery channel.

    Channels receive CLI events from the broker and deliver them
    to their respective endpoints (browser, Telegram, etc.).
    """

    @abstractmethod
    async def send_event(self, event: dict) -> None:
        """Send a CLI event to this channel."""

    @abstractmethod
    async def close(self) -> None:
        """Close this channel and release resources."""

    @property
    @abstractmethod
    def channel_type(self) -> str:
        """Return channel type identifier (e.g., 'browser', 'telegram')."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Return True if the channel is open and can accept events."""


# ---------------------------------------------------------------------------
# WebSocketChannel — wraps existing browser WebSocket
# ---------------------------------------------------------------------------


_INTERNAL_BLOCK_TYPES = ("tool_use", "tool_result")


def filter_internal_blocks(
    event: dict,
    *,
    open_block_type: str | None,
) -> tuple[dict | None, str | None]:
    """Drop tool_use/tool_result content from an event for an "hide internal" channel.

    Returns ``(event_to_send_or_None, new_open_block_type)``.

    For streaming content_block events, only one block is open at a time
    in the current transports — track the open block's type sequentially so
    we know whether the matching ``content_block_delta`` and
    ``content_block_stop`` belong to an internal block.

    For ``assistant`` / ``user`` events that arrive with a content list
    (Anthropic SDK shape), strip internal blocks and drop the event when
    nothing meaningful remains.
    """
    et = event.get("type")

    if et == "content_block_start":
        block_type = event.get("content_block", {}).get("type")
        next_open = block_type
        if block_type in _INTERNAL_BLOCK_TYPES:
            return None, next_open
        return event, next_open

    if et == "content_block_delta":
        if open_block_type in _INTERNAL_BLOCK_TYPES:
            return None, open_block_type
        return event, open_block_type

    if et == "content_block_stop":
        if open_block_type in _INTERNAL_BLOCK_TYPES:
            return None, None
        return event, None

    if et in ("assistant", "user"):
        message = event.get("message")
        content = (
            message.get("content")
            if isinstance(message, dict) and isinstance(message.get("content"), list)
            else event.get("content")
            if isinstance(event.get("content"), list)
            else None
        )
        if content is None:
            return event, open_block_type

        kept = [
            b
            for b in content
            if not (isinstance(b, dict) and b.get("type") in _INTERNAL_BLOCK_TYPES)
        ]
        if len(kept) == len(content):
            return event, open_block_type
        if not kept:
            return None, open_block_type

        new_event = dict(event)
        if isinstance(message, dict) and isinstance(message.get("content"), list):
            new_message = dict(message)
            new_message["content"] = kept
            new_event["message"] = new_message
        else:
            new_event["content"] = kept
        return new_event, open_block_type

    return event, open_block_type


class WebSocketChannel(MessageChannel):
    """Message channel backed by a FastAPI WebSocket connection.

    Wraps the existing browser WebSocket so it can participate in the
    broker's channel registry alongside other channel types.
    """

    def __init__(self, ws: object, *, show_internal: bool = False) -> None:
        """Initialize with a FastAPI WebSocket instance.

        Args:
            ws: A FastAPI WebSocket (typed as object to avoid import
                dependency at module level).
            show_internal: Whether to forward tool_use/tool_result blocks
                to this channel. Default ``False`` (hide), matching the
                browser's default toggle position.
        """
        self._ws = ws
        self._closed = False
        self._show_internal = show_internal
        self._open_block_type: str | None = None

    def set_show_internal(self, visible: bool) -> None:
        """Update the per-channel filter for tool_use / tool_result events."""
        self._show_internal = visible
        if visible:
            self._open_block_type = None

    @property
    def show_internal(self) -> bool:
        return self._show_internal

    async def send_event(self, event: dict) -> None:
        """Send a JSON-encoded CLI event over the WebSocket."""
        if self._closed:
            return
        if not self._show_internal:
            filtered, self._open_block_type = filter_internal_blocks(
                event, open_block_type=self._open_block_type
            )
            if filtered is None:
                return
            event = filtered
        try:
            await self._ws.send_text(json.dumps(event))
        except Exception as exc:
            if not _is_expected_ws_disconnect(exc):
                raise
            self._closed = True

    async def close(self) -> None:
        """Close the underlying WebSocket connection."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.close()
        except Exception:
            logger.debug("Error closing WebSocket channel", exc_info=True)

    @property
    def channel_type(self) -> str:
        return "browser"

    @property
    def is_open(self) -> bool:
        return not self._closed

    @property
    def ws(self) -> object:
        """Access the underlying WebSocket (for receive operations)."""
        return self._ws


# ---------------------------------------------------------------------------
# TelegramChannel — sends CLI events to a Telegram chat
# ---------------------------------------------------------------------------


def _format_outcome_lines(
    *,
    name: str,
    outcome_type: str,
    verdict: str,
    summary: str,
    fields: object,
) -> str:
    if isinstance(fields, dict) and _is_judgment_outcome(outcome_type, fields):
        return _format_judgment_outcome(
            name=name,
            outcome_type=outcome_type,
            verdict=verdict,
            summary=summary,
            fields=fields,
        )

    outcome_label = _humanize_field_name(outcome_type or "outcome")
    lines = [f"{name} — {outcome_label}"]
    if verdict:
        lines.append(f"Verdict: {_humanize_field_name(verdict)}")
    if summary:
        lines.append(summary)
    if isinstance(fields, dict):
        for key, value in fields.items():
            if key in {"summary", "verdict"}:
                continue
            lines.extend(_format_structured_field(str(key), value))
    elif fields:
        lines.append(str(fields))
    return "\n".join(line for line in lines if line)


def _is_judgment_outcome(outcome_type: str, fields: dict[str, Any]) -> bool:
    return "judgment" in outcome_type.casefold() or (
        "decision" in fields
        and any(
            key in fields
            for key in (
                "rationale",
                "recommended_action",
                "operational_state",
                "tier",
            )
        )
    )


def _format_judgment_outcome(
    *,
    name: str,
    outcome_type: str,
    verdict: str,
    summary: str,
    fields: dict[str, Any],
) -> str:
    decision = str(fields.get("decision") or "").strip()
    title = _judgment_title(decision, outcome_type)
    lines = [f"{_judgment_emoji(decision, fields)} **{name} — {title}**"]

    narrative = _first_meaningful(
        summary,
        fields.get("state_summary"),
        fields.get("evidence_summary"),
    )
    if narrative:
        lines.extend(
            [
                "",
                _bounded_telegram_text(
                    narrative,
                    TELEGRAM_OUTCOME_SUMMARY_MAX_LENGTH,
                ),
            ]
        )

    question = _meaningful_text(fields.get("question"))
    if question:
        lines.extend(
            [
                "",
                "**Needs your input**",
                _bounded_telegram_text(question, TELEGRAM_OUTCOME_DETAIL_MAX_LENGTH),
            ]
        )

    recommendation = _meaningful_text(fields.get("recommended_action"))
    if recommendation:
        lines.extend(
            [
                "",
                "**Recommended action**",
                _bounded_telegram_text(
                    recommendation,
                    TELEGRAM_OUTCOME_DETAIL_MAX_LENGTH,
                ),
            ]
        )

    rationale = _meaningful_text(fields.get("rationale"))
    if rationale and rationale != narrative:
        lines.extend(
            [
                "",
                "**Why**",
                ">! "
                + _bounded_telegram_text(
                    rationale,
                    TELEGRAM_OUTCOME_SUMMARY_MAX_LENGTH,
                ),
            ]
        )

    capability_gap = _meaningful_text(fields.get("capability_gap"))
    if capability_gap:
        lines.extend(
            [
                "",
                "**Capability gap**",
                _bounded_telegram_text(
                    capability_gap,
                    TELEGRAM_OUTCOME_DETAIL_MAX_LENGTH,
                ),
            ]
        )

    tool_plan = _meaningful_text(fields.get("tool_evolution_plan"))
    if tool_plan:
        lines.extend(
            [
                "",
                "**Tool evolution**",
                _bounded_telegram_text(
                    tool_plan,
                    TELEGRAM_OUTCOME_DETAIL_MAX_LENGTH,
                ),
            ]
        )

    open_questions = _bounded_outcome_items(
        fields.get("open_questions"),
        limit=2,
    )
    if open_questions:
        lines.extend(["", "**Open questions**"])
        lines.extend(f"- {item}" for item in open_questions)

    raw_evidence = fields.get("evidence")
    evidence = _bounded_outcome_items(raw_evidence)
    if evidence:
        evidence_count = _outcome_item_count(raw_evidence)
        count_label = (
            str(len(evidence))
            if evidence_count == len(evidence)
            else f"{len(evidence)} of {evidence_count}"
        )
        lines.extend(["", f"**Evidence ({count_label})**"])
        lines.extend(f">! • {item}" for item in evidence)

    details = _judgment_details(verdict=verdict, fields=fields)
    if details:
        lines.extend(["", "**Details**"])
        lines.extend(f">! {detail}" for detail in details)

    return "\n".join(lines)


def _judgment_title(decision: str, outcome_type: str) -> str:
    labels = {
        "ignore": "No action needed",
        "watch": "Watching",
        "investigate": "Investigating",
        "propose_action": "Action proposed",
        "escalate": "Escalation",
        "learn": "Learning",
        "blocked": "Blocked",
    }
    return labels.get(decision.casefold(), _humanize_field_name(outcome_type or "judgment"))


def _judgment_emoji(decision: str, fields: dict[str, Any]) -> str:
    tier = str(fields.get("tier") or "").casefold()
    if tier == "urgent":
        return "🚨"
    return {
        "ignore": "✅",
        "watch": "👀",
        "investigate": "🔎",
        "propose_action": "🛠️",
        "escalate": "⚠️",
        "learn": "🧠",
        "blocked": "⛔",
    }.get(decision.casefold(), "ℹ️")


def _judgment_details(*, verdict: str, fields: dict[str, Any]) -> list[str]:
    details: list[str] = []
    status_parts = [
        _humanize_field_name(fields[key])
        for key in ("tier", "operational_state", "wakefulness")
        if _meaningful_text(fields.get(key))
    ]
    if status_parts:
        details.append("Status: " + " · ".join(status_parts))

    authority = _meaningful_text(fields.get("action_authority"))
    capability = _meaningful_text(fields.get("action_capability"))
    action_parts: list[str] = []
    if authority:
        action_parts.append(_humanize_field_name(authority))
    if capability:
        action_parts.append(_bounded_telegram_text(capability, 160))
    if action_parts:
        details.append("Authority: " + " · ".join(action_parts))

    if verdict and verdict.casefold() not in {"judged", "success"}:
        details.append(f"Verdict: {_humanize_field_name(verdict)}")

    confidence = fields.get("confidence")
    if isinstance(confidence, int | float):
        percentage = confidence * 100 if 0 <= confidence <= 1 else confidence
        details.append(f"Confidence: {percentage:.0f}%")

    targets = _bounded_outcome_items(fields.get("target_surfaces"), limit=2, max_length=160)
    if targets:
        details.append("Targets: " + ", ".join(targets))

    signal_refs = fields.get("signal_refs")
    if isinstance(signal_refs, list | tuple) and signal_refs:
        details.append(f"Signals: {len(signal_refs)}")
    return details


def _first_meaningful(*values: object) -> str:
    for value in values:
        text = _meaningful_text(value)
        if text:
            return text
    return ""


def _meaningful_text(value: object) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if not text or text.casefold() in {"none", "n/a", "null", "unknown"}:
        return ""
    return text


def _bounded_telegram_text(value: object, max_length: int) -> str:
    text = _meaningful_text(value)
    if len(text) <= max_length:
        return text
    return text[: max(0, max_length - 1)].rstrip() + "…"


def _bounded_outcome_items(
    value: object,
    *,
    limit: int = TELEGRAM_OUTCOME_LIST_LIMIT,
    max_length: int = TELEGRAM_OUTCOME_DETAIL_MAX_LENGTH,
) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    items = [
        _bounded_telegram_text(item, max_length)
        for item in value
        if _meaningful_text(item)
    ]
    return items[:limit]


def _outcome_item_count(value: object) -> int:
    if not isinstance(value, list | tuple):
        return 0
    return sum(1 for item in value if _meaningful_text(item))


def _humanize_field_name(value: object) -> str:
    text = str(value or "").strip().replace("_", " ").replace(".", " ")
    return " ".join(text.split()).capitalize()


def _format_structured_field(
    key: str,
    value: object,
    *,
    indent: str = "",
) -> list[str]:
    """Render structured outcome data as readable labels and bullets."""
    label = _humanize_field_name(key)
    if isinstance(value, dict):
        if not value:
            return []
        lines = [f"{indent}{label}:"]
        for nested_key, nested_value in value.items():
            lines.extend(
                _format_structured_field(
                    str(nested_key),
                    nested_value,
                    indent=f"{indent}  ",
                )
            )
        return lines

    if isinstance(value, list | tuple):
        if not value:
            return []
        lines = [f"{indent}{label}:"]
        for item in value:
            if isinstance(item, dict):
                lines.append(f"{indent}-")
                for nested_key, nested_value in item.items():
                    lines.extend(
                        _format_structured_field(
                            str(nested_key),
                            nested_value,
                            indent=f"{indent}  ",
                        )
                    )
                continue
            lines.append(f"{indent}- {item}")
        return lines

    if value is None or value == "":
        return []
    return [f"{indent}{label}: {value}"]


def format_telegram_event(event: dict) -> str | None:
    """Format a CLI event as a Telegram-friendly message.

    Returns None if the event should be skipped (e.g., thinking blocks).

    Formatting rules:
    - Text responses: plain text (Telegram MarkdownV2 is fragile)
    - Tool use: prefixed with a compact tool label
    - Errors: prefixed with an error label
    - Public room events: fanned out so Telegram can mirror user-visible chat
    - Internal room events/activity/detail frames: skipped
    - content_block_delta: returns the delta text fragment
    """
    event_type = event.get("type", "")

    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        text = delta.get("text", "")
        if not text:
            return None
        return text

    if event_type == "user_confirmed":
        metadata = event.get("metadata", {})
        source = event.get("source")
        if isinstance(source, str) and source and source != "browser":
            return None
        if isinstance(metadata, dict) and metadata.get("source_platform"):
            return None
        content = event.get("content", "")
        if not content:
            return None
        return f"[prompt] {content}"

    if event_type == "assistant":
        content = event.get("content", event.get("message", {}).get("content", []))
        if not isinstance(content, list):
            return None

        parts = []
        for block in content:
            block_type = block.get("type", "")

            if block_type == "thinking":
                continue

            if block_type == "text":
                parts.append(block.get("text", ""))

            if block_type == "tool_use":
                name = block.get("name", "unknown")
                tool_input = block.get("input", {})
                # Show relevant input fields
                detail = ""
                if "command" in tool_input:
                    detail = tool_input["command"]
                elif "file_path" in tool_input:
                    detail = tool_input["file_path"]
                elif "pattern" in tool_input:
                    detail = tool_input["pattern"]

                if detail:
                    parts.append(f"[tool] {name}: {detail}")
                else:
                    parts.append(f"[tool] {name}")

        if not parts:
            return None
        return "\n".join(parts)

    if event_type == "room_message":
        if event.get("visibility") == "internal":
            return None
        participant = event.get("participant", {}) or {}
        name = (
            participant.get("display_name")
            or participant.get("persona")
            or event.get("participantId")
            or "agent"
        )
        content = event.get("content", "")
        if not content:
            return None
        if isinstance(content, str):
            parsed_outcome = parse_outcome_block(content)
            if parsed_outcome is not None:
                # Ravn emits a typed room_outcome after its response. Sending
                # both surfaces the same judgment twice and exposes the raw
                # outcome contract to the operator.
                return None
        prefix = "[error]" if event.get("error") else f"[{name}]"
        return f"{prefix} {content}"

    if event_type == "room_notification":
        participant = event.get("participant", {}) or {}
        name = (
            participant.get("display_name")
            or participant.get("persona")
            or event.get("participantId")
            or "agent"
        )
        summary = event.get("summary", "")
        reason = event.get("reason", "")
        recommendation = event.get("recommendation", "")
        attempted = event.get("attempted", [])
        notification_type = str(event.get("notificationType") or "notice")
        if notification_type == "help_needed":
            parts = [f"{name} needs your input"]
            if summary:
                parts.append(str(summary))
        else:
            parts = [f"{name} — {_humanize_field_name(notification_type)}"]
            if summary:
                parts.append(str(summary))
        if reason:
            parts.append(f"Why: {reason}")
        if isinstance(attempted, list) and attempted:
            parts.append("Already tried:")
            parts.extend(f"- {item}" for item in attempted if str(item).strip())
        if recommendation:
            parts.append(f"Suggested next step: {recommendation}")
        return "\n".join(parts)

    if event_type == "room_outcome":
        participant = event.get("participant", {}) or {}
        name = (
            participant.get("display_name")
            or participant.get("persona")
            or event.get("participantId")
            or "agent"
        )
        raw_fields = event.get("fields", {})
        fields = raw_fields if isinstance(raw_fields, dict) else {}
        verdict = str(event.get("verdict") or fields.get("verdict") or "")
        continuation = str(fields.get("continuation") or "")
        if verdict.casefold() == "help_needed" or continuation.casefold() == "ask_operator":
            # The paired help notification is the single answerable operator
            # message for this judgment.
            return None
        tier = str(event.get("tier") or fields.get("tier") or "")
        if tier.casefold() == "silent":
            # Silent outcomes remain available in the room and telemetry HUD;
            # they do not page the operator through Telegram.
            return None
        return _format_outcome_lines(
            name=name,
            outcome_type=event.get("eventType", "") or "outcome",
            verdict=verdict,
            summary=str(event.get("summary", "") or ""),
            fields=raw_fields,
        )

    if event_type == "room_mesh_message":
        participant = event.get("participant", {}) or {}
        name = (
            participant.get("display_name")
            or participant.get("persona")
            or event.get("participantId")
            or "agent"
        )
        event_name = event.get("eventType", "") or "work"
        preview = event.get("preview", "")
        direction = event.get("direction", "") or "delegate"
        lines = [f"[{name}] {direction}: {event_name}"]
        if preview:
            lines.append(preview)
        return "\n".join(lines)

    if event_type == "error":
        error_content = event.get("content", event.get("error", "Unknown error"))
        if isinstance(error_content, dict):
            error_content = error_content.get("message", str(error_content))
        return f"[error] {error_content}"

    if event_type == "result":
        return None

    if event_type == "system":
        content = event.get("content", "")
        if content:
            return f"[system] {content}"
        return None

    return None


def split_message(text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> list[str]:
    """Split a long message into chunks respecting Telegram's limit.

    Splits at newline boundaries when possible, falling back to
    hard breaks at max_length.
    """
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        # Find last newline within the limit
        split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1:
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip("\n")

    return chunks


def _parse_markdown_table_row(line: str) -> list[str] | None:
    trimmed = line.strip()
    if "|" not in trimmed:
        return None
    if trimmed.startswith("|"):
        trimmed = trimmed[1:]
    if trimmed.endswith("|"):
        trimmed = trimmed[:-1]
    cells = [cell.strip() for cell in trimmed.split("|")]
    if len(cells) < 2 or any(cell == "" for cell in cells):
        return None
    return cells


def _is_markdown_table_divider(line: str) -> bool:
    cells = _parse_markdown_table_row(line)
    if not cells:
        return False
    return all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def _strip_markdown_inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return text


def _render_inline_telegram_html(text: str) -> str:
    parts: list[str] = []
    cursor = 0

    while cursor < len(text):
        if text.startswith("**", cursor):
            end = text.find("**", cursor + 2)
            if end != -1:
                parts.append(f"<b>{_render_inline_telegram_html(text[cursor + 2 : end])}</b>")
                cursor = end + 2
                continue

        if text[cursor] == "`":
            end = text.find("`", cursor + 1)
            if end != -1:
                code = html.escape(text[cursor + 1 : end])
                parts.append(f"<code>{code}</code>")
                cursor = end + 1
                continue

        if text[cursor] == "[":
            label_end = text.find("]", cursor + 1)
            if label_end != -1 and label_end + 1 < len(text) and text[label_end + 1] == "(":
                url_end = text.find(")", label_end + 2)
                if url_end != -1:
                    label = html.escape(text[cursor + 1 : label_end])
                    href = html.escape(text[label_end + 2 : url_end], quote=True)
                    parts.append(f'<a href="{href}">{label}</a>')
                    cursor = url_end + 1
                    continue

        next_positions = [
            pos
            for pos in (
                text.find("**", cursor),
                text.find("`", cursor),
                text.find("[", cursor),
            )
            if pos != -1
        ]
        next_token = min(next_positions) if next_positions else len(text)
        if next_token == cursor:
            parts.append(html.escape(text[cursor]))
            cursor += 1
            continue
        parts.append(html.escape(text[cursor:next_token]))
        cursor = next_token

    return "".join(parts)


def _render_telegram_table_block(lines: list[str]) -> str:
    rows = [_parse_markdown_table_row(line) for line in lines]
    parsed_rows = [row for row in rows if row]
    if not parsed_rows:
        return html.escape("\n".join(lines))

    plain_rows = [[_strip_markdown_inline(cell) for cell in row] for row in parsed_rows]
    column_count = max(len(row) for row in plain_rows)
    widths = [0] * column_count
    for row in plain_rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    rendered_rows: list[str] = []
    for row_index, row in enumerate(plain_rows):
        padded = [cell.ljust(widths[idx]) for idx, cell in enumerate(row)]
        rendered_rows.append(" | ".join(padded))
        if row_index == 0 and len(plain_rows) > 1:
            rendered_rows.append("-+-".join("-" * width for width in widths[: len(row)]))

    return f"<pre>{html.escape(chr(10).join(rendered_rows))}</pre>"


def render_telegram_html(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return ""

    rendered: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]

        if line.startswith(">! ") or line.startswith("> "):
            expandable = line.startswith(">! ")
            prefix = ">! " if expandable else "> "
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].startswith(prefix):
                quote_lines.append(
                    _render_inline_telegram_html(lines[index][len(prefix) :])
                )
                index += 1
            attribute = " expandable" if expandable else ""
            rendered.append(
                f"<blockquote{attribute}>{chr(10).join(quote_lines)}</blockquote>"
            )
            continue

        header_cells = _parse_markdown_table_row(line)
        divider_line = lines[index + 1] if index + 1 < len(lines) else None
        if header_cells and divider_line and _is_markdown_table_divider(divider_line):
            table_lines = [line]
            row_index = index + 2
            while row_index < len(lines):
                row = lines[row_index]
                parsed = _parse_markdown_table_row(row)
                if not parsed or _is_markdown_table_divider(row):
                    break
                table_lines.append(row)
                row_index += 1
            rendered.append(_render_telegram_table_block(table_lines))
            index = row_index
            continue

        heading_match = re.fullmatch(r"(#{1,6})\s+(.*)", line)
        if heading_match:
            rendered.append(f"<b>{_render_inline_telegram_html(heading_match.group(2))}</b>")
            index += 1
            continue

        unordered_match = re.fullmatch(r"\s*[-*+]\s+(.*)", line)
        if unordered_match:
            rendered.append(f"• {_render_inline_telegram_html(unordered_match.group(1))}")
            index += 1
            continue

        ordered_match = re.fullmatch(r"\s*(\d+)\.\s+(.*)", line)
        if ordered_match:
            rendered.append(
                f"{ordered_match.group(1)}. {_render_inline_telegram_html(ordered_match.group(2))}"
            )
            index += 1
            continue

        rendered.append(_render_inline_telegram_html(line))
        index += 1

    return "\n".join(rendered)


def telegram_parse_mode(event: dict) -> str | None:
    event_type = event.get("type", "")
    if event_type in {
        "room_message",
        "room_notification",
        "room_outcome",
        "room_mesh_message",
        "user_confirmed",
        "system",
    }:
        return "HTML"
    return None


def _telegram_should_send_event(event: dict) -> bool:
    """Return whether an event belongs on the human Telegram operator channel."""
    event_type = event.get("type", "")
    return event_type in {
        "room_message",
        "room_notification",
        "room_outcome",
        "room_mesh_message",
        "error",
    }


class TelegramChannel(MessageChannel):
    """Message channel that sends CLI events to a Telegram chat.

    Requires the `python-telegram-bot` package (>=21.0, async).
    When the package is not installed, instantiation raises RuntimeError.

    Args:
        bot_token: Telegram Bot API token.
        chat_id: Target chat ID to send messages to.
        notify_only: If True, only send outbound notifications (no inbound).
        on_message: Optional async callback for inbound Telegram messages.
    """

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        notify_only: bool = False,
        topic_mode: TelegramTopicMode = "topic_per_session",
        message_thread_id: int | None = None,
        topic_name: str | None = None,
        inbound_chat_ids: Iterable[str | int] | None = None,
        allow_any_inbound_chat: bool = False,
        on_message: object | None = None,
    ) -> None:
        if not HAS_TELEGRAM:
            raise RuntimeError(
                "python-telegram-bot is not installed. "
                "Install it with: pip install 'python-telegram-bot>=21.0'"
            )

        self._bot_token = bot_token
        self._chat_id = chat_id
        self._notify_only = notify_only
        self._topic_mode = topic_mode
        self._message_thread_id = message_thread_id
        self._inbound_chat_ids = {str(chat_id), *(str(item) for item in inbound_chat_ids or ())}
        self._allow_any_inbound_chat = allow_any_inbound_chat
        base_topic_name = (topic_name or "Volundr session").strip()
        self._topic_name = base_topic_name[:TELEGRAM_TOPIC_NAME_MAX_LENGTH]
        self._on_message = on_message
        self._bot: object | None = None
        self._application: object | None = None
        self._started = False
        self._closed = False
        self._text_buffer: list[str] = []
        self._flush_task: asyncio.Task | None = None
        self._last_send_results: list[dict[str, Any]] = []
        self._reply_targets: dict[int, dict[str, Any]] = {}
        self._delivered_event_ids: dict[str, None] = {}
        self._active_failure_keys: dict[str, None] = {}

    async def start(self) -> None:
        """Start the Telegram bot (initialize, but don't poll if notify_only)."""
        if self._started:
            return

        self._bot = Bot(token=self._bot_token)
        self._started = True
        logger.info(
            (
                "TelegramChannel started (chat_id=%s, notify_only=%s, "
                "topic_mode=%s, message_thread_id=%s)"
            ),
            self._chat_id,
            self._notify_only,
            self._topic_mode,
            self._message_thread_id,
        )

        await self._ensure_topic_target()

        if not self._notify_only:
            self._application = Application.builder().token(self._bot_token).build()

            # Register handlers
            self._application.add_handler(CommandHandler("status", self._cmd_status))
            self._application.add_handler(CommandHandler("interrupt", self._cmd_interrupt))
            self._application.add_handler(CommandHandler("model", self._cmd_model))
            self._application.add_handler(
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    self._handle_text_message,
                )
            )
            self._application.add_handler(CallbackQueryHandler(self._handle_callback_query))

            await self._application.initialize()
            await self._application.start()
            asyncio.create_task(self._application.updater.start_polling())

    async def send_event(self, event: dict) -> None:
        """Format and send a CLI event to the Telegram chat."""
        if self._closed or not self._started:
            return

        if not _telegram_should_send_event(event):
            return

        event_type = event.get("type", "")
        participant = event.get("participant")
        if not isinstance(participant, dict):
            participant = {}
        participant_id = str(
            event.get("participantId")
            or participant.get("peer_id")
            or participant.get("persona")
            or ""
        )
        if event_type == "room_outcome" and participant_id:
            prefix = f"{participant_id}:"
            self._active_failure_keys = {
                key: None for key in self._active_failure_keys if not key.startswith(prefix)
            }

        failure_kind = str(event.get("failureKind") or "").strip()
        failure_key = (
            f"{participant_id}:{failure_kind}"
            if event_type == "room_message"
            and event.get("error")
            and participant_id
            and failure_kind
            else ""
        )
        if failure_key and failure_key in self._active_failure_keys:
            get_observability().count(
                "skuld.telegram.messages",
                attributes={"direction": "outbound", "outcome": "coalesced_failure"},
            )
            return

        source_event_id = str(event.get("sourceEventId") or "").strip()
        if source_event_id and source_event_id in self._delivered_event_ids:
            get_observability().count(
                "skuld.telegram.messages",
                attributes={"direction": "outbound", "outcome": "duplicate"},
            )
            return

        text = format_telegram_event(event)
        if not text:
            return
        reply_context_text = text
        parse_mode = telegram_parse_mode(event)
        if parse_mode == "HTML":
            text = render_telegram_html(text)

        reply_markup = None
        if (
            event_type == "room_notification"
            and event.get("notificationType") == "help_needed"
            and ForceReply is not None
        ):
            reply_markup = ForceReply(
                selective=True,
                input_field_placeholder="Reply with the requested operator input",
            )

        # Buffer streaming text deltas and flush periodically
        if event_type == "content_block_delta":
            self._text_buffer.append(text)
            if not self._flush_task or self._flush_task.done():
                self._flush_task = asyncio.create_task(self._scheduled_flush())
            return

        # Non-delta event: flush buffer first, then send. Retain the Telegram
        # message id and its neutral room context so a reply returns to the exact
        # peer and the runtime can see which prior message the human answered.
        await self._flush_buffer()
        carrier = event.get("trace_context")
        if not isinstance(carrier, dict):
            carrier = {}
        telemetry = get_observability()
        with telemetry.span(
            "skuld.telegram.send",
            attributes={
                "skuld.channel": "telegram",
                "skuld.event.type": str(event_type),
                "skuld.help.peer_id": str(event.get("participantId") or ""),
            },
            carrier=carrier,
        ):
            await self._send_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
            if source_event_id and self._last_send_results:
                self._delivered_event_ids[source_event_id] = None
                while len(self._delivered_event_ids) > TELEGRAM_REPLY_CACHE_SIZE:
                    self._delivered_event_ids.pop(next(iter(self._delivered_event_ids)))
            if failure_key and self._last_send_results:
                self._active_failure_keys[failure_key] = None
                while len(self._active_failure_keys) > TELEGRAM_REPLY_CACHE_SIZE:
                    self._active_failure_keys.pop(next(iter(self._active_failure_keys)))
            self._remember_reply_targets(event, reply_context_text)
            telemetry.count(
                "skuld.telegram.messages",
                value=len(self._last_send_results),
                attributes={"direction": "outbound", "event_type": str(event_type)},
            )

    def _remember_reply_targets(self, event: dict[str, Any], rendered_text: str) -> None:
        """Correlate Telegram replies with the room message and peer they answer."""
        peer_id = str(event.get("participantId") or "").strip()
        if not peer_id:
            return
        trace_context = event.get("trace_context")
        if not isinstance(trace_context, dict):
            trace_context = {}
        reply_context = {
            "event_type": str(event.get("type") or "room_message"),
            "content": rendered_text[:TELEGRAM_MAX_MESSAGE_LENGTH],
            "participant_id": peer_id,
        }
        for result in self._last_send_results:
            message_id = result.get("message_id")
            if not isinstance(message_id, int):
                continue
            self._reply_targets[message_id] = {
                "target_peer_id": peer_id,
                "trace_context": dict(trace_context),
                "reply_context": dict(reply_context),
            }
        while len(self._reply_targets) > TELEGRAM_REPLY_CACHE_SIZE:
            self._reply_targets.pop(next(iter(self._reply_targets)))

    async def _scheduled_flush(self) -> None:
        """Wait then flush the text buffer."""
        await asyncio.sleep(TELEGRAM_BUFFER_FLUSH_INTERVAL)
        await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Send accumulated text buffer to Telegram."""
        if not self._text_buffer:
            return

        combined = "".join(self._text_buffer)
        self._text_buffer.clear()

        if self._flush_task and not self._flush_task.done():
            self._flush_task.cancel()
            self._flush_task = None

        if combined.strip():
            await self._send_text(combined)

    async def _send_text(
        self,
        text: str,
        *,
        parse_mode: str | None = None,
        reply_markup: object | None = None,
    ) -> None:
        """Send text to the Telegram chat, splitting if too long."""
        if not self._bot:
            return

        chunks = split_message(text)
        self._last_send_results = []
        for index, chunk in enumerate(chunks):
            try:
                kwargs = {
                    "chat_id": self._chat_id,
                    "text": chunk,
                }
                if parse_mode:
                    kwargs["parse_mode"] = parse_mode
                if self._message_thread_id is not None:
                    kwargs["message_thread_id"] = self._message_thread_id
                if reply_markup is not None and index == 0:
                    kwargs["reply_markup"] = reply_markup
                sent = await self._bot.send_message(**kwargs)
                result = {
                    "chat_id": str(getattr(getattr(sent, "chat", None), "id", self._chat_id)),
                    "message_id": getattr(sent, "message_id", None),
                    "date": (
                        sent.date.isoformat()
                        if isinstance(getattr(sent, "date", None), datetime)
                        else str(getattr(sent, "date", "") or "")
                    ),
                }
                self._last_send_results.append(result)
                logger.info(
                    "Telegram message sent chat_id=%s message_id=%s",
                    result["chat_id"],
                    result["message_id"],
                )
            except Exception:
                logger.warning(
                    "Failed to send Telegram message to chat %s",
                    self._chat_id,
                    exc_info=True,
                )

    @property
    def last_send_results(self) -> list[dict[str, Any]]:
        """Metadata returned by the most recent Telegram send operation."""
        return list(self._last_send_results)

    async def send_permission_request(
        self,
        request_id: str,
        tool_name: str,
        tool_input: dict,
    ) -> None:
        """Send a permission request with inline keyboard buttons."""
        if not self._bot or not HAS_TELEGRAM:
            return

        detail = ""
        if "command" in tool_input:
            detail = tool_input["command"]
        elif "file_path" in tool_input:
            detail = tool_input["file_path"]

        text = f"[permission] {tool_name}"
        if detail:
            text += f": {detail}"

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Allow",
                        callback_data=f"perm:allow:{request_id}",
                    ),
                    InlineKeyboardButton(
                        "Deny",
                        callback_data=f"perm:deny:{request_id}",
                    ),
                ]
            ]
        )
        try:
            kwargs = {
                "chat_id": self._chat_id,
                "text": text,
                "reply_markup": keyboard,
            }
            if self._message_thread_id is not None:
                kwargs["message_thread_id"] = self._message_thread_id
            await self._bot.send_message(**kwargs)
        except Exception:
            logger.warning("Failed to send permission request to Telegram", exc_info=True)

    async def _ensure_topic_target(self) -> None:
        """Resolve the effective Telegram topic target for this session."""
        if not self._bot:
            return

        if self._topic_mode == "shared_chat":
            return

        if self._topic_mode == "fixed_topic":
            if self._message_thread_id is None:
                logger.warning(
                    "Telegram fixed_topic mode selected without message_thread_id; "
                    "falling back to shared chat"
                )
                self._topic_mode = "shared_chat"
            return

        if self._topic_mode != "topic_per_session":
            logger.warning(
                "Unknown Telegram topic mode %r; falling back to shared chat",
                self._topic_mode,
            )
            self._topic_mode = "shared_chat"
            return

        if self._message_thread_id is not None:
            return

        try:
            topic = await self._bot.create_forum_topic(
                chat_id=self._chat_id,
                name=self._topic_name or "Volundr session",
            )
            thread_id = getattr(topic, "message_thread_id", None)
            if thread_id is None:
                logger.warning(
                    "Telegram topic creation returned no message_thread_id; "
                    "falling back to shared chat"
                )
                self._topic_mode = "shared_chat"
                return
            self._message_thread_id = int(thread_id)
            logger.info(
                (
                    "Telegram topic created for session "
                    "(chat_id=%s, message_thread_id=%s, topic_name=%s)"
                ),
                self._chat_id,
                self._message_thread_id,
                self._topic_name,
            )
        except Exception:
            logger.warning(
                "Failed to create Telegram session topic; falling back to shared chat",
                exc_info=True,
            )
            self._topic_mode = "shared_chat"

    async def close(self) -> None:
        """Stop the Telegram bot and clean up."""
        if self._closed:
            return
        self._closed = True

        await self._flush_buffer()

        if self._application and hasattr(self._application, "stop"):
            try:
                if hasattr(self._application, "updater") and self._application.updater:
                    await self._application.updater.stop()
                await self._application.stop()
                await self._application.shutdown()
            except Exception:
                logger.warning("Error stopping Telegram application", exc_info=True)

        self._bot = None
        self._application = None
        self._started = False
        logger.info("TelegramChannel closed")

    @property
    def channel_type(self) -> str:
        return "telegram"

    @property
    def is_open(self) -> bool:
        return self._started and not self._closed

    def communication_route(self) -> dict[str, Any]:
        """Return the effective external route for this Telegram channel."""
        return {
            "platform": "telegram",
            "conversation_id": self._chat_id,
            "thread_id": (
                str(self._message_thread_id) if self._message_thread_id is not None else None
            ),
            "mode": "room",
            "metadata": {
                "notify_only": self._notify_only,
                "topic_mode": self._topic_mode,
                "topic_name": self._topic_name,
            },
        }

    # --- Bot command handlers ---

    async def _cmd_status(self, update: object, context: object) -> None:
        """Handle /status command."""
        if not self._validate_chat(update):
            return
        # Status info is injected by the broker via a callback
        await update.message.reply_text("[status] Session active")

    async def _cmd_interrupt(self, update: object, context: object) -> None:
        """Handle /interrupt command."""
        if not self._validate_chat(update):
            return
        if self._on_message:
            await self._on_message({"type": "interrupt"})
        await update.message.reply_text("[interrupt] Interrupt signal sent")

    async def _cmd_model(self, update: object, context: object) -> None:
        """Handle /model <name> command."""
        if not self._validate_chat(update):
            return
        text = update.message.text or ""
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await update.message.reply_text("Usage: /model <model_name>")
            return
        model_name = parts[1].strip()
        if self._on_message:
            await self._on_message({"type": "set_model", "model": model_name})
        await update.message.reply_text(f"[model] Switching to {model_name}")

    async def _handle_text_message(self, update: object, context: object) -> None:
        """Handle incoming text messages (dispatch to broker)."""
        if not self._validate_chat(update):
            return
        text = update.message.text or ""
        if not text:
            return
        if self._on_message:
            message = update.message
            payload: dict[str, Any] = {
                "type": "message",
                "content": text,
                "source": "telegram",
            }
            message_id = getattr(message, "message_id", None)
            if isinstance(message_id, int):
                payload["message_id"] = message_id
            date = getattr(message, "date", None)
            if isinstance(date, datetime):
                payload["date"] = date.isoformat() if hasattr(date, "isoformat") else str(date)
            chat = getattr(update, "effective_chat", None)
            chat_id = getattr(chat, "id", None)
            if isinstance(chat_id, str | int):
                payload["chat_id"] = str(chat_id)
            thread_id = getattr(message, "message_thread_id", None)
            if isinstance(thread_id, int):
                payload["message_thread_id"] = thread_id
            reply_to = getattr(message, "reply_to_message", None)
            reply_to_message_id = getattr(reply_to, "message_id", None)
            if isinstance(reply_to_message_id, int):
                payload["reply_to_message_id"] = reply_to_message_id
                target = self._reply_targets.get(reply_to_message_id)
                if target is not None:
                    payload.update(target)
            carrier = payload.get("trace_context")
            if not isinstance(carrier, dict):
                carrier = {}
            telemetry = get_observability()
            with telemetry.span(
                "skuld.telegram.reply.receive",
                attributes={
                    "skuld.channel": "telegram",
                    "skuld.help.peer_id": str(payload.get("target_peer_id") or ""),
                    "skuld.telegram.reply_to_message_id": reply_to_message_id or 0,
                },
                carrier=carrier,
            ):
                payload["trace_context"] = telemetry.inject() or carrier
                await self._on_message(payload)
                telemetry.count(
                    "skuld.telegram.messages",
                    attributes={"direction": "inbound", "event_type": "message"},
                )

    async def _handle_callback_query(self, update: object, context: object) -> None:
        """Handle inline keyboard button presses (permission responses)."""
        query = update.callback_query
        if not query:
            return

        data = query.data or ""
        if not data.startswith("perm:"):
            return

        parts = data.split(":", 2)
        if len(parts) < 3:
            return

        action = parts[1]  # "allow" or "deny"
        request_id = parts[2]

        behavior = "allowOnce" if action == "allow" else "deny"
        if self._on_message:
            await self._on_message(
                {
                    "type": "permission_response",
                    "request_id": request_id,
                    "behavior": behavior,
                }
            )

        await query.answer(f"Permission {action}ed")
        await query.edit_message_text(f"[permission] {action}ed (request {request_id[:8]})")

    def _validate_chat(self, update: object) -> bool:
        """Check that the message comes from the authorized chat."""
        if not hasattr(update, "effective_chat"):
            return False
        chat = update.effective_chat
        if not chat:
            return False
        if self._allow_any_inbound_chat:
            return True
        return str(chat.id) in self._inbound_chat_ids


# ---------------------------------------------------------------------------
# ChannelRegistry — manages active channels
# ---------------------------------------------------------------------------


class ChannelRegistry:
    """Thread-safe registry of active message channels.

    The broker uses this to track all connected channels and broadcast
    events to them. Channels that fail to receive events are automatically
    removed.
    """

    def __init__(self) -> None:
        self._channels: list[MessageChannel] = []

    def add(self, channel: MessageChannel) -> None:
        """Register a channel."""
        self._channels.append(channel)
        logger.info(
            "Channel added: type=%s, total=%d",
            channel.channel_type,
            len(self._channels),
        )

    def remove(self, channel: MessageChannel) -> None:
        """Unregister a channel."""
        try:
            self._channels.remove(channel)
        except ValueError:
            pass  # Expected: channel may have already been removed
        logger.info(
            "Channel removed: type=%s, total=%d",
            channel.channel_type,
            len(self._channels),
        )

    async def broadcast(self, event: dict) -> None:
        """Send an event to all registered channels.

        Channels that raise exceptions during send are automatically
        removed from the registry.
        """
        failed: list[MessageChannel] = []

        for channel in list(self._channels):
            if not channel.is_open:
                failed.append(channel)
                continue
            try:
                await channel.send_event(event)
            except Exception:
                logger.warning(
                    "Channel send failed, removing: type=%s",
                    channel.channel_type,
                    exc_info=True,
                )
                failed.append(channel)

        for ch in failed:
            self.remove(ch)

    async def close_all(self) -> None:
        """Close and remove all channels."""
        for channel in list(self._channels):
            try:
                await channel.close()
            except Exception:
                logger.debug(
                    "Error closing channel during close_all: type=%s",
                    channel.channel_type,
                    exc_info=True,
                )
        self._channels.clear()

    @property
    def count(self) -> int:
        """Number of registered channels."""
        return len(self._channels)

    @property
    def channels(self) -> list[MessageChannel]:
        """List of registered channels (copy)."""
        return list(self._channels)

    def by_type(self, channel_type: str) -> list[MessageChannel]:
        """Return channels filtered by type."""
        return [c for c in self._channels if c.channel_type == channel_type]
