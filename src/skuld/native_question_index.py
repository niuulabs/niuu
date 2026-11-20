"""What a Claude CLI session file says about the questions it asked (LEX-1979).

A question can stay open in Forge after the CLI has moved on: an auto-compaction drops
its tool call, or the session restarts while its menu is up, so no tool_result ever
reaches the conversation. The CLI's own session file (``<native_id>.jsonl``) is the
truth. This index reads it once, then only the bytes appended since, and keeps just
the AskUserQuestion calls and their results, so a check every sweep stays cheap on a
session file of hundreds of megabytes.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from stat import S_ISREG
from typing import Any

#: The tool whose calls are questions to the person.
QUESTION_TOOL = "AskUserQuestion"

#: What the session file says about one question's tool call.
ANSWERED = "answered"  # its tool_result is in the file
UNANSWERED = "unanswered"  # its tool call is in the file, with no result yet
NEVER_SHOWN = "never_shown"  # its tool call is not in the file at all


class NativeTranscriptUnavailableError(RuntimeError):
    """The session file cannot be read; no question may be closed on its evidence."""


def claude_project_dir(workspace_dir: str, home: Path | None = None) -> Path:
    """``~/.claude/projects/<slug>``: Claude names it after the workspace path."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", workspace_dir)
    return (home or Path.home()) / ".claude" / "projects" / slug


@dataclass
class QuestionResult:
    content: Any
    is_error: bool


@dataclass
class NativeQuestionIndex:
    """An incremental index of one session file's questions and their results."""

    path: Path
    native_session_id: str
    _offset: int = 0
    _device: int | None = None
    _inode: int | None = None
    _partial: bytes = b""
    _questions: set[str] = field(default_factory=set)
    _results: dict[str, QuestionResult] = field(default_factory=dict)
    # One refresh at a time: a concurrent one must never read a half-indexed file.
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def refresh(self) -> None:
        """Read what was appended since the last refresh (all of it, the first time).

        Streams line by line, so a first read of a large file holds one line at a time.
        A replaced file (another inode) or a truncated one is read again from the
        start. Raises :class:`NativeTranscriptUnavailableError` when it cannot be read.
        """
        with self._lock:
            self._refresh_locked()

    def _refresh_locked(self) -> None:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        except OSError as exc:
            raise NativeTranscriptUnavailableError(
                f"Claude session file {self.path} cannot be opened: {exc}"
            ) from exc
        with os.fdopen(fd, "rb") as stream:
            stat = os.fstat(stream.fileno())
            if not S_ISREG(stat.st_mode):
                raise NativeTranscriptUnavailableError(f"{self.path} is not a regular file")
            if (stat.st_dev, stat.st_ino) != (
                self._device,
                self._inode,
            ) or stat.st_size < self._offset:
                self._offset, self._partial = 0, b""
                self._questions.clear()
                self._results.clear()
                self._device, self._inode = stat.st_dev, stat.st_ino
            stream.seek(self._offset)
            end = stat.st_size
            while self._offset < end:
                line = stream.readline(end - self._offset)
                if not line:
                    break
                self._offset += len(line)
                if not line.endswith(b"\n"):
                    # A writer may still be appending this line: finish it next time.
                    self._partial += line
                    break
                self._index_line(self._partial + line)
                self._partial = b""

    def _index_line(self, line: bytes) -> None:
        if b"tool_" not in line:
            return
        try:
            record = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            return
        if not isinstance(record, dict) or record.get("isSidechain"):
            return
        if record.get("sessionId", record.get("session_id")) != self.native_session_id:
            return
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == QUESTION_TOOL:
                if isinstance(block.get("id"), str):
                    self._questions.add(block["id"])
            elif block.get("type") == "tool_result":
                tool_use_id = block.get("tool_use_id")
                if isinstance(tool_use_id, str) and tool_use_id in self._questions:
                    self._results[tool_use_id] = QuestionResult(
                        content=block.get("content"), is_error=bool(block.get("is_error"))
                    )

    def state(self, tool_use_id: str) -> str:
        if tool_use_id in self._results:
            return ANSWERED
        if tool_use_id in self._questions:
            return UNANSWERED
        return NEVER_SHOWN

    def result(self, tool_use_id: str) -> QuestionResult | None:
        return self._results.get(tool_use_id)
