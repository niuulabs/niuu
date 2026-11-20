"""Work facts: the one meaning of "working" (G10, control-plane design §V9.2).

A session is working only while rows that prove turn work keep arriving. This module
defines which durable log rows count, and folds them into two facts:

* ``last_work_at``: the NEWEST source timestamp of a qualifying row (not the last row's:
  a late upload can carry an older producer time than a row stored before it);
* ``tool_open``: a ``PreToolUse`` without its ``PostToolUse``/``PostToolUseFailure``.
  A ``result`` or ``Stop`` closes every open tool.

Forge keeps these at log ingest and serves them on the session (F12d), Skuld uses the
same predicate for its own self-heal (F12b), and the control plane's reference
(``derived_state.py``) and the ForgeKit port are bound by the same goldens.

Excluded on purpose: the ``system`` and ``capabilities`` rows every reconnect writes,
the ``user_*`` delivery states, terminal frames and snapshots, ``Notification`` hooks,
compaction, and hooks from Claude Code's own side queries (LEX-2026): prompt
suggestions run after a turn with every tool denied, so their ``PreToolUse`` never gets
a ``PostToolUse`` and would hold a tool open until the next turn ends. They carry an
``agent_id`` with no ``agent_type``; a real subagent names its type. The vendored
control-plane goldens carry no agent fields, so they are unaffected.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

WORK_KINDS = frozenset(
    {
        "assistant",
        "result",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
    }
)
HOOK_KIND = "claude_hook"
WORK_HOOKS = frozenset(
    {
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PostToolBatch",
        "MessageDisplay",
        "Stop",
        "SubagentStart",
        "SubagentStop",
    }
)
TOOL_OPEN_HOOK = "PreToolUse"
TOOL_CLOSE_HOOKS = frozenset({"PostToolUse", "PostToolUseFailure"})
TURN_END_HOOK = "Stop"
SUBAGENT_START_HOOK = "SubagentStart"
TURN_END_KIND = "result"


def hook_name(payload: Mapping[str, Any] | None) -> str:
    """The Claude hook event name carried by a ``claude_hook`` frame ("" if none).

    Reads exactly what Forge's ingest SQL reads: the frame's ``hook_event_name``, else
    the raw hook payload's.
    """
    if not isinstance(payload, Mapping):
        return ""
    name = payload.get("hook_event_name")
    if name:
        return str(name)
    inner = payload.get("payload")
    if isinstance(inner, Mapping):
        return str(inner.get("hook_event_name") or "")
    return ""


def is_side_query_hook(payload: Mapping[str, Any] | None) -> bool:
    """A ``claude_hook`` frame from a Claude Code side query (LEX-2025/2026).

    Its raw hook names an ``agent_id`` and no ``agent_type`` and is not a SubagentStart.
    Forge's ingest SQL applies the same test (``SIDE_QUERY_HOOK_SQL`` there).
    """
    if not isinstance(payload, Mapping):
        return False
    inner = payload.get("payload")
    if not isinstance(inner, Mapping):
        return False
    agent_id, agent_type = inner.get("agent_id"), inner.get("agent_type")
    if not (isinstance(agent_id, str) and agent_id):
        return False
    typed = isinstance(agent_type, str) and bool(agent_type)
    return not typed and hook_name(payload) != SUBAGENT_START_HOOK


def is_work_row(kind: str, payload: Mapping[str, Any] | None) -> bool:
    """True when a durable row of ``kind`` proves the agent is doing turn work."""
    if kind in WORK_KINDS:
        return True
    return (
        kind == HOOK_KIND and hook_name(payload) in WORK_HOOKS and not is_side_query_hook(payload)
    )


@dataclass(frozen=True)
class WorkFacts:
    """Folded work facts for one session."""

    last_work_at: datetime | None = None
    open_tools: int = 0

    @property
    def tool_open(self) -> bool:
        return self.open_tools > 0

    def fold(self, kind: str, payload: Mapping[str, Any] | None, ts: datetime) -> WorkFacts:
        """Fold one row (in seq order). Non-work rows leave the facts unchanged."""
        if not is_work_row(kind, payload):
            return self
        last = ts if self.last_work_at is None or ts > self.last_work_at else self.last_work_at
        hook = hook_name(payload) if kind == HOOK_KIND else ""
        open_tools = self.open_tools
        if hook == TOOL_OPEN_HOOK:
            open_tools += 1
        elif hook in TOOL_CLOSE_HOOKS and open_tools:
            open_tools -= 1
        elif kind == TURN_END_KIND or hook == TURN_END_HOOK:
            open_tools = 0
        return WorkFacts(last_work_at=last, open_tools=open_tools)


def fold_rows(
    rows: Iterable[tuple[str, Mapping[str, Any] | None, datetime]],
    start: WorkFacts | None = None,
) -> WorkFacts:
    """Fold ``(kind, payload, ts)`` rows in seq order."""
    facts = start or WorkFacts()
    for kind, payload, ts in rows:
        facts = facts.fold(kind, payload, ts)
    return facts
