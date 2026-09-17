"""Per-subagent token accounting, read incrementally from Claude's subagent JSONL.

A Claude ``SubagentStart`` hook hands us the subagent's ``transcript_path`` — an
append-only JSONL whose assistant rows carry ``message.usage``. This tracker tails
each registered transcript from a stored byte offset, so a poll costs O(new bytes)
rather than O(file), and reports a cumulative token total per agent id.

Two properties of the real files drive the design (both validated against live
transcripts under ``~/.claude/projects/*/*/subagents/``):

1. **Streaming re-emits the same message.** A single assistant message is written
   several times as it streams, each row carrying the usage *so far* for that
   message (``output_tokens`` 1 → 236 …) with identical input/cache numbers.
   Naively summing every usage row roughly DOUBLES the true total (measured
   305,415,363 vs 153,968,131 on a real 1,693-row transcript). So rows are folded
   by ``message.id``: the largest total seen for an id wins.
2. **Rows for one message id are contiguous.** Verified across sample transcripts
   (zero non-contiguous reappearances), so folding needs only the *current* id in
   memory — the running total is ``committed + pending``, O(1) per agent.

Token components follow the existing convention in ``event_mapper._extract_token_event``:
``input + output + cache_read + cache_creation``.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# A workflow subagent's transcript lives at
# ``…/subagents/workflows/wf_<id>/agent-<agent_id>.jsonl``; a plain subagent at
# ``…/subagents/agent-<agent_id>.jsonl``. The parent directory name IS the workflow id.
_WORKFLOWS_DIR = "workflows"
_WORKFLOW_PREFIX = "wf_"

# Bound the registry so a very long session can never grow it without limit.
_DEFAULT_MAX_AGENTS = 256


def workflow_id_from_path(transcript_path: str | Path | None) -> str | None:
    """``wf_<id>`` when the transcript sits under a ``workflows/wf_<id>/`` directory."""
    if not transcript_path:
        return None
    parts = Path(transcript_path).parts
    for parent, child in zip(parts, parts[1:], strict=False):
        if parent == _WORKFLOWS_DIR and child.startswith(_WORKFLOW_PREFIX):
            return child
    return None


def tokens_in_usage(usage: object) -> int:
    """Sum the four token components of one ``usage`` block (event_mapper convention)."""
    if not isinstance(usage, dict):
        return 0
    total = 0
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ):
        value = usage.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        if value > 0:
            total += value
    return total


def _usage_of_row(row: object) -> tuple[str | None, int] | None:
    """``(message_id, tokens)`` for a JSONL row that carries usage, else None."""
    if not isinstance(row, dict):
        return None
    message = row.get("message")
    if isinstance(message, dict) and isinstance(message.get("usage"), dict):
        return (
            message.get("id") if isinstance(message.get("id"), str) else None,
            tokens_in_usage(message["usage"]),
        )
    if isinstance(row.get("usage"), dict):
        row_id = row.get("id")
        return (row_id if isinstance(row_id, str) else None, tokens_in_usage(row["usage"]))
    return None


@dataclass
class _AgentUsage:
    """Tail state for one agent's transcript."""

    path: Path
    workflow: str | None = None
    offset: int = 0
    committed_tokens: int = 0
    pending_tokens: int = 0
    last_message_id: str | None = None
    saw_usage: bool = field(default=False)

    @property
    def total(self) -> int:
        return self.committed_tokens + self.pending_tokens

    def fold(self, message_id: str | None, tokens: int) -> None:
        """Fold one usage row in, collapsing streaming re-emissions of one message."""
        if message_id and message_id == self.last_message_id:
            # Same message, streamed again — keep the largest total seen for it.
            self.pending_tokens = max(self.pending_tokens, tokens)
        else:
            self.committed_tokens += self.pending_tokens
            self.pending_tokens = tokens
            self.last_message_id = message_id
        self.saw_usage = True


class AgentUsageTracker:
    """Cumulative token totals per subagent, tailed from each agent's transcript JSONL.

    ``tokens_for`` returns ``None`` — never ``0`` — while nothing is known yet (agent
    never registered, transcript not written yet, no usage row seen). Callers use that
    to omit the key entirely rather than publish a misleading zero.
    """

    def __init__(self, *, max_agents: int = _DEFAULT_MAX_AGENTS) -> None:
        self._agents: OrderedDict[str, _AgentUsage] = OrderedDict()
        self._max_agents = max(1, max_agents)

    # ──────────────────────────── registry ────────────────────────────

    def register(self, agent_id: str, transcript_path: str | Path) -> None:
        """Start tracking ``agent_id``'s transcript. Re-registering the same path is a no-op."""
        agent_id = str(agent_id or "").strip()
        if not agent_id or not transcript_path:
            return
        path = Path(transcript_path)
        existing = self._agents.get(agent_id)
        if existing is not None and existing.path == path:
            self._agents.move_to_end(agent_id)
            return
        self._agents[agent_id] = _AgentUsage(path=path, workflow=workflow_id_from_path(path))
        self._agents.move_to_end(agent_id)
        while len(self._agents) > self._max_agents:
            self._agents.popitem(last=False)

    def release(self, agent_id: str) -> None:
        """Drop an agent's tail state (called once its final total has been frozen)."""
        self._agents.pop(str(agent_id or ""), None)

    def transcript_path_for(self, agent_id: str) -> str | None:
        state = self._agents.get(str(agent_id or ""))
        return str(state.path) if state is not None else None

    def workflow_for(self, agent_id: str) -> str | None:
        """``wf_<id>`` when this agent's transcript lives under a workflow directory."""
        state = self._agents.get(str(agent_id or ""))
        return state.workflow if state is not None else None

    # ──────────────────────────── reading ────────────────────────────

    def tokens_for(self, agent_id: str) -> int | None:
        """Cumulative tokens for ``agent_id``, or None when nothing is known yet."""
        state = self._agents.get(str(agent_id or ""))
        if state is None:
            return None
        self._consume_new_bytes(state)
        return state.total if state.saw_usage else None

    def _consume_new_bytes(self, state: _AgentUsage) -> None:
        """Read whatever was appended since the last poll; never re-read old bytes."""
        try:
            with state.path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                if size < state.offset:
                    # File shrank (rotated/rewritten) — restart the tail from the top.
                    state.offset = 0
                if size == state.offset:
                    return
                handle.seek(state.offset)
                chunk = handle.read(size - state.offset)
        except FileNotFoundError:
            return
        except OSError as exc:  # unreadable/permission/IO — stay silent-but-logged, retry next poll
            logger.debug("agent usage: cannot read %s: %s", state.path, exc)
            return

        # A partial trailing line means the writer is mid-append: stop before it and
        # pick it up on the next poll (the files are append-only, so it will be intact).
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return
        state.offset += cut + 1
        for raw in chunk[: cut + 1].splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue  # malformed/truncated row — tolerate and keep going
            found = _usage_of_row(row)
            if found is None:
                continue
            message_id, tokens = found
            if tokens <= 0 and not message_id:
                continue
            state.fold(message_id, tokens)
