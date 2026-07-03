"""Map Claude Code JSONL session events to chronicle timeline events.

Reads structured JSONL lines written by Claude Code to disk and extracts
timeline-reportable events (file changes, git commits, terminal commands,
token usage).  Reuses classification logic from the broker's SessionArtifacts
but operates on the JSONL on-disk format rather than the SDK WebSocket stream.

DISPOSITION (SRD FR-10 — see ``docs/forge-chronicle-vs-event-log.md``):
the output of this mapper is a DERIVED, NON-AUTHORITATIVE, Claude-CLI-only UI
timeline aggregate, NOT a source of truth. The single source of truth for the
transcript is ``session_event_log`` (folded by the shared reducer).

Because the patterns here key on Anthropic on-disk JSONL shapes, this mapper
emits nothing for non-Claude transports (Codex / Grok / OpenCode), so the
chronicle is LEGITIMATELY EMPTY for those — that is expected, not a defect.
The mapping is intentionally lossy (a curated UI subset) and may drift from
the canonical log. Consumers MUST NOT treat the chronicle as the transcript.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("skuld.event_mapper")

# ---------------------------------------------------------------------------
# Git helpers (same patterns as broker.py)
# ---------------------------------------------------------------------------

_GIT_COMMIT_PREFIXES = ("git commit", "git -c ", "git -C ")
_GIT_COMMIT_OUTPUT_RE = re.compile(r"\[[\w/-]+\s+([a-f0-9]{7,})\]\s+(.+)")


def _is_git_commit(cmd: str) -> bool:
    stripped = cmd.lstrip()
    if stripped.startswith(_GIT_COMMIT_PREFIXES):
        return True
    return "git commit" in stripped


def _extract_git_commit_info(output: str) -> tuple[str, str] | None:
    match = _GIT_COMMIT_OUTPUT_RE.search(output)
    if not match:
        return None
    return match.group(1), match.group(2)


# ---------------------------------------------------------------------------
# EventMapper
# ---------------------------------------------------------------------------


@dataclass
class EventMapper:
    """Stateful mapper: JSONL events → chronicle timeline events.

    Maintains file-knowledge state and buffers pending tool_use events so
    they can be enriched when the corresponding tool_result arrives.
    """

    _known_files: set[str] = field(default_factory=set)
    _pending: dict[str, dict] = field(default_factory=dict)
    _start_ts: datetime | None = field(default=None, repr=False)

    # ---- public API -------------------------------------------------------

    def map_event(self, line: dict) -> list[dict]:
        """Convert a single JSONL line into 0+ timeline event dicts.

        Each returned dict is ready to POST to the chronicle timeline API::

            {"t": int, "type": str, "label": str, ...optional fields}
        """
        ev_type = line.get("type")

        if ev_type == "file-history-snapshot":
            self._ingest_snapshot(line)
            return []

        if ev_type not in ("assistant", "user"):
            return []

        msg = line.get("message") or {}
        content = msg.get("content") or []
        if not isinstance(content, list):
            return []

        ts = self._parse_timestamp(line)
        t = self._elapsed(ts)

        # --- tool results (enrich pending events) --------------------------
        # Tool results arrive as "user" messages in the JSONL format
        result_events = self._try_enrich_results(content, t)

        # --- tool_use blocks (only in assistant messages) ------------------
        tool_events: list[dict] = []
        if ev_type == "assistant":
            tool_events = self._extract_tool_events(content, t)

        # --- message-level token usage (only in assistant messages) --------
        token_event: dict | None = None
        if ev_type == "assistant":
            token_event = self._extract_token_event(msg, t)

        events: list[dict] = []
        events.extend(result_events)
        events.extend(tool_events)
        if token_event:
            events.append(token_event)
        return events

    # ---- internal helpers -------------------------------------------------

    def _ingest_snapshot(self, line: dict) -> None:
        """Learn about known files from a file-history-snapshot."""
        snapshot = line.get("snapshot") or {}
        backups = snapshot.get("trackedFileBackups") or {}
        for path in backups:
            self._known_files.add(path)

    def _extract_tool_events(self, content: list, t: int) -> list[dict]:
        events: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue

            tool_name = block.get("name", "")
            tool_input = block.get("input") or {}
            tool_use_id = block.get("id", "")

            ev = self._classify_tool(tool_name, tool_input)
            if ev is None:
                continue

            ev["t"] = t

            if tool_use_id:
                # Buffer for result enrichment
                self._pending[tool_use_id] = ev
            else:
                # No id to correlate — emit immediately
                events.append(ev)

        return events

    def _try_enrich_results(self, content: list, t: int) -> list[dict]:
        """Match tool_result blocks to pending tool_use events and emit."""
        if not self._pending:
            return []

        enriched: list[dict] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue

            use_id = block.get("tool_use_id", "")
            if not use_id or use_id not in self._pending:
                continue

            ev = self._pending.pop(use_id)
            result_text = self._result_text(block)

            if ev.get("type") == "git" and ev.pop("_pending_git", False):
                info = _extract_git_commit_info(result_text)
                if info:
                    ev["hash"] = info[0]
                    ev["label"] = info[1]

            if ev.get("type") == "terminal":
                ev["exit"] = self._extract_exit_code(block)

            enriched.append(ev)

        return enriched

    def _classify_tool(self, tool_name: str, tool_input: dict) -> dict | None:
        file_path = tool_input.get("file_path") or tool_input.get("path")

        if tool_name in ("Edit", "Write", "NotebookEdit"):
            if tool_name == "Edit":
                action = "modified"
                if file_path:
                    self._known_files.add(file_path)
            elif file_path and file_path in self._known_files:
                action = "modified"
            elif file_path:
                action = "created"
                self._known_files.add(file_path)
            else:
                action = "created"
            return {"type": "file", "label": file_path or tool_name, "action": action}

        if tool_name == "Read":
            if file_path:
                self._known_files.add(file_path)
            return None

        if tool_name != "Bash":
            return None

        cmd = tool_input.get("command", "")
        if _is_git_commit(cmd):
            return {"type": "git", "label": cmd[:80] or "git commit", "_pending_git": True}

        return {"type": "terminal", "label": cmd[:80] or "bash"}

    def _extract_token_event(self, msg: dict, t: int) -> dict | None:
        """Emit a message event with total token count on completed turns."""
        if msg.get("stop_reason") is None:
            return None

        usage = msg.get("usage") or {}
        tokens = (
            usage.get("input_tokens", 0)
            + usage.get("output_tokens", 0)
            + usage.get("cache_read_input_tokens", 0)
            + usage.get("cache_creation_input_tokens", 0)
        )
        if tokens <= 0:
            return None

        # First non-empty text line as label
        label = ""
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    label = text.split("\n", 1)[0][:80]
                    break
        if not label:
            label = f"Turn (stop: {msg['stop_reason']})"

        return {"t": t, "type": "message", "label": label, "tokens": tokens}

    # ---- timestamp helpers ------------------------------------------------

    def _parse_timestamp(self, line: dict) -> datetime:
        ts_str = line.get("timestamp", "")
        if not ts_str:
            return datetime.min
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return datetime.min

    def _elapsed(self, ts: datetime) -> int:
        if ts == datetime.min:
            return 0
        if self._start_ts is None:
            self._start_ts = ts
            return 0
        delta = (ts - self._start_ts).total_seconds()
        return max(0, int(delta))

    # ---- result helpers ---------------------------------------------------

    @staticmethod
    def _result_text(block: dict) -> str:
        content = block.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        return ""

    @staticmethod
    def _extract_exit_code(block: dict) -> int:
        if "exit_code" in block:
            return block["exit_code"]
        if block.get("is_error"):
            return 1
        return 0
