"""Claude Code session provider — scans ~/.claude/projects for sessions.

Claude Code stores one JSONL transcript per session under
``~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl``. Each record
carries the session id, working directory, and timestamps, so sessions
can be discovered and later resumed with ``claude --resume <id>``.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from volundr.domain.models import ExternalSessionRecord
from volundr.domain.ports import ExternalSessionProvider

logger = logging.getLogger(__name__)

DEFAULT_PROJECTS_DIR = "~/.claude/projects"
DEFAULT_LIVE_THRESHOLD_SECONDS = 120
DEFAULT_HEAD_LINES = 50
DEFAULT_MAX_SESSIONS = 200
TITLE_MAX_CHARS = 120


def _parse_timestamp(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _message_text(message: object) -> str:
    """Extract plain text from a Claude message payload."""
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    return text
    return ""


def _clean_title(text: str) -> str:
    title = " ".join(text.split())
    if len(title) > TITLE_MAX_CHARS:
        return title[: TITLE_MAX_CHARS - 1] + "…"
    return title


class ClaudeCodeSessionProvider(ExternalSessionProvider):
    """Discovers Claude Code sessions on the local host."""

    def __init__(
        self,
        *,
        projects_dir: str = DEFAULT_PROJECTS_DIR,
        live_threshold_seconds: int = DEFAULT_LIVE_THRESHOLD_SECONDS,
        head_lines: int = DEFAULT_HEAD_LINES,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        **_extra: object,
    ):
        self._projects_dir = Path(str(projects_dir)).expanduser()
        self._live_threshold_seconds = int(live_threshold_seconds)
        self._head_lines = int(head_lines)
        self._max_sessions = int(max_sessions)

    @property
    def name(self) -> str:
        return "claude-code"

    @property
    def harness(self) -> str:
        return "claude"

    async def list_sessions(self) -> list[ExternalSessionRecord]:
        return await asyncio.to_thread(self._scan)

    async def get_session(self, external_id: str) -> ExternalSessionRecord | None:
        return await asyncio.to_thread(self._find_one, external_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _scan(self) -> list[ExternalSessionRecord]:
        candidates = self._session_files()
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        records: list[ExternalSessionRecord] = []
        for path in candidates[: self._max_sessions]:
            record = self._parse_session_file(path)
            if record is not None:
                records.append(record)
        return records

    def _find_one(self, external_id: str) -> ExternalSessionRecord | None:
        try:
            UUID(external_id)
        except ValueError:
            return None
        for path in self._session_files():
            if path.stem == external_id:
                return self._parse_session_file(path)
        return None

    def _session_files(self) -> list[Path]:
        if not self._projects_dir.is_dir():
            return []
        files: list[Path] = []
        for project_dir in self._projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            for path in project_dir.glob("*.jsonl"):
                try:
                    UUID(path.stem)
                except ValueError:
                    continue
                files.append(path)
        return files

    def _parse_session_file(self, path: Path) -> ExternalSessionRecord | None:
        cwd = ""
        title = ""
        model = ""
        created_at: datetime | None = None

        try:
            with path.open(encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if index >= self._head_lines:
                        break
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    if record.get("isSidechain"):
                        continue
                    if not cwd and isinstance(record.get("cwd"), str):
                        cwd = record["cwd"]
                    if created_at is None:
                        created_at = _parse_timestamp(record.get("timestamp"))
                    if not title and record.get("type") == "user":
                        title = _clean_title(_message_text(record.get("message")))
                    if not model and record.get("type") == "assistant":
                        message = record.get("message")
                        if isinstance(message, dict) and isinstance(message.get("model"), str):
                            model = message["model"]
                    if cwd and title and model and created_at is not None:
                        break
        except OSError:
            logger.warning("Failed to read Claude session file %s", path, exc_info=True)
            return None

        stat = path.stat()
        updated_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        age_seconds = (datetime.now(UTC) - updated_at).total_seconds()

        return ExternalSessionRecord(
            provider=self.name,
            harness=self.harness,
            external_id=path.stem,
            workspace_path=cwd,
            title=title,
            model=model,
            created_at=created_at,
            updated_at=updated_at,
            live=age_seconds <= self._live_threshold_seconds,
            workspace_exists=bool(cwd) and Path(cwd).is_dir(),
        )
